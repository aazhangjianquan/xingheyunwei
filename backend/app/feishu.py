import json
import hashlib
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5
import httpx
from cryptography.fernet import InvalidToken
from redis.exceptions import RedisError
from . import redis_runtime
from .database import SessionLocal
from sqlalchemy import select
from .models import NotificationLog, User, now
from .settings import all_values
from .roles import has_role
from .security import decrypt_secret, encrypt_secret, is_encrypted_secret
from . import reminders
from . import personal_reminders
from .notification_cards import message_content


def token_cache_key(app_id, app_secret):
    fingerprint = hashlib.sha256((app_id + '\0' + app_secret).encode()).hexdigest()
    return redis_runtime.key('feishu:tenant_token:' + fingerprint)


def tenant_token(client, app_id, app_secret):
    cache_key = token_cache_key(app_id, app_secret)
    if redis_runtime.configured():
        try:
            cached = redis_runtime.client().get(cache_key)
            if cached:
                return decrypt_secret(cached)
        except (RedisError, InvalidToken, ValueError):
            pass  # An optional token cache failure does not prevent delivery.
    response = client.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                           json={'app_id': app_id, 'app_secret': app_secret})
    response.raise_for_status()
    data = response.json()
    if data.get('code') != 0 or not data.get('tenant_access_token'):
        raise RuntimeError('飞书获取令牌失败')
    token = data['tenant_access_token']
    ttl = max(1, min(7200, int(data.get('expire', 7200))) - 60)
    if redis_runtime.configured():
        try:
            redis_runtime.client().setex(cache_key, ttl, encrypt_secret(token))
        except RedisError:
            pass
    return token


def send_notification(log_id: int):
    if redis_runtime.configured():
        from .notification_queue import dispatch
        dispatch(log_id)
    else:
        deliver_notification(log_id)


def deliver_notification(log_id: int, *, scheduled=False):
    with SessionLocal() as db:
        personal_context = personal_reminders.lock_notification_context(db, log_id)
        context = reminders.lock_notification_context(db, log_id)
        log = db.scalar(select(NotificationLog).where(NotificationLog.id == log_id).with_for_update())
        if not log or log.status not in {'pending', 'failed', 'unconfigured'}:
            return
        invalid = personal_reminders.invalid_reason(db, personal_context) or reminders.invalid_reason(db, context)
        if invalid:
            log.status, log.error = invalid
            log.dispatch_ready, log.next_attempt_at = False, None
            db.commit()
            return
        if scheduled:
            if not log.dispatch_ready:
                return
            if log.status == 'unconfigured':
                return
            if log.status == 'failed' and (log.next_attempt_at is None or
                    log.next_attempt_at.replace(tzinfo=None) > now().replace(tzinfo=None)):
                return
        log.next_attempt_at = None
        user = db.get(User, log.recipient_id)
        settings = all_values(db)
        app_id, app_secret = settings["feishu_app_id"], settings["feishu_app_secret"]
        if not settings["feishu_notifications_enabled"]:
            log.status = "unconfigured"
            log.error = "管理员尚未启用飞书通知"
            db.commit()
            return
        if not user or not user.active:
            log.status = "unconfigured"
            log.error = "通知收件人不存在或账号已停用"
            db.commit()
            return
        if log.event == 'request_rate_exceeded' and not has_role(user, 'admin'):
            log.status = 'unconfigured'
            log.error = '收件人已不再是系统管理员，安全告警已停止发送'
            db.commit()
            return
        if not app_id or not app_secret or not user.feishu_open_id:
            log.status = "unconfigured"
            log.error = "飞书应用凭据或收件人 open_id 未配置"
            db.commit()
            return
        message_type, error_code = 'text', None
        try:
            stamp = now()
            if context:
                item, plan, _ = context
                log.message = reminders.create_message_with_link(db, plan.object_type, item, plan, stamp)
            log.attempt_count += 1
            # 卡片与审计原文使用同一时刻；发送失败走原重试预算，不额外补发另一条消息。
            message_type, content = message_content(log, settings, context=context, stamp=stamp)
            with httpx.Client(timeout=10) as client:
                token = tenant_token(client, app_id, app_secret)
                open_id = decrypt_secret(user.feishu_open_id) if is_encrypted_secret(user.feishu_open_id) else user.feishu_open_id
                response = client.post(
                    "https://open.feishu.cn/open-apis/im/v1/messages",
                    params={"receive_id_type": "open_id"},
                    headers={"Authorization": f"Bearer {token}"},
                    json={"receive_id": open_id, "msg_type": message_type, "content": json.dumps(content, ensure_ascii=False),
                          'uuid': str(uuid5(NAMESPACE_URL, f'{app_id}:{log.id}:{log.created_at.isoformat()}'))},
                )
                response.raise_for_status()
                data = response.json()
                if data.get("code") != 0:
                    code = str(data.get('code', ''))
                    error_code = code if code.isascii() and code.isdecimal() and len(code) <= 12 else None
                    raise RuntimeError('飞书发送失败')
            log.status = "sent"
            log.error = ''
            log.sent_at = now()
            log.next_attempt_at = None
        except Exception:
            log.status = "failed"
            log.error = "飞书发送失败，请检查应用权限、open_id、可用范围及网络后重试"
            if message_type == 'interactive':
                log.error += '；本次为卡片消息，请同时检查卡片格式及飞书客户端版本'
            if error_code:
                log.error += f'（错误码 {error_code}）'
            log.next_attempt_at = now() + timedelta(seconds=30 * log.attempt_count) if log.attempt_count < 3 else None
            if redis_runtime.configured():
                try:
                    redis_runtime.client().delete(token_cache_key(app_id, app_secret))
                except RedisError:
                    pass
        db.commit()

