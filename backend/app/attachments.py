"""Private delivery documents. No uploaded content is served from public URLs."""
import codecs
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import uuid
import zipfile

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.background import BackgroundTask

from . import audit
from .attachment_storage import MinioDocuments, upload_backend, validate_key
from .config import INSTANCE_DIR
from .database import get_db
from .models import ChangeAttachment, ChangeRequest, DeliveryAttachment, DeliveryProject, User

MAX_FILE_SIZE = 20 * 1024 * 1024
IMAGE_CONTENT_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}
MAX_ARCHIVE_SIZE = 150 * 1024 * 1024
TEXT_EXTENSIONS = {'.txt', '.csv', '.md'}
CONTENT_TYPES = {
    '.pdf': 'application/pdf', '.txt': 'text/plain', '.csv': 'text/csv', '.md': 'text/markdown',
    '.doc': 'application/msword', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt': 'application/vnd.ms-powerpoint', '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.rtf': 'application/rtf',
}
CHANGE_CONTENT_TYPES = {**CONTENT_TYPES, **IMAGE_CONTENT_TYPES}
HEADERS = {'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'}
_conversion_slots = threading.BoundedSemaphore(2)


def storage_root():
    root = INSTANCE_DIR / 'delivery-attachments'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def storage_path(key):
    validate_key(key)
    root = storage_root().resolve()
    path = root / key
    if path.is_symlink() or path.resolve().parent != root:
        raise HTTPException(500, '附件存储记录异常，请联系管理员')
    return path


def prepare_original(row):
    """Return a private local working file and its optional lifetime owner."""
    if row.storage_backend == 'local':
        path = storage_path(row.storage_key)
        if not path.is_file():
            raise HTTPException(404, '附件文件暂不可用，请联系管理员检查附件存储或备份')
        return path, None
    if row.storage_backend != 'minio':
        raise HTTPException(500, '附件存储记录异常，请联系管理员')
    temporary = tempfile.TemporaryDirectory(prefix='delivery-document-')
    try:
        path = Path(temporary.name) / validate_key(row.storage_key)
        MinioDocuments(row.storage_bucket).fetch(row.storage_key, path, MAX_FILE_SIZE, size=row.size, digest=row.sha256)
        return path, temporary
    except BaseException:
        temporary.cleanup()
        raise


def prepare_preview(row):
    if row.storage_backend == 'local':
        source, _ = prepare_original(row)
        if row.extension in TEXT_EXTENSIONS or row.extension == '.pdf':
            return source, None
        previews = storage_root() / 'previews'
        previews.mkdir(exist_ok=True, mode=0o700)
        cached = previews / (validate_key(row.storage_key) + '.pdf')
        if not cached.is_file():
            convert_to_pdf(source, cached)
        return cached, None
    if row.extension in TEXT_EXTENSIONS or row.extension == '.pdf':
        return prepare_original(row)
    if row.storage_backend != 'minio':
        raise HTTPException(500, '附件存储记录异常，请联系管理员')
    store = MinioDocuments(row.storage_bucket)
    temporary = tempfile.TemporaryDirectory(prefix='delivery-preview-')
    try:
        folder = Path(temporary.name)
        result = folder / 'preview.pdf'
        if not store.fetch(row.storage_key, result, MAX_ARCHIVE_SIZE, preview=True, missing_ok=True):
            source = folder / validate_key(row.storage_key)
            store.fetch(row.storage_key, source, MAX_FILE_SIZE, size=row.size, digest=row.sha256)
            convert_to_pdf(source, result)
            store.put(row.storage_key, result, 'application/pdf', hashlib.sha256(result.read_bytes()).hexdigest(), preview=True)
        with result.open('rb') as file:
            if file.read(5) != b'%PDF-':
                raise HTTPException(502, '文档预览缓存异常，请联系管理员检查 MinIO 存储')
        return result, temporary
    except BaseException:
        temporary.cleanup()
        raise


def normalize_filename(value, allowed_extensions=None):
    value = value.replace('\\', '/').rsplit('/', 1)[-1]
    value = ''.join(c for c in value if c.isprintable()).strip()
    extension = Path(value).suffix.lower()
    allowed_extensions = allowed_extensions or CONTENT_TYPES
    if not value or len(value) > 240 or extension not in allowed_extensions:
        raise HTTPException(400, '请选择支持的 Office、PDF、文本或图片附件，文件名最多 240 个字符')
    return value, extension


