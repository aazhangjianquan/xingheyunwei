"""Private one-time reminders, sharing files only with their named recipients."""
import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import uuid

from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import attachments, settings
from .attachment_storage import MinioDocuments, upload_backend
from .database import get_db
from .models import (NotificationLog, PersonalReminder, PersonalReminderAttachment,
                     PersonalReminderRecipient, User, now)

BEIJING = timezone(timedelta(hours=8))
MAX_ATTACHMENTS = 10


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value):
    return utc(value).isoformat() if value else None


class ReminderIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=4000)
    remind_at: datetime
    recipient_ids: list[int] = Field(min_length=1, max_length=100)

    @field_validator('remind_at')
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError('提醒时间必须包含时区')
        return value.astimezone(timezone.utc)


def owner_item(db, reminder_id, user, *, lock=False):
    query = select(PersonalReminder).where(PersonalReminder.id == reminder_id,
        PersonalReminder.owner_id == user.id, PersonalReminder.deleted_at.is_(None))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    item = db.scalar(query)
    if not item:
        raise HTTPException(404, '提醒不存在或无权查看')
    return item


def recipients(db, item):
    return list(db.scalars(select(PersonalReminderRecipient).where(
        PersonalReminderRecipient.reminder_id == item.id).order_by(PersonalReminderRecipient.user_id)))


def check_people(db, ids):
    people = list(db.scalars(select(User).where(User.id.in_(set(ids))).order_by(User.id)))
    if len(people) != len(set(ids)) or any(not p.active or not (p.feishu_open_id or '').strip() for p in people):
        raise HTTPException(422, '只能选择已启用且已配置飞书 ID 的提醒对象，请刷新人员列表')
    return people


def check_time(value):
    if utc(value) <= now():
        raise HTTPException(422, '请选择未来的提醒时间（北京时间）')


def attachment_info(row, name):
    data = attachments.attachment_out(row, name)
    if row.extension in attachments.IMAGE_CONTENT_TYPES:
        data['preview_type'] = 'image'
    return data


def detail(db, item):
    rows = recipients(db, item)
    names = {p.id: p for p in db.scalars(select(User).where(User.id.in_([r.user_id for r in rows])))}
    logs = {log.id: log for log in db.scalars(select(NotificationLog).where(
        NotificationLog.id.in_([r.notification_id for r in rows if r.notification_id])))}
    owner_name = db.scalar(select(User.display_name).where(User.id == item.owner_id))
    files = list(db.scalars(select(PersonalReminderAttachment).where(
        PersonalReminderAttachment.reminder_id == item.id).order_by(PersonalReminderAttachment.id)))
    return {'id': item.id, 'content': item.content, 'remind_at': iso(item.remind_at), 'state': item.state,
        'created_at': iso(item.created_at), 'can_upload': item.state == 'draft',
        'attachments': [attachment_info(row, owner_name) for row in files],
        'recipients': [{'id': row.user_id, 'name': names[row.user_id].display_name,
            'configured': bool(names[row.user_id].active and (names[row.user_id].feishu_open_id or '').strip()),
            'status': logs[row.notification_id].status if row.notification_id in logs else 'scheduled',
            'error': logs[row.notification_id].error if row.notification_id in logs else '',
            'sent_at': iso(logs[row.notification_id].sent_at) if row.notification_id in logs else None,
            'can_retry': item.state == 'dispatched' and row.notification_id in logs and (
                logs[row.notification_id].status in {'failed', 'unconfigured'})}
            for row in rows]}


def lock_notification_context(db, log_id):
    identity = db.execute(select(NotificationLog.object_id, NotificationLog.recipient_id).where(
        NotificationLog.id == log_id, NotificationLog.object_type == 'personal_reminder')).first()
    if not identity:
        return None
    # 和取消、删除、调度采用相同锁序：提醒 -> 收件关系 -> 通知，防止撤销后仍发送。
    item = db.scalar(select(PersonalReminder).where(PersonalReminder.id == identity.object_id)
        .with_for_update().execution_options(populate_existing=True))
    recipient = db.scalar(select(PersonalReminderRecipient).where(
        PersonalReminderRecipient.reminder_id == identity.object_id,
        PersonalReminderRecipient.user_id == identity.recipient_id,
        PersonalReminderRecipient.notification_id == log_id).with_for_update())
    return item, recipient


