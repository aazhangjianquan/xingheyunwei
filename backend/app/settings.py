import json
from urllib.parse import urlparse
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from .models import SystemSetting
from .security import decrypt_secret, encrypt_secret

DEFAULTS = {
    "site_name": "星河运维协作平台",
    "public_base_url": "http://localhost:8080",
    "feishu_app_id": "",
    "feishu_app_secret": "",
    "feishu_tenant_key": "",
    "feishu_login_enabled": False,
    "feishu_notifications_enabled": False,
    "local_login_enabled": True,
}
SECRET_KEYS = {"feishu_app_secret"}
PUBLIC_KEYS = ("site_name", "feishu_login_enabled", "local_login_enabled")
PASSWORD_LOGIN_MODE = {"feishu_login_enabled": False, "local_login_enabled": True}


def value(db: Session, key: str):
    if key in PASSWORD_LOGIN_MODE:
        return PASSWORD_LOGIN_MODE[key]
    row = db.get(SystemSetting, key)
    if row is None:
        return DEFAULTS[key]
    return json.loads(decrypt_secret(row.value) if row.is_secret else row.value)


def values(db: Session, keys):
    """Read only the requested settings in one query, without loading secrets unnecessarily."""
    rows = {row.key: row for row in db.scalars(select(SystemSetting).where(SystemSetting.key.in_(keys)))}
    return {key: PASSWORD_LOGIN_MODE[key] if key in PASSWORD_LOGIN_MODE else json.loads(decrypt_secret(rows[key].value) if rows[key].is_secret else rows[key].value)
            if key in rows else DEFAULTS[key] for key in keys}


def all_values(db: Session):
    return values(db, tuple(DEFAULTS))


def public_settings(db: Session):
    return values(db, PUBLIC_KEYS)


def admin_settings(db: Session):
    settings = all_values(db)
    settings["feishu_app_secret_set"] = bool(settings.pop("feishu_app_secret"))
    settings.pop("feishu_tenant_key", None)
    return settings


def apply_password_login_mode(db: Session):
    """Retire old login toggles without changing notification credentials or identities."""
    db.flush()
    for key, expected in PASSWORD_LOGIN_MODE.items():
        row = db.get(SystemSetting, key)
        raw = json.dumps(expected)
        if row is None:
            db.add(SystemSetting(key=key, value=raw, is_secret=False))
        elif row.value != raw or row.is_secret:
            row.value, row.is_secret = raw, False


def save_settings(db: Session, changes: dict, actor_id: int):
    current = all_values(db)
    previous = current.copy()
    for key, new_value in changes.items():
        if key not in DEFAULTS:
            raise HTTPException(400, "不支持的配置项")
        if key in PASSWORD_LOGIN_MODE and new_value is not PASSWORD_LOGIN_MODE[key]:
            raise HTTPException(400, "平台已统一使用账号密码登录，飞书仅用于消息通知")
        if key in SECRET_KEYS and new_value == "":
            continue  # 留空保留已保存密钥
        if isinstance(DEFAULTS[key], bool) and not isinstance(new_value, bool):
            raise HTTPException(400, "开关配置必须为布尔值")
        if not isinstance(DEFAULTS[key], bool) and not isinstance(new_value, str):
            raise HTTPException(400, "配置值格式错误")
        current[key] = new_value.strip() if isinstance(new_value, str) else new_value
    parsed = urlparse(current["public_base_url"])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise HTTPException(400, "平台地址需为完整 HTTP(S) 根地址")
    if current["feishu_notifications_enabled"] and not (current["feishu_app_id"] and current["feishu_app_secret"]):
        raise HTTPException(400, "请先填写飞书 App ID 和 App Secret")
    for key in changes:
        if key in SECRET_KEYS and changes[key] == "":
            continue
        raw = json.dumps(current[key], ensure_ascii=False)
        row = db.get(SystemSetting, key) or SystemSetting(key=key)
        row.value = encrypt_secret(raw) if key in SECRET_KEYS else raw
        row.is_secret = key in SECRET_KEYS
        row.updated_by = actor_id
        db.add(row)
    from . import audit
    from .models import User
    keys = [k for k in changes if not (k in SECRET_KEYS and changes[k] == '')]
    audit.record(db, db.get(User, actor_id), 'configure', 'settings',
                 target_id='platform', target_name='平台与飞书通知',
                 before={k: previous[k] for k in keys}, after={k: current[k] for k in keys})
    db.commit()
    return admin_settings(db)