def decode_text(data):
    encodings = ['utf-16'] if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else ['utf-8-sig', 'gb18030']
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            if any(ord(c) < 32 and c not in '\n\r\t\f' for c in text):
                continue
            return text
        except UnicodeError:
            continue
    raise HTTPException(400, '无法识别文本编码，请将文档另存为 UTF-8、UTF-16 或 GB18030 后重试')


def validate_document(path, extension):
    with path.open('rb') as file:
        header = file.read(1024)
    valid = True
    if extension == '.pdf':
        valid = header.startswith(b'%PDF-')
    elif extension in {'.doc', '.xls', '.ppt'}:
        valid = header.startswith(bytes.fromhex('d0cf11e0a1b11ae1'))
    elif extension == '.rtf':
        valid = header.lstrip().startswith(b'{\\rtf')
    elif extension in IMAGE_CONTENT_TYPES:
        signatures = {'.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8\xff', '.jpeg': b'\xff\xd8\xff', '.gif': (b'GIF87a', b'GIF89a'), '.webp': b'RIFF'}
        signature = signatures[extension]
        valid = any(header.startswith(item) for item in signature) if isinstance(signature, tuple) else header.startswith(signature)
        if extension == '.webp': valid = valid and header[8:12] == b'WEBP'
    elif extension in TEXT_EXTENSIONS:
        decode_text(path.read_bytes())
    else:
        required = {'.docx': 'word/document.xml', '.xlsx': 'xl/workbook.xml', '.pptx': 'ppt/presentation.xml'}[extension]
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                names = {entry.filename for entry in entries}
                valid = (required in names and '[Content_Types].xml' in names and len(entries) <= 5000
                         and sum(entry.file_size for entry in entries) <= MAX_ARCHIVE_SIZE
                         and not any(entry.flag_bits & 1 or entry.filename.lower().endswith('vbaproject.bin') for entry in entries))
        except (zipfile.BadZipFile, OSError):
            valid = False
    if not valid:
        raise HTTPException(400, '文件内容与类型不符，或文档已损坏、加密或包含宏，请另存为普通文档后上传')


def attachment_out(row, uploader_name):
    return {'id': row.id, 'filename': row.filename, 'extension': row.extension, 'size': row.size,
            'uploaded_by': row.uploaded_by, 'uploader_name': uploader_name,
            'created_at': row.created_at.isoformat(), 'preview_type': 'text' if row.extension in TEXT_EXTENSIONS else 'pdf'}


def convert_to_pdf(source, destination):
    if not _conversion_slots.acquire(blocking=False):
        raise HTTPException(503, '文档预览服务繁忙，请稍后重试或先下载原文件')
    try:
        _convert_to_pdf(source, destination)
    finally:
        _conversion_slots.release()