def invalid_reason(db, context):
    if context is None:
        return None
    item, recipient = context
    if not item or item.deleted_at or item.state != 'dispatched' or not recipient:
        return 'cancelled', '个人提醒已取消、删除或收件关系已变更'
    if utc(item.remind_at) > now():
        return 'cancelled', '尚未到达提醒时间'
    # MySQL 身份查询可能已建立旧快照；当前读按 ID 顺序加锁，并保留引用供投递复用。
    people = {p.id: p for p in db.scalars(select(User).where(User.id.in_({item.owner_id, recipient.user_id}))
        .order_by(User.id).with_for_update().execution_options(populate_existing=True))}
    db.info['personal_reminder_people'] = people
    owner = people.get(item.owner_id)
    if not owner or not owner.active:
        return 'cancelled', '提醒创建人的账号已停用'
    return None


def schedule_due(db, item, stamp):
    if item.deleted_at or item.state != 'scheduled' or utc(item.remind_at) > utc(stamp):
        return 0
    owner = db.get(User, item.owner_id)
    if not owner or not owner.active:
        return 0
    link = f"{settings.value(db, 'public_base_url').rstrip('/')}/personal-reminders/{item.id}/files"
    text = (f"【{settings.value(db, 'site_name')}】个人提醒\n来自：{owner.display_name}\n"
            f"提醒时间：{utc(item.remind_at).astimezone(BEIJING):%Y-%m-%d %H:%M}（北京时间）\n"
            f"{item.content}\n附件（需登录）：{link}")
    count = 0
    for recipient in recipients(db, item):
        if recipient.notification_id is None:
            log = NotificationLog(recipient_id=recipient.user_id, event='personal_reminder_due',
                object_type='personal_reminder', object_id=item.id, message=text)
            db.add(log); db.flush()
            recipient.notification_id = log.id
            count += 1
    item.state = 'dispatched'
    db.flush()
    return count


def scan_due(sessions, stamp, *, preview=False):
    counts = {'scanned': 0, 'notifications': 0, 'errors': 0}
    cursor = 0
    while True:
        with sessions() as db:
            ids = list(db.scalars(select(PersonalReminder.id).where(
                PersonalReminder.id > cursor, PersonalReminder.state == 'scheduled',
                PersonalReminder.deleted_at.is_(None), PersonalReminder.remind_at <= stamp)
                .order_by(PersonalReminder.id).limit(200)))
        if not ids:
            break
        for reminder_id in ids:
            try:
                with sessions() as db:
                    item = db.scalar(select(PersonalReminder).where(PersonalReminder.id == reminder_id).with_for_update())
                    if item:
                        counts['notifications'] += schedule_due(db, item, stamp)
                        counts['scanned'] += 1
                    db.rollback() if preview else db.commit()
            except Exception:
                import logging
                logging.getLogger(__name__).exception('个人提醒调度失败 id=%s', reminder_id)
                counts['errors'] += 1
        cursor = ids[-1]
    return counts


def stop(db, item):
    for log in db.scalars(select(NotificationLog).where(NotificationLog.object_type == 'personal_reminder',
            NotificationLog.object_id == item.id, NotificationLog.status != 'sent').with_for_update()):
        log.status, log.error = 'cancelled', '创建人已取消或删除个人提醒'
        log.dispatch_ready, log.next_attempt_at = False, None
    item.state = 'cancelled'


def shared_item(db, reminder_id, user):
    item = db.get(PersonalReminder, reminder_id)
    if not item or item.deleted_at:
        raise HTTPException(404, '附件不存在或无权查看')
    if item.owner_id == user.id:
        return item
    recipient = db.get(PersonalReminderRecipient, (item.id, user.id))
    if not recipient or item.state != 'dispatched' or utc(item.remind_at) > now():
        raise HTTPException(404, '附件不存在或无权查看')
    return item


