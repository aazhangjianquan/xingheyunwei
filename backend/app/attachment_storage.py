"""Private MinIO objects; credentials and object locations never go to the browser."""
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import logging
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from fastapi import HTTPException

logger = logging.getLogger(__name__)
MISSING_OBJECT = {'NoSuchKey', 'NoSuchObject', 'NotFound'}


def upload_backend():
    backend = os.getenv('ATTACHMENT_STORAGE', 'minio').strip().lower()
    if backend not in {'local', 'minio'}:
        raise HTTPException(503, '附件存储配置错误，请联系管理员检查 ATTACHMENT_STORAGE')
    return backend


def validate_key(key):
    if not re.fullmatch(r'[a-f0-9]{32}\.[a-z0-9]{2,5}', key):
        raise HTTPException(500, '附件存储记录异常，请联系管理员')
    return key


@dataclass(frozen=True)
class MinioSettings:
    endpoint: str
    secure: bool
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    bucket: str
    region: str


def minio_settings():
    endpoint = os.getenv('MINIO_ENDPOINT', '').strip()
    access = os.getenv('MINIO_ACCESS_KEY', '').strip()
    secret = os.getenv('MINIO_SECRET_KEY', '').strip()
    bucket = os.getenv('MINIO_BUCKET', '').strip()
    region = os.getenv('MINIO_REGION', 'us-east-1').strip()
    try:
        parsed = urlsplit(endpoint)
        parsed.port
    except ValueError:
        raise HTTPException(503, 'MinIO 地址格式错误，请联系管理员检查 docker-compose.yml 中的连接配置') from None
    if (parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment or not access or not secret or not region
            or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket)):
        raise HTTPException(503, 'MinIO 配置不完整或格式错误，请联系管理员检查 docker-compose.yml 中的连接配置')
    return MinioSettings(parsed.netloc, parsed.scheme == 'https', access, secret, bucket, region)


@lru_cache(maxsize=4)
def minio_client(config):
    try:
        import certifi
        from minio import Minio
        import urllib3
    except ImportError:
        raise HTTPException(503, 'MinIO 存储依赖尚未安装，请更新 API 镜像') from None
    # Bounded waits; no automatic retries on authentication/status errors.
    pool = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=30),
        retries=urllib3.Retry(total=2, connect=2, read=1, status=0, backoff_factor=0.3),
        cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())
    try:
        return Minio(config.endpoint, access_key=config.access_key, secret_key=config.secret_key,
                     secure=config.secure, region=config.region, http_client=pool)
    except ValueError:
        raise HTTPException(503, 'MinIO 地址或区域配置错误，请联系管理员检查 docker-compose.yml') from None


def storage_error(error):
    if isinstance(error, HTTPException):
        return error
    code = getattr(error, 'code', '')
    if code in {'AccessDenied', 'InvalidAccessKeyId', 'SignatureDoesNotMatch'}:
        return HTTPException(503, 'MinIO 访问被拒绝，请联系管理员检查 AK/SK 和私有桶读写权限')
    if code == 'NoSuchBucket':
        return HTTPException(503, 'MinIO 附件桶不存在，请联系管理员检查桶配置')
    return HTTPException(503, 'MinIO 附件存储暂不可用，请稍后重试或联系管理员检查连接')


class MinioDocuments:
    def __init__(self, bucket=None):
        self.config = minio_settings()
        self.bucket = bucket or self.config.bucket
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', self.bucket):
            raise HTTPException(500, '附件存储记录异常，请联系管理员')
        self.client = minio_client(self.config)

    @staticmethod
    def key(key, preview=False):
        key = validate_key(key)
        return f'delivery-attachments/previews/{key}.pdf' if preview else f'delivery-attachments/originals/{key}'

    def check_bucket(self):
        try:
            if not self.client.bucket_exists(self.bucket):
                raise HTTPException(503, 'MinIO 附件桶不存在，请联系管理员检查桶配置')
        except Exception as exc:
            raise storage_error(exc) from None

    def put(self, key, source, content_type, digest, preview=False):
        name = self.key(key, preview)
        try:
            self.client.fput_object(self.bucket, name, str(source), content_type=content_type,
                                    metadata={'sha256': digest}, num_parallel_uploads=1)
        except Exception as exc:
            # The object may have reached MinIO before the connection failed.
            self.remove(key, preview=preview)
            raise storage_error(exc) from None

    def remove(self, key, preview=False):
        try:
            self.client.remove_object(self.bucket, self.key(key, preview))
        except Exception:
            logger.warning('MinIO 附件回滚清理失败，请检查孤立对象：%s', self.key(key, preview))

    def fetch(self, key, destination, max_size, *, size=None, digest=None, preview=False, missing_ok=False):
        response = None
        path = Path(destination)
        try:
            response = self.client.get_object(self.bucket, self.key(key, preview))
            count, checksum = 0, hashlib.sha256()
            with path.open('wb') as file:
                for chunk in response.stream(64 * 1024):
                    count += len(chunk)
                    if count > max_size:
                        raise HTTPException(502, '附件存储内容异常，请联系管理员检查文件完整性')
                    file.write(chunk); checksum.update(chunk)
            path.chmod(0o600)
            if not count or (size is not None and count != size) or (digest and checksum.hexdigest() != digest):
                raise HTTPException(502, '附件存储内容异常，请联系管理员检查文件完整性')
            return True
        except Exception as exc:
            if getattr(exc, 'code', '') in MISSING_OBJECT:
                if missing_ok:
                    return False
                raise HTTPException(404, '附件文件暂不可用，请联系管理员检查 MinIO 存储或备份') from None
            raise storage_error(exc) from None
        finally:
            if response is not None:
                response.close()
                response.release_conn()
