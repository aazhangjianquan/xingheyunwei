"""Calendar-based expiry reminders, serialized by the business row lock.

Lock order: business row -> reminder plan/delivery -> notification. The outbox
and its delivery identity commit together; preference edits never change period.
"""
import calendar
from datetime import date, datetime, time, timedelta, timezone

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from . import audit, flow_engine as flow, settings
from .models import (Approval, LicenseRequest, NotificationLog, ReminderDelivery,
    ReminderPlan, ResourceReminderContact, ResourceRequest, User, WorkflowInstance,
    WorkflowNode, now)
from .notifications import create_notification
from .roles import has_role

BEIJING = timezone(timedelta(hours=8))
MODELS = {'license': LicenseRequest, 'resource': ResourceRequest}
LABELS = {'license_1m': '到期前 1 个月', 'license_15d': '到期前 15 天',
          'license_7d': '到期前 7 天', 'license_1d': '到期前 1 天', 'resource_7d': '结束前 7 天'}


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value):
    return utc(value).isoformat() if value else None


def month_before(value):
    year, month = (value.year - 1, 12) if value.month == 1 else (value.year, value.month - 1)
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def dates(kind, item):
    if kind == 'license':
        expiry = datetime.fromtimestamp(item.expire_time / 1000, BEIJING)
        rules = [('license_1m', month_before(expiry))] + [
            (f'license_{days}d', expiry - timedelta(days=days)) for days in (15, 7, 1)]
    else:
        end = date.fromisoformat(item.end_date)
        expiry = datetime.combine(end + timedelta(days=1), time.min, BEIJING)
        rules = [('resource_7d', datetime.combine(end - timedelta(days=7), time(9), BEIJING))]
    return utc(expiry), [(rule, utc(stamp)) for rule, stamp in rules]


def latest_instance(db, kind, item):
    return db.scalar(select(WorkflowInstance).where(WorkflowInstance.object_type == kind,
        WorkflowInstance.object_id == item.id).order_by(WorkflowInstance.round.desc()).limit(1))


def execution_tasks(db, instance, *, completed):
    if not instance:
        return []
    return list(db.scalars(select(Approval).join(WorkflowNode, Approval.node_run_id == WorkflowNode.id)
        .where(WorkflowNode.instance_id == instance.id, WorkflowNode.node_type == 'execution',
               WorkflowNode.status == ('completed' if completed else 'pending'),
               Approval.action == ('approve' if completed else 'pending')).order_by(Approval.id)))


def resource_people(db, item, instance, *, save=False):
    contacts = list(db.scalars(select(ResourceReminderContact).where(
        ResourceReminderContact.resource_id == item.id,
        ResourceReminderContact.instance_id == instance.id))) if instance else []
    by_user = {c.user_id: c for c in contacts}
    tasks = execution_tasks(db, instance, completed=item.status == 'completed')
    ids = {t.actor_id if item.status == 'completed' else t.assignee_id for t in tasks}
    ids.discard(None)
    if save and instance and item.status == 'completed':
        for task in tasks:
            if task.actor_id and task.actor_id not in by_user:
                contact = ResourceReminderContact(resource_id=item.id, instance_id=instance.id,
                    user_id=task.actor_id, source_task_id=task.id)
                db.add(contact)
                by_user[task.actor_id] = contact
    if item.status == 'completed':
        ids.update(by_user)
    return ids


def recipients(db, kind, item, instance=None, *, save=False):
    ids = {item.applicant_id}
    if kind == 'license':
        if item.final_approver_id:
            ids.add(item.final_approver_id)
    else:
        ids.update(resource_people(db, item, instance or latest_instance(db, kind, item), save=save))
    return ids


def can_edit(db, kind, item, user, instance=None):
    if not user or not user.active or item.deleted_at is not None:
        return False
    if item.applicant_id == user.id:
        return True
    if kind == 'license':
        return item.status == 'generated' and item.final_approver_id == user.id
    if not has_role(user, 'operator'):
        return False
    instance = instance or latest_instance(db, kind, item)
    if item.status == 'completed':
        return user.id in resource_people(db, item, instance)
    return any(t.assignee_id == user.id and flow.eligible(user, t)
               for t in execution_tasks(db, instance, completed=False))