def register_routes(app, current_user):
    @app.get('/api/personal-reminders/people')
    def people(db=Depends(get_db), user=Depends(current_user)):
        # 仅公开选择器需要的姓名和部门；不返回明文或脱敏飞书 ID。
        return [{'id': p.id, 'name': p.display_name, 'department': p.department, 'is_self': p.id == user.id}
            for p in db.scalars(select(User).where(User.active.is_(True), User.feishu_open_id.is_not(None)).order_by(User.display_name, User.id))
            if (p.feishu_open_id or '').strip()]

    @app.get('/api/personal-reminders')
    def list_reminders(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=50),
                       db=Depends(get_db), user=Depends(current_user)):
        query = select(PersonalReminder).where(PersonalReminder.owner_id == user.id, PersonalReminder.deleted_at.is_(None))
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        pages = max(1, (total + page_size - 1) // page_size)
        current = min(page, pages)
        rows = db.scalars(query.order_by(PersonalReminder.id.desc()).offset((current - 1) * page_size).limit(page_size))
        return {'items': [{'id': r.id, 'content': r.content, 'remind_at': iso(r.remind_at), 'state': r.state} for r in rows],
                'page': current, 'total': total, 'total_pages': pages}

    @app.post('/api/personal-reminders', status_code=201)
    def create(data: ReminderIn, db=Depends(get_db), user=Depends(current_user)):
        check_time(data.remind_at)
        check_people(db, data.recipient_ids)
        item = PersonalReminder(owner_id=user.id, content=data.content, remind_at=data.remind_at, state='draft')
        db.add(item); db.flush()
        for uid in sorted(set(data.recipient_ids)):
            db.add(PersonalReminderRecipient(reminder_id=item.id, user_id=uid))
        db.commit()
        return detail(db, item)

    @app.get('/api/personal-reminders/{reminder_id}')
    def read(reminder_id: int, db=Depends(get_db), user=Depends(current_user)):
        return detail(db, owner_item(db, reminder_id, user))

    @app.post('/api/personal-reminders/{reminder_id}/activate')
    def activate(reminder_id: int, db=Depends(get_db), user=Depends(current_user)):
        db.rollback()
        item = owner_item(db, reminder_id, user, lock=True)
        if item.state != 'draft':
            raise HTTPException(409, '仅草稿可以启用，请刷新提醒列表')
        check_time(item.remind_at)
        check_people(db, [r.user_id for r in recipients(db, item)])
        item.state = 'scheduled'; db.commit()
        return detail(db, item)

    @app.put('/api/personal-reminders/{reminder_id}')
    def update_draft(reminder_id: int, data: ReminderIn, db=Depends(get_db), user=Depends(current_user)):
        db.rollback()
        item = owner_item(db, reminder_id, user, lock=True)
        if item.state != 'draft':
            raise HTTPException(409, '只能编辑尚未启用的草稿')
        check_time(data.remind_at); check_people(db, data.recipient_ids)
        for recipient in recipients(db, item):
            db.delete(recipient)
        db.flush()
        for uid in sorted(set(data.recipient_ids)):
            db.add(PersonalReminderRecipient(reminder_id=item.id, user_id=uid))
        item.content, item.remind_at = data.content, data.remind_at
        db.commit()
        return detail(db, item)

    @app.post('/api/personal-reminders/{reminder_id}/cancel')
    def cancel(reminder_id: int, db=Depends(get_db), user=Depends(current_user)):
        db.rollback()
        item = owner_item(db, reminder_id, user, lock=True)
        stop(db, item); db.commit()
        return detail(db, item)

    @app.delete('/api/personal-reminders/{reminder_id}')
    def delete(reminder_id: int, db=Depends(get_db), user=Depends(current_user)):
        db.rollback()
        item = owner_item(db, reminder_id, user, lock=True)
        stop(db, item); item.deleted_at = now(); db.commit()
        return {'message': '提醒已删除，尚未发送的通知已停止'}

    @app.post('/api/personal-reminders/{reminder_id}/retry/{recipient_id}')
    def retry(reminder_id: int, recipient_id: int, tasks: BackgroundTasks,
              db=Depends(get_db), user=Depends(current_user)):
        db.rollback()
        item = owner_item(db, reminder_id, user, lock=True)
        recipient = db.get(PersonalReminderRecipient, (item.id, recipient_id))
        if not recipient or not recipient.notification_id or invalid_reason(db, (item, recipient)):
            raise HTTPException(409, '此提醒已停止或尚未触发，无法重试')
        check_people(db, [recipient_id])
        log = db.scalar(select(NotificationLog).where(NotificationLog.id == recipient.notification_id).with_for_update())
        if log.status not in {'failed', 'unconfigured'}:
            raise HTTPException(409, '仅失败或待配置通知可重试')
        log.status, log.error, log.dispatch_ready = 'pending', '', True
        log.attempt_count, log.next_attempt_at = 0, None
        db.commit()
        from .feishu import send_notification
        tasks.add_task(send_notification, log.id)
        return detail(db, item)

    @app.get('/api/personal-reminders/{reminder_id}/attachments')
    def files(reminder_id: int, db=Depends(get_db), user=Depends(current_user)):
        item = shared_item(db, reminder_id, user)
        owner_name = db.scalar(select(User.display_name).where(User.id == item.owner_id))
        rows = db.scalars(select(PersonalReminderAttachment).where(PersonalReminderAttachment.reminder_id == item.id).order_by(PersonalReminderAttachment.id))
        return {'items': [attachment_info(r, owner_name) for r in rows], 'owner_name': owner_name,
                'can_upload': item.owner_id == user.id and item.state == 'draft',
                'max_size': attachments.MAX_FILE_SIZE, 'extensions': list(attachments.CHANGE_CONTENT_TYPES)}

    def save_file(db, reminder_id, user_id, filename, extension, temp_path, size, digest):
        db.rollback()
        user = db.get(User, user_id, populate_existing=True)
        if not user or not user.active:
            raise HTTPException(401, '账号已停用，请重新登录')
        item = owner_item(db, reminder_id, user, lock=True)
        if item.state != 'draft':
            raise HTTPException(409, '仅草稿可添加附件')
        count = db.scalar(select(func.count()).select_from(PersonalReminderAttachment).where(PersonalReminderAttachment.reminder_id == item.id))
        if count >= MAX_ATTACHMENTS:
            raise HTTPException(422, '每条提醒最多添加 10 个附件')
        attachments.validate_document(temp_path, extension)
        key = uuid.uuid4().hex + extension
        backend = upload_backend()
        store = MinioDocuments() if backend == 'minio' else None
        target = attachments.storage_path(key) if backend == 'local' else None
        stored = False
        try:
            if store:
                store.put(key, temp_path, attachments.CHANGE_CONTENT_TYPES[extension], digest)
            else:
                os.replace(temp_path, target); target.chmod(0o600)
            stored = True
            row = PersonalReminderAttachment(reminder_id=item.id, uploaded_by=user.id, filename=filename,
                extension=extension, content_type=attachments.CHANGE_CONTENT_TYPES[extension], storage_key=key,
                size=size, sha256=digest, storage_backend=backend, storage_bucket=store.bucket if store else '')
            db.add(row); db.flush()
            result = attachment_info(row, user.display_name); db.commit()
            return result
        except BaseException:
            db.rollback()
            if stored and store: store.remove(key)
            elif target: target.unlink(missing_ok=True)
            raise

    @app.post('/api/personal-reminders/{reminder_id}/attachments', status_code=201)
    async def upload(reminder_id: int, request: Request, filename: str = Query(min_length=1, max_length=1000),
                     db=Depends(get_db), user=Depends(current_user)):
        item = await run_in_threadpool(owner_item, db, reminder_id, user)
        if item.state != 'draft':
            raise HTTPException(409, '仅草稿可添加附件')
        uid = user.id
        name, extension = attachments.normalize_filename(filename, attachments.CHANGE_CONTENT_TYPES)
        length = request.headers.get('content-length', '')
        if length.isdigit() and int(length) > attachments.MAX_FILE_SIZE:
            raise HTTPException(413, '单个附件不能超过 20 MB')
        fd, temporary = tempfile.mkstemp(prefix='personal-reminder-upload-')
        path = Path(temporary)
        size, digest = 0, hashlib.sha256()
        try:
            with os.fdopen(fd, 'wb') as file:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > attachments.MAX_FILE_SIZE:
                        raise HTTPException(413, '单个附件不能超过 20 MB')
                    digest.update(chunk)
                    await run_in_threadpool(file.write, chunk)
            if not size: raise HTTPException(400, '不能上传空文件')
            return await run_in_threadpool(save_file, db, reminder_id, uid, name, extension, path, size, digest.hexdigest())
        finally:
            path.unlink(missing_ok=True)

    def file_item(db, reminder_id, attachment_id, user):
        shared_item(db, reminder_id, user)
        row = db.scalar(select(PersonalReminderAttachment).where(PersonalReminderAttachment.id == attachment_id,
            PersonalReminderAttachment.reminder_id == reminder_id))
        if not row: raise HTTPException(404, '附件不存在')
        return row

    @app.get('/api/personal-reminders/{reminder_id}/attachments/{attachment_id}/download')
    def download(reminder_id: int, attachment_id: int, db=Depends(get_db), user=Depends(current_user)):
        row = file_item(db, reminder_id, attachment_id, user)
        path, temporary = attachments.prepare_original(row)
        return FileResponse(path, filename=row.filename, media_type=row.content_type, headers=attachments.HEADERS,
            background=BackgroundTask(temporary.cleanup) if temporary else None)

    @app.get('/api/personal-reminders/{reminder_id}/attachments/{attachment_id}/preview')
    def preview(reminder_id: int, attachment_id: int, db=Depends(get_db), user=Depends(current_user)):
        row = file_item(db, reminder_id, attachment_id, user)
        image = row.extension in attachments.IMAGE_CONTENT_TYPES
        path, temporary = attachments.prepare_original(row) if image else attachments.prepare_preview(row)
        try:
            if row.extension in attachments.TEXT_EXTENSIONS:
                response = Response(attachments.decode_text(path.read_bytes()), media_type='text/plain; charset=utf-8', headers=attachments.HEADERS)
                if temporary: temporary.cleanup()
                return response
            return FileResponse(path, filename=row.filename if image else Path(row.filename).stem + '.pdf',
                media_type=row.content_type if image else 'application/pdf', content_disposition_type='inline',
                headers=attachments.HEADERS, background=BackgroundTask(temporary.cleanup) if temporary else None)
        except BaseException:
            if temporary: temporary.cleanup()
            raise
