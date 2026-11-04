"""Only the database bootstrap connection and master keys live outside MySQL."""
import json
import os
import secrets
import fcntl
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet

INSTANCE_DIR = Path(os.getenv("INSTANCE_DIR", Path(__file__).resolve().parents[2] / ".instance"))
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
KEY_FILE = INSTANCE_DIR / "keys.json"
CONNECTION_FILE = INSTANCE_DIR / "database.json"


def write_private_json(path: Path, data: dict):
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_keys():
    if os.getenv('REQUIRE_SHARED_KEYS', '').lower() in {'true', '1'}:
        if not os.getenv('APP_SECRET') or not os.getenv('FERNET_KEY'):
            raise RuntimeError('此部署必须从统一 Secret 配置 APP_SECRET 和 FERNET_KEY，禁止每个 Pod 自动生成不同密钥')
        if len(os.environ['APP_SECRET']) < 32:
            raise RuntimeError('APP_SECRET 至少需要 32 个字符')
    # Replicas sharing the instance volume must never generate different keys.
    with (INSTANCE_DIR / 'keys.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not KEY_FILE.exists():
            write_private_json(KEY_FILE, {
                "app_secret": os.getenv("APP_SECRET") or secrets.token_urlsafe(48),
                "fernet_key": os.getenv("FERNET_KEY") or Fernet.generate_key().decode(),
            })
        keys = json.loads(KEY_FILE.read_text())
        for field in ('app_secret', 'fernet_key'):
            configured = os.getenv(field.upper())
            if configured and configured != keys[field]:
                raise RuntimeError(f'{field.upper()} 与已保存的主密钥不一致，请统一所有后端副本配置')
        Fernet(keys['fernet_key'].encode())
        return keys


KEYS = load_keys()


def database_url():
    if database_managed_by_environment():
        url = os.getenv('DATABASE_URL', '').strip()
        if not url:
            raise RuntimeError('环境变量管理数据库时必须配置 DATABASE_URL')
        return url
    if CONNECTION_FILE.exists():
        value = json.loads(CONNECTION_FILE.read_text())
        return Fernet(KEYS["fernet_key"].encode()).decrypt(value["url_ciphertext"].encode()).decode()
    return os.getenv("DATABASE_URL", "mysql+pymysql://yunwei:yunwei_dev_2026@127.0.0.1:3306/yunweishenji?charset=utf8mb4")


def save_database_url(url: str):
    if database_managed_by_environment():
        raise RuntimeError('数据库由部署环境统一管理，请更新 Secret 后滚动重启 API 与 Worker')
    write_private_json(CONNECTION_FILE, {"url_ciphertext": Fernet(KEYS["fernet_key"].encode()).encrypt(url.encode()).decode()})


def database_managed_by_environment():
    mode = os.getenv('DATABASE_CONFIG_MODE', 'file').strip().lower()
    if mode not in {'file', 'environment'}:
        raise RuntimeError('DATABASE_CONFIG_MODE 必须为 file 或 environment')
    return mode == 'environment'