def _convert_to_pdf(source, destination):
    binary = os.getenv('SOFFICE_BIN') or shutil.which('libreoffice') or shutil.which('soffice')
    if not binary:
        raise HTTPException(503, '文档预览服务尚未安装，请更新包含 LibreOffice 的 API 镜像；原文件仍可下载')
    with tempfile.TemporaryDirectory(prefix='document-preview-') as directory:
        folder = Path(directory)
        profile = folder / 'profile'
        (profile / 'user').mkdir(parents=True)
        (profile / 'user/registrymodifications.xcu').write_text(
            '<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
            '</oor:items>', encoding='utf-8')
        local_source = folder / ('document' + source.suffix)
        shutil.copyfile(source, local_source)
        try:
            result = subprocess.run([binary, '-env:UserInstallation=' + profile.as_uri(), '--headless', '--nologo',
                '--nodefault', '--nofirststartwizard', '--convert-to', 'pdf', '--outdir', str(folder), str(local_source)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
        except subprocess.TimeoutExpired:
            raise HTTPException(504, '文档预览转换超时，请稍后重试或下载原文件查看')
        except OSError:
            raise HTTPException(503, '文档预览服务暂不可用，请稍后重试或下载原文件查看')
        output = folder / 'document.pdf'
        if result.returncode or not output.is_file() or output.stat().st_size > MAX_ARCHIVE_SIZE:
            raise HTTPException(422, '此文档无法转换预览，可能已损坏或受密码保护，请下载原文件查看')
        with output.open('rb') as file:
            if not file.read(5) == b'%PDF-':
                raise HTTPException(422, '文档转换结果无效，请下载原文件查看')
        # Only complete results become visible, including with concurrent readers.
        temporary = destination.with_name(uuid.uuid4().hex + '.tmp')
        try:
            shutil.copyfile(output, temporary)
            temporary.chmod(0o600)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def register_routes(app, current_user, get_visible):
    def visible_change(db, item_id, user):
        return get_visible(db, ChangeRequest, item_id, user)

    def change_attachment_out(row, uploader_name):
        return {'id': row.id, 'filename': row.filename, 'extension': row.extension, 'size': row.size,
                'uploaded_by': row.uploaded_by, 'uploader_name': uploader_name,
                'created_at': row.created_at.isoformat(),
                'preview_type': 'text' if row.extension in TEXT_EXTENSIONS else 'image' if row.extension in IMAGE_CONTENT_TYPES else 'pdf'}

    def get_change_attachment(db, item_id, attachment_id, user):
        item = visible_change(db, item_id, user)
        row = db.scalar(select(ChangeAttachment).where(ChangeAttachment.id == attachment_id, ChangeAttachment.change_id == item.id))
        if not row:
            raise HTTPException(404, '附件不存在或不属于此变更单')
        return item, row

    @app.get('/api/changes/{item_id}/attachments')
    def list_change_attachments(item_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        item = visible_change(db, item_id, user)
        rows = db.execute(select(ChangeAttachment, User.display_name).join(User, User.id == ChangeAttachment.uploaded_by)
            .where(ChangeAttachment.change_id == item.id).order_by(ChangeAttachment.created_at.desc(), ChangeAttachment.id.desc())).all()
        return {'items': [change_attachment_out(row, name) for row, name in rows], 'can_upload': item.owner_id == user.id,
                'max_size': MAX_FILE_SIZE, 'extensions': list(CHANGE_CONTENT_TYPES)}

    def save_change_attachment(db, item_id, user_id, filename, extension, temp_path, size, digest):
        db.rollback()
        item = db.scalar(select(ChangeRequest).where(ChangeRequest.id == item_id, ChangeRequest.deleted_at.is_(None)).with_for_update())
        user = db.scalar(select(User).where(User.id == user_id).with_for_update())
        if not user or not user.active:
            raise HTTPException(401, '此账号已停用，请联系管理员启用后再登录')
        if not item:
            raise HTTPException(404, '变更单不存在或已删除')
        if item.owner_id != user.id:
            raise HTTPException(403, '仅变更单创建人可以上传附件')
        validate_document(temp_path, extension)
        key = uuid.uuid4().hex + extension
        backend = upload_backend()
        store = MinioDocuments() if backend == 'minio' else None
        target = storage_path(key) if backend == 'local' else None
        stored = False
        try:
            if store: store.put(key, temp_path, CHANGE_CONTENT_TYPES[extension], digest)
            else:
                os.replace(temp_path, target)
                target.chmod(0o600)
            stored = True
            row = ChangeAttachment(change_id=item.id, uploaded_by=user.id, filename=filename, extension=extension,
                content_type=CHANGE_CONTENT_TYPES[extension], storage_key=key, size=size, sha256=digest,
                storage_backend=backend, storage_bucket=store.bucket if store else '')
            db.add(row); db.flush()
            audit.record(db, user, 'upload_attachment', 'change', item, after={},
                         extra={'attachment': (None, filename), 'attachment_size': (None, size)})
            result = change_attachment_out(row, user.display_name)
            db.commit()
            return result
        except BaseException:
            db.rollback()
            if store and stored: store.remove(key)
            elif target: target.unlink(missing_ok=True)
            raise

    @app.post('/api/changes/{item_id}/attachments', status_code=201)
    async def upload_change_attachment(item_id: int, request: Request, filename: str = Query(min_length=1, max_length=1000),
                                      db: Session = Depends(get_db), user=Depends(current_user)):
        await run_in_threadpool(visible_change, db, item_id, user)
        owner_id = user.id
        name, extension = normalize_filename(filename, CHANGE_CONTENT_TYPES)
        length = request.headers.get('content-length', '')
        if length.isdigit() and int(length) > MAX_FILE_SIZE:
            raise HTTPException(413, '单个附件不能超过 20 MB')
        fd, temporary = tempfile.mkstemp(prefix='change-upload-')
        path = Path(temporary)
        size, digest = 0, hashlib.sha256()
        try:
            with os.fdopen(fd, 'wb') as file:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_FILE_SIZE: raise HTTPException(413, '单个附件不能超过 20 MB')
                    digest.update(chunk)
                    await run_in_threadpool(file.write, chunk)
            if not size: raise HTTPException(400, '不能上传空文件')
            return await run_in_threadpool(save_change_attachment, db, item_id, owner_id, name, extension, path, size, digest.hexdigest())
        finally:
            path.unlink(missing_ok=True)

    @app.get('/api/changes/{item_id}/attachments/{attachment_id}/download')
    def download_change_attachment(item_id: int, attachment_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        item, row = get_change_attachment(db, item_id, attachment_id, user)
        path, temporary = prepare_original(row)
        try:
            audit.record(db, user, 'download_attachment', 'change', item, after={}, extra={'attachment': (None, row.filename)})
            db.commit()
            return FileResponse(path, filename=row.filename, media_type=row.content_type, headers=HEADERS,
                                background=BackgroundTask(temporary.cleanup) if temporary else None)
        except BaseException:
            if temporary: temporary.cleanup()
            raise

    @app.get('/api/changes/{item_id}/attachments/{attachment_id}/preview')
    def preview_change_attachment(item_id: int, attachment_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        item, row = get_change_attachment(db, item_id, attachment_id, user)
        if row.extension in IMAGE_CONTENT_TYPES:
            path, temporary = prepare_original(row)
            try:
                response = FileResponse(path, media_type=row.content_type, headers=HEADERS,
                    background=BackgroundTask(temporary.cleanup) if temporary else None)
                audit.record(db, user, 'preview_attachment', 'change', item, after={}, extra={'attachment': (None, row.filename)})
                db.commit()
                return response
            except BaseException:
                if temporary: temporary.cleanup()
                raise
        path, temporary = prepare_preview(row)
        try:
            if row.extension in TEXT_EXTENSIONS:
                response = Response(decode_text(path.read_bytes()), media_type='text/plain; charset=utf-8', headers=HEADERS)
                if temporary: temporary.cleanup()
            else:
                response = FileResponse(path, filename=Path(row.filename).stem + '.pdf', content_disposition_type='inline',
                    media_type='application/pdf', headers=HEADERS, background=BackgroundTask(temporary.cleanup) if temporary else None)
            audit.record(db, user, 'preview_attachment', 'change', item, after={}, extra={'attachment': (None, row.filename)})
            db.commit()
            return response
        except BaseException:
            if temporary: temporary.cleanup()
            raise

    def visible_delivery(db, item_id, user, upload=False):
        item = get_visible(db, DeliveryProject, item_id, user)
        if upload and item.owner_id != user.id:
            raise HTTPException(403, '仅此交付项目的创建人可以上传附件')
        return item

    def get_attachment(db, item_id, attachment_id, user):
        item = visible_delivery(db, item_id, user)
        row = db.scalar(select(DeliveryAttachment).where(DeliveryAttachment.id == attachment_id, DeliveryAttachment.delivery_id == item.id))
        if not row:
            raise HTTPException(404, '附件不存在或不属于此交付项目')
        return item, row

    @app.get('/api/deliveries/{item_id}/attachments')
    def list_attachments(item_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        item = visible_delivery(db, item_id, user)
        rows = db.execute(select(DeliveryAttachment, User.display_name).join(User, User.id == DeliveryAttachment.uploaded_by)
            .where(DeliveryAttachment.delivery_id == item.id).order_by(DeliveryAttachment.created_at.desc(), DeliveryAttachment.id.desc())).all()
        return {'items': [attachment_out(row, name) for row, name in rows], 'can_upload': item.owner_id == user.id,
                'max_size': MAX_FILE_SIZE, 'extensions': list(CONTENT_TYPES)}

    def save_attachment(db, item_id, user_id, filename, extension, temp_path, size, digest):
        # End the upload's initial read snapshot before rechecking live state.
        db.rollback()
        item = db.scalar(select(DeliveryProject).where(DeliveryProject.id == item_id).with_for_update())
        user = db.scalar(select(User).where(User.id == user_id).with_for_update())
        if not user or not user.active:
            raise HTTPException(401, '此账号已停用，请联系管理员启用后再登录')
        if not item:
            raise HTTPException(404, '交付项目不存在或已删除')
        if item.owner_id != user.id:
            raise HTTPException(403, '交付项目创建人已变化，请刷新后重试')
        validate_document(temp_path, extension)
        key = uuid.uuid4().hex + extension
        backend = upload_backend()
        store = MinioDocuments() if backend == 'minio' else None
        target = storage_path(key) if backend == 'local' else None
        stored = False
        try:
            if store:
                store.put(key, temp_path, CONTENT_TYPES[extension], digest)
            else:
                os.replace(temp_path, target)
                target.chmod(0o600)
            stored = True
            row = DeliveryAttachment(delivery_id=item.id, uploaded_by=user.id, filename=filename, extension=extension,
                content_type=CONTENT_TYPES[extension], storage_key=key, size=size, sha256=digest,
                storage_backend=backend, storage_bucket=store.bucket if store else '')
            db.add(row); db.flush()
            audit.record(db, user, 'upload_attachment', 'delivery', item, after={},
                extra={'attachment': (None, filename), 'attachment_size': (None, size)})
            result = attachment_out(row, user.display_name)
            db.commit()
            return result
        except BaseException:
            db.rollback()
            if store and stored:
                store.remove(key)
            elif target:
                target.unlink(missing_ok=True)
            raise

    @app.post('/api/deliveries/{item_id}/attachments', status_code=201)
    async def upload_attachment(item_id: int, request: Request, filename: str = Query(min_length=1, max_length=1000),
                                db: Session = Depends(get_db), user=Depends(current_user)):
        # Raw file body allows streaming limits before parsing or buffering a
        # multipart request. The browser sends one file per authorized request.
        await run_in_threadpool(visible_delivery, db, item_id, user, True)
        owner_id = user.id
        name, extension = normalize_filename(filename)
        length = request.headers.get('content-length', '')
        if length.isdigit() and int(length) > MAX_FILE_SIZE:
            raise HTTPException(413, '单个附件不能超过 20 MB')
        fd, temporary = tempfile.mkstemp(prefix='delivery-upload-')
        path = Path(temporary)
        size, digest = 0, hashlib.sha256()
        try:
            with os.fdopen(fd, 'wb') as file:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_FILE_SIZE:
                        raise HTTPException(413, '单个附件不能超过 20 MB')
                    digest.update(chunk)
                    await run_in_threadpool(file.write, chunk)
            if not size:
                raise HTTPException(400, '不能上传空文件')
            return await run_in_threadpool(save_attachment, db, item_id, owner_id, name, extension, path, size, digest.hexdigest())
        finally:
            path.unlink(missing_ok=True)

    @app.get('/api/deliveries/{item_id}/attachments/{attachment_id}/download')
    def download_attachment(item_id: int, attachment_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        item, row = get_attachment(db, item_id, attachment_id, user)
        path, temporary = prepare_original(row)
        try:
            audit.record(db, user, 'download_attachment', 'delivery', item, after={}, extra={'attachment': (None, row.filename)})
            db.commit()
            return FileResponse(path, filename=row.filename, media_type=row.content_type, headers=HEADERS,
                                background=BackgroundTask(temporary.cleanup) if temporary else None)
        except BaseException:
            if temporary:
                temporary.cleanup()
            raise

    @app.get('/api/deliveries/{item_id}/attachments/{attachment_id}/preview')
    def preview_attachment(item_id: int, attachment_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        item, row = get_attachment(db, item_id, attachment_id, user)
        path, temporary = prepare_preview(row)
        try:
            if row.extension in TEXT_EXTENSIONS:
                response = Response(decode_text(path.read_bytes()), media_type='text/plain; charset=utf-8', headers=HEADERS)
                if temporary:
                    temporary.cleanup()
            else:
                response = FileResponse(path, filename=Path(row.filename).stem + '.pdf', content_disposition_type='inline',
                    media_type='application/pdf', headers=HEADERS, background=BackgroundTask(temporary.cleanup) if temporary else None)
            audit.record(db, user, 'preview_attachment', 'delivery', item, after={}, extra={'attachment': (None, row.filename)})
            db.commit()
            return response
        except BaseException:
            if temporary:
                temporary.cleanup()
            raise