def is_effective(kind, item):
    return item.deleted_at is None and item.status == ('generated' if kind == 'license' else 'completed') and (
        kind != 'license' or bool(item.final_approver_id))


def period_key(instance, expiry):
    return f'{instance.id if instance else 0}:{utc(expiry).isoformat()}'


def stop_plan(db, plan, reason, status='cancelled'):
    for log in db.scalars(select(NotificationLog).join(ReminderDelivery,
            ReminderDelivery.notification_id == NotificationLog.id).where(
            ReminderDelivery.plan_id == plan.id, NotificationLog.status != 'sent').with_for_update()):
        log.status, log.error = status, reason
        log.dispatch_ready, log.next_attempt_at = False, None


def sync_plans(db, kind, item, stamp=None):
    """Caller holds business row lock and commits. Also reconciles legacy records."""
    if kind not in MODELS:
        return []
    stamp = utc(stamp or now())
    db.flush()
    plans = list(db.scalars(select(ReminderPlan).where(ReminderPlan.object_type == kind,
        ReminderPlan.object_id == item.id).order_by(ReminderPlan.scheduled_at).with_for_update()))
    instance = latest_instance(db, kind, item)
    try:
        expiry, rules = dates(kind, item)
        effective = is_effective(kind, item) and expiry > stamp
    except (ValueError, OverflowError, OSError):
        effective, expiry, rules = False, None, []
    period = period_key(instance, expiry) if effective else None
    active = []
    for plan in plans:
        if plan.period != period:
            plan.state, plan.note = 'obsolete', '单据已删除、到期、未生效或提醒周期已变更'
            stop_plan(db, plan, plan.note)
        else:
            plan.state = 'active'
            active.append(plan)
    if not effective:
        return []
    if kind == 'resource':
        resource_people(db, item, instance, save=True)
    existing = {p.rule for p in active}
    for rule, scheduled in rules:
        if rule not in existing:
            plan = ReminderPlan(object_type=kind, object_id=item.id, period=period,
                instance_id=instance.id if instance else None, rule=rule,
                scheduled_at=scheduled, expires_at=expiry)
            db.add(plan)
            active.append(plan)
    db.flush()
    if not item.reminder_enabled:
        for plan in active:
            stop_plan(db, plan, '单据到期提醒已关闭')
    return sorted(active, key=lambda p: utc(p.scheduled_at))


