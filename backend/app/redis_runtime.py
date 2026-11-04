"""Shared Redis connections; business records and authorization remain in MySQL."""
from functools import lru_cache
import os
import re

from redis import Redis


def configured():
    return bool(os.getenv('REDIS_URL', '').strip())


def key(suffix):
    namespace = os.getenv('REDIS_NAMESPACE', 'project_workspace')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', namespace):
        raise ValueError('REDIS_NAMESPACE 仅允许字母、数字、下划线和短横线')
    # Keep multi-key Lua operations in one slot when using a Redis Cluster proxy.
    return '{' + namespace + '}:' + suffix


@lru_cache(maxsize=4)
def _client(url, password):
    return Redis.from_url(url, password=password or None, decode_responses=True,
                          socket_connect_timeout=1, socket_timeout=3,
                          health_check_interval=30, max_connections=64)


def client():
    url = os.getenv('REDIS_URL', '').strip()
    if not url:
        raise RuntimeError('请配置 REDIS_URL')
    return _client(url, os.getenv('REDIS_PASSWORD', ''))

