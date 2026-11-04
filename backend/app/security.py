import base64
import hashlib
import hmac
import json
import secrets
import time
import re
import string
from cryptography.fernet import Fernet
from .config import KEYS


ACCOUNT_DISABLED_MESSAGE = "此账号已停用，请联系管理员启用后再登录"


def validate_password(password: str) -> str:
    categories = [bool(re.search(pattern, password)) for pattern in (r'[A-Z]', r'[a-z]', r'[0-9]')]
    categories.append(any(char in string.punctuation for char in password))
    if len(password) < 12 or sum(categories) < 3:
        raise ValueError('密码至少 12 位，且包含大写字母、小写字母、数字、特殊字符中的至少三类')
    return password


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 390000)
    return f"pbkdf2_sha256$390000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def _secret() -> bytes:
    return KEYS["app_secret"].encode()


def make_token(user_id: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"sub": user_id, "exp": int(time.time()) + 86400}, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def token_user_id(token: str) -> int | None:
    try:
        payload, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return int(data["sub"]) if data["exp"] > time.time() else None
    except (ValueError, KeyError, TypeError):
        return None


def _fernet() -> Fernet:
    return Fernet(KEYS["fernet_key"].encode())


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def is_encrypted_secret(value: str | None) -> bool:
    if not value:
        return False
    try:
        decrypt_secret(value)
        return True
    except Exception:
        return False


def mask_open_id(value: str | None) -> str:
    if not value:
        return ''
    if len(value) <= 8:
        return '••••••••'
    return f'{value[:3]}••••••••{value[-4:]}'