def message(kind, item, plan, stamp):
    expiry = utc(plan.expires_at).astimezone(BEIJING)
    late = stamp - utc(plan.scheduled_at) > timedelta(minutes=5)
    if kind == 'license':
        minutes = max(1, int((utc(plan.expires_at) - stamp).total_seconds() // 60))
        remaining = f'{minutes // 1440} 天 {minutes % 1440 // 60} 小时' if minutes >= 1440 else (
            f'{minutes // 60} 小时 {minutes % 60} 分钟' if minutes >= 60 else f'{minutes} 分钟')
        node = f'当前剩余约 {remaining}（生效或恢复后补提醒）' if late else LABELS[plan.rule]
        return (f'License 到期提醒\n申请编号：{item.code}\n项目：{item.project_name}'
            f'\n客户 / 产品：{item.customer} / {item.product}'
            f'\n到期时间：{expiry:%Y-%m-%d %H:%M:%S}（北京时间）\n本次提醒：{node}'
            '\n请提前安排续期；无需继续提醒时，可在申请详情关闭。')
    node = '当前使用期即将结束（生效或恢复后补提醒）' if late else LABELS[plan.rule]
    return (f'资源使用结束提醒\n申请编号：{item.code}\n项目：{item.project_name}'
        f'\n资源类型：{item.resource_type}\n结束日期：{item.end_date}（北京时间，含当天）'
        f'\n本次提醒：{node}\n请申请人与运维人员确认是否延期或安排回收。')


def configured_for(db, user):
    return bool(user and user.active and user.feishu_open_id and
        settings.value(db, 'feishu_notifications_enabled') and settings.value(db, 'feishu_app_id') and
        settings.value(db, 'feishu_app_secret'))


def schedule_due(db, kind, item, plans, stamp=None):
    stamp = utc(stamp or now())
    if not item.reminder_enabled:
        return 0
    due = [p for p in plans if p.state == 'active' and utc(p.scheduled_at) <= stamp < utc(p.expires_at)]
    if not due:
        return 0
    current = max(due, key=lambda p: utc(p.scheduled_at))
    for plan in due:
        if plan.id != current.id:
            plan.note = '已进入后续提醒节点，仅保留最近适用提醒'
            stop_plan(db, plan, plan.note, 'skipped')
    count = 0
    for uid in sorted(recipients(db, kind, item)):
        delivery = db.scalar(select(ReminderDelivery).where(ReminderDelivery.plan_id == current.id,
            ReminderDelivery.recipient_id == uid).with_for_update())
        if not delivery:
            log = create_notification(db, uid, current.rule, kind, item, message(kind, item, current, stamp))
            db.add(ReminderDelivery(plan_id=current.id, recipient_id=uid, notification_id=log.id))
            count += 1
        else:
            log = db.scalar(select(NotificationLog).where(NotificationLog.id == delivery.notification_id).with_for_update())
            # Never make automatic scanning reset an exhausted retry budget.
            person = db.get(User, uid)
            role_valid = kind != 'resource' or uid == item.applicant_id or (person and has_role(person, 'operator'))
            if log.status in {'cancelled', 'unconfigured'} and log.attempt_count < 3 and role_valid and configured_for(db, person):
                log.status, log.error, log.dispatch_ready = 'pending', '', True
                log.message = create_message_with_link(db, kind, item, current, stamp)
                log.next_attempt_at = None
                count += 1
    db.flush()
    return count


def create_message_with_link(db, kind, item, plan, stamp):
    section = 'licenses' if kind == 'license' else kind
    link = f"{settings.value(db, 'public_base_url').rstrip('/')}/{section}/{item.id}"
    return f"【{settings.value(db, 'site_name')}】{message(kind, item, plan, stamp)}\n{link}"


def lock_notification_context(db, log_id):
    identity = db.execute(select(ReminderDelivery.id, ReminderPlan.object_type, ReminderPlan.object_id)
        .join(ReminderPlan, ReminderDelivery.plan_id == ReminderPlan.id)
        .where(ReminderDelivery.notification_id == log_id)).first()
    if not identity:
        return None
    model = MODELS[identity.object_type]
    item = db.scalar(select(model).where(model.id == identity.object_id).with_for_update().execution_options(populate_existing=True))
    delivery = db.scalar(select(ReminderDelivery).where(ReminderDelivery.id == identity.id).with_for_update())
    plan = db.scalar(select(ReminderPlan).where(ReminderPlan.id == delivery.plan_id).with_for_update())
    return item, plan, delivery


def invalid_reason(db, context, stamp=None):
    if context is None:
        return None
    item, plan, delivery = context
    stamp = utc(stamp or now())
    if not item or not is_effective(plan.object_type, item):
        return 'cancelled', '单据已删除或未处于提醒生效状态'
    if not item.reminder_enabled:
        return 'cancelled', '单据到期提醒已关闭'
    try:
        expiry, rules = dates(plan.object_type, item)
    except (ValueError, OverflowError, OSError):
        return 'cancelled', '单据到期日期无效'
    instance = latest_instance(db, plan.object_type, item)
    if plan.state != 'active' or plan.period != period_key(instance, expiry):
        return 'cancelled', '提醒周期或到期时间已变更'
    if stamp >= expiry:
        return 'skipped', '业务已到期，停止发送到期前提醒'
    due = [(rule, due_at) for rule, due_at in rules if due_at <= stamp]
    if not due or max(due, key=lambda r: r[1])[0] != plan.rule:
        return 'skipped', '该提醒节点已过时或尚未到达'
    if delivery.recipient_id not in recipients(db, plan.object_type, item, instance):
        return 'cancelled', '收件人已不再负责此单据'
    person = db.get(User, delivery.recipient_id)
    if plan.object_type == 'resource' and delivery.recipient_id != item.applicant_id and (
            not person or not has_role(person, 'operator')):
        return 'unconfigured', '资源收件人已不具备运维角色，请核对责任人'
    return None


def info(db, kind, item, user=None):
    instance = latest_instance(db, kind, item)
    people_ids = recipients(db, kind, item, instance)
    people = list(db.scalars(select(User).where(User.id.in_(people_ids)).order_by(User.id)))
    try:
        expiry, rules = dates(kind, item)
        effective = is_effective(kind, item) and expiry > now()
        period = period_key(instance, expiry)
    except (ValueError, OverflowError, OSError):
        effective, rules, period = False, [], None
    logs = list(db.scalars(select(NotificationLog).join(ReminderDelivery,
        ReminderDelivery.notification_id == NotificationLog.id).join(ReminderPlan, ReminderDelivery.plan_id == ReminderPlan.id)
        .where(ReminderPlan.object_type == kind, ReminderPlan.object_id == item.id)
        .order_by(NotificationLog.id.desc()).limit(20)))
    stamp = now()
    due = [(rule, at) for rule, at in rules if at <= stamp]
    current_rule = max(due, key=lambda p: p[1])[0] if due else None
    next_at = None
    if effective and item.reminder_enabled:
        sent_to = set(db.scalars(select(NotificationLog.recipient_id).join(ReminderDelivery,
            ReminderDelivery.notification_id == NotificationLog.id).join(ReminderPlan,
            ReminderDelivery.plan_id == ReminderPlan.id).where(ReminderPlan.object_type == kind,
            ReminderPlan.object_id == item.id, ReminderPlan.period == period,
            ReminderPlan.rule == current_rule, NotificationLog.status == 'sent')))
        if current_rule and people_ids - sent_to:
            next_at = stamp
        else:
            next_at = min((at for _, at in rules if at > stamp), default=None)
    editor = db.get(User, item.reminder_updated_by) if item.reminder_updated_by else None
    names = {p.id: p.display_name for p in people}
    missing_ops = kind == 'resource' and item.status == 'completed' and not resource_people(db, item, instance)
    return {'enabled': item.reminder_enabled, 'revision': item.reminder_revision,
        'can_edit': can_edit(db, kind, item, user, instance), 'effective': effective,
        'rules': [LABELS[r] for r, _ in rules], 'next_at': iso(next_at),
        'updated_by_name': editor.display_name if editor else None, 'updated_at': iso(item.reminder_updated_at),
        'missing_operator': missing_ops,
        'channel_enabled': bool(settings.value(db, 'feishu_notifications_enabled')),
        'can_fill_contacts': bool(user and kind == 'resource' and instance and item.status == 'completed'
            and has_role(user, 'admin', 'ops_head') and not execution_tasks(db, instance, completed=True)),
        'recipients': [{'id': p.id, 'name': p.display_name, 'active': p.active,
            'configured': bool(p.feishu_open_id), 'role_valid': kind != 'resource' or p.id == item.applicant_id or has_role(p, 'operator')}
            for p in people],
        'records': [{'id': l.id, 'recipient_id': l.recipient_id, 'recipient_name': names.get(l.recipient_id, '原责任人'),
            'rule': LABELS.get(l.event, l.event), 'status': l.status, 'error': l.error,
            'created_at': iso(l.created_at), 'sent_at': iso(l.sent_at)} for l in logs]}


def set_enabled(item, enabled, user):
    if item.reminder_enabled != enabled:
        item.reminder_enabled = enabled
        item.reminder_revision += 1
        item.reminder_updated_by, item.reminder_updated_at = user.id, now()


class ReminderIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool
    expected_revision: int = Field(ge=1)


class ReminderContactsIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    user_ids: list[int] = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=1, max_length=500)


def register_routes(app, current_user):
    def get_item(db, kind, item_id, user, lock=False):
        if kind not in MODELS:
            raise HTTPException(404, '提醒业务不存在')
        if kind == 'license':
            from .licenses import get_visible
            get_visible(db, item_id, user)
        else:
            from .main import get_visible
            get_visible(db, ResourceRequest, item_id, user)
        query = select(MODELS[kind]).where(MODELS[kind].id == item_id)
        return db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)

    from .database import get_db

    @app.get('/api/{section}/{item_id}/reminder')
    def read_reminder(section: str, item_id: int, db=Depends(get_db), user=Depends(current_user)):
        kind = 'license' if section == 'licenses' else section
        return info(db, kind, get_item(db, kind, item_id, user), user)

    @app.patch('/api/{section}/{item_id}/reminder')
    def update_reminder(section: str, item_id: int, data: ReminderIn,
                        db=Depends(get_db), user=Depends(current_user)):
        kind = 'license' if section == 'licenses' else section
        item = get_item(db, kind, item_id, user, lock=True)
        if not can_edit(db, kind, item, user):
            raise HTTPException(403, '只有申请人和本单据的有效处理人可以修改提醒')
        if item.reminder_revision != data.expected_revision:
            raise HTTPException(409, '提醒设置已被其他人修改，请刷新后重试')
        before = audit.snapshot(db, kind, item)
        set_enabled(item, data.enabled, user)
        plans = sync_plans(db, kind, item)
        schedule_due(db, kind, item, plans)
        audit.record(db, user, 'update', kind, item, before=before)
        db.commit()
        return info(db, kind, item, user)

    @app.get('/api/resource/{item_id}/reminder/contacts')
    def contact_candidates(item_id: int, db=Depends(get_db), user=Depends(current_user)):
        item = get_item(db, 'resource', item_id, user)
        if not info(db, 'resource', item, user)['can_fill_contacts']:
            raise HTTPException(403, '仅管理员或运维负责人可以补录缺失的历史运维责任人')
        return [{'id': p.id, 'name': p.display_name} for p in db.scalars(select(User).where(User.active.is_(True)))
            if has_role(p, 'operator') and p.id != item.applicant_id]

    @app.patch('/api/resource/{item_id}/reminder/contacts')
    def fill_contacts(item_id: int, data: ReminderContactsIn, db=Depends(get_db), user=Depends(current_user)):
        item = get_item(db, 'resource', item_id, user, lock=True)
        if not info(db, 'resource', item, user)['can_fill_contacts']:
            raise HTTPException(403, '仅管理员或运维负责人可以补录缺失的历史运维责任人')
        instance = latest_instance(db, 'resource', item)
        people = list(db.scalars(select(User).where(User.id.in_(set(data.user_ids)))))
        if len(people) != len(set(data.user_ids)) or any(
                not p.active or not has_role(p, 'operator') or p.id == item.applicant_id for p in people):
            raise HTTPException(400, '请选择已启用且具备运维角色的人员，不能指定申请人本人')
        previous = list(db.scalars(select(ResourceReminderContact).where(
            ResourceReminderContact.resource_id == item.id, ResourceReminderContact.instance_id == instance.id)))
        before_ids = {c.user_id for c in previous}
        after_ids = set(data.user_ids)
        for contact in previous:
            if contact.user_id not in after_ids:
                db.delete(contact)
        for uid in after_ids - before_ids:
            db.add(ResourceReminderContact(resource_id=item.id, instance_id=instance.id, user_id=uid,
                source='manual', reason=data.reason, created_by=user.id))
        db.flush()
        if before_ids != after_ids:
            names = {p.id: p.display_name for p in db.scalars(select(User).where(User.id.in_(before_ids | after_ids)))}
            audit.record(db, user, 'update', 'resource', item, before={}, after={}, extra={
                'reminder_contact': ('、'.join(names[i] for i in sorted(before_ids)), '、'.join(names[i] for i in sorted(after_ids))),
                'reason': (None, data.reason)})
        plans = sync_plans(db, 'resource', item)
        schedule_due(db, 'resource', item, plans)
        db.commit()
        return info(db, 'resource', item, user)
