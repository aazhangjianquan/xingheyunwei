"""Explicit production bootstrap without demonstration accounts or requests."""
import os
from sqlalchemy import func, select
from .models import User, UserRole
from .security import hash_password, validate_password


def demo_enabled():
    value = os.getenv('SEED_DEMO_DATA', 'true').strip().lower()
    if value not in {'true', 'false', '1', '0'}:
        raise RuntimeError('SEED_DEMO_DATA 必须为 true 或 false')
    return value in {'true', '1'}


def ensure_administrator(db):
    # Existing accounts and passwords are never replaced by a Pod restart.
    if db.scalar(select(func.count()).select_from(User)):
        return
    username = os.getenv('BOOTSTRAP_ADMIN_USERNAME', '').strip()
    password = os.getenv('BOOTSTRAP_ADMIN_PASSWORD', '')
    display_name = os.getenv('BOOTSTRAP_ADMIN_NAME', '系统管理员').strip()
    if not username or len(username) > 80 or not display_name or len(display_name) > 80:
        raise RuntimeError('空数据库首次启动需配置有效的 BOOTSTRAP_ADMIN_USERNAME 和 BOOTSTRAP_ADMIN_NAME')
    try:
        validate_password(password)
    except ValueError as error:
        raise RuntimeError('BOOTSTRAP_ADMIN_PASSWORD 至少 12 位且包含大小写字母、数字、特殊字符中的至少三类') from error
    user = User(username=username, display_name=display_name, department='平台管理', role='admin',
                password_hash=hash_password(password), role_grants=[UserRole(role='admin')])
    db.add(user)
    db.commit()
