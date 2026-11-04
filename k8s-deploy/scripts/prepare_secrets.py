"""Prepare a local Kubernetes Secret without printing values; new/migrate are explicit."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import string
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def prepare(values, mode, keys=None):
    allowed = {'DATABASE_URL', 'MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY', 'REDIS_PASSWORD',
               'BOOTSTRAP_ADMIN_USERNAME', 'BOOTSTRAP_ADMIN_NAME', 'BOOTSTRAP_ADMIN_PASSWORD',
               'APP_SECRET', 'FERNET_KEY'}
    if not isinstance(values, dict) or set(values) - allowed or any(not isinstance(value, str) for value in values.values()):
        raise ValueError('输入只允许模板中的字符串 Secret 字段，不能覆盖生产 ConfigMap 开关')
    data = {key: str(value) for key, value in values.items()}
    for key in ('DATABASE_URL', 'MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY'):
        if not data.get(key) or any(word in data[key] for word in ('REPLACE_', 'example.internal')):
            raise ValueError(key + ' 需要替换成真实配置')
    url = urlsplit(data['DATABASE_URL'])
    if url.scheme != 'mysql+pymysql' or url.path != '/yunweishenji' or not url.hostname or not url.username:
        raise ValueError('DATABASE_URL 必须是指向 yunweishenji 的 mysql+pymysql 连接串')
    if mode == 'migrate':
        if not keys or not keys.get('app_secret') or not keys.get('fernet_key'):
            raise ValueError('迁移必须提供原 keys.json；禁止重新生成主密钥')
        data['APP_SECRET'], data['FERNET_KEY'] = keys['app_secret'], keys['fernet_key']
    else:
        data['APP_SECRET'] = data.get('APP_SECRET') or secrets.token_urlsafe(48)
        data['FERNET_KEY'] = data.get('FERNET_KEY') or base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    try:
        if len(base64.urlsafe_b64decode(data['FERNET_KEY'])) != 32 or len(data['APP_SECRET']) < 32:
            raise ValueError()
    except (ValueError, KeyError):
        raise ValueError('主密钥格式无效') from None
    data['REDIS_PASSWORD'] = data.get('REDIS_PASSWORD') or secrets.token_urlsafe(48)
    data['BOOTSTRAP_ADMIN_USERNAME'] = data.get('BOOTSTRAP_ADMIN_USERNAME', 'admin')
    data['BOOTSTRAP_ADMIN_NAME'] = data.get('BOOTSTRAP_ADMIN_NAME', '系统管理员')
    data['BOOTSTRAP_ADMIN_PASSWORD'] = data.get('BOOTSTRAP_ADMIN_PASSWORD') or ('Aa1!' + secrets.token_urlsafe(24))
    password = data['BOOTSTRAP_ADMIN_PASSWORD']
    categories = [bool(re.search(pattern, password)) for pattern in (r'[A-Z]', r'[a-z]', r'[0-9]')]
    categories.append(any(char in string.punctuation for char in password))
    if len(password) < 12 or sum(categories) < 3:
        raise ValueError('初始密码至少 12 位，且包含至少三类字符')
    return {'apiVersion': 'v1', 'kind': 'Secret',
            'metadata': {'name': 'workspace-secrets', 'namespace': 'project-workspace'},
            'type': 'Opaque', 'stringData': data}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=('new', 'migrate'))
    parser.add_argument('--input', type=Path, default=ROOT/'.private/input.json')
    parser.add_argument('--keys-file', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT/'.private/workspace-secrets.yaml')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('输出 Secret 已存在，禁止覆盖以免误换主密钥；请先检查现有文件')
    if args.mode == 'migrate' and not args.keys_file:
        raise SystemExit('迁移请指定 --keys-file，原主密钥不能重新生成')
    try:
        inputs = json.loads(args.input.read_text())
        keys = json.loads(args.keys_file.read_text()) if args.keys_file else None
        result = prepare(inputs, args.mode, keys)
    except (ValueError, OSError) as error:
        # Configuration errors do not echo input values or connection strings.
        raise SystemExit('无法生成 Secret，请检查输入文件、主密钥和必填项。' +
                         (' ' + str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else '')) from None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.chmod(0o700)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as file:
        # JSON is valid Kubernetes YAML and needs no third-party dependency.
        json.dump(result, file, ensure_ascii=False, indent=2)
        file.write('\n')
    print('Secret 已保存到私有目录（权限 600），未输出凭据。请妥善备份且不要提交 Git。')
