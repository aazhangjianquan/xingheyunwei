"""Proxy audit addresses and legacy counters; HTTP requests no longer invoke counters."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from ipaddress import ip_address, ip_network
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from redis.exceptions import RedisError

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError

from . import settings
from .models import NotificationLog, RequestRateLimit, User, UserRole
from .roles import has_role
from . import redis_runtime

SHORT_WINDOW_MS = 10_000
SHORT_LIMIT = 20  # Allow twenty; reject the twenty-first attempt.
ALERT_WINDOW_MS = 60_000
ALERT_THRESHOLD = 120  # Hard minute limit and alert on the 121st attempt.
ALERT_COOLDOWN_MS = 60_000
BEIJING = timezone(timedelta(hours=8))
_cleanup_lock = threading.Lock()
_next_cleanup_ms = 0
RATE_SCRIPT = Path(__file__).with_suffix('.lua').read_text()


class RateLimitUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RateDecision:
    allowed: bool = True
    retry_after: int = 0
    notification_ids: tuple[int, ...] = ()


@lru_cache(maxsize=16)
def trusted_proxies(config):
    return tuple(ip_network(part.strip(), strict=False) for part in config.split(',') if part.strip())


def normalize_ip(value):
    try:
        address = ip_address(value.split('%', 1)[0])
        return str(getattr(address, 'ipv4_mapped', None) or address)
    except (ValueError, AttributeError):
        return None


def client_ip(request):
    """Ignore spoofable forwarding headers unless the socket peer is trusted.

    Run Uvicorn with --no-proxy-headers so this is the single trust boundary.
    Walk X-Forwarded-For from the nearest trusted proxy towards the client.
    """
    peer = normalize_ip(request.client.host) if request.client else None
    if peer is None:
        return 'unknown'
    networks = trusted_proxies(os.getenv('TRUSTED_PROXY_CIDRS', ''))
    def is_trusted(address):
        return any(ip_address(address) in network for network in networks)
    if not is_trusted(peer):
        return peer
    header = request.headers.get('x-forwarded-for', '')
    if not header or len(header) > 2048:
        return peer
    parts = [normalize_ip(p.strip()) for p in header.split(',')]
    if len(parts) > 20 or any(p is None for p in parts):
        return peer
    for address in reversed([*parts, peer]):
        if not is_trusted(address):
            return address
    return parts[0]


def _record_alert(db, uid, ip, method, path, triggers, blocked, timestamp):
    admins = [u for u in db.scalars(select(User).where(User.active.is_(True), or_(
        User.role == 'admin', User.id.in_(select(UserRole.user_id).where(UserRole.role == 'admin')))))
        if has_role(u, 'admin')]
    if not admins:
        return ()
    person = db.get(User, uid) if uid else None
    actor = f'{person.display_name}（{person.username}，用户 #{person.id}）' if person else f'未知账号（用户 #{uid}）' if uid else '未登录或登录凭据无效'
    config = settings.values(db, ('site_name', 'public_base_url'))
    stamp = datetime.fromtimestamp(timestamp / 1000, BEIJING).strftime('%Y-%m-%d %H:%M:%S')
    text = (f'【{config["site_name"]}】访问频率异常提醒\n'
            f'触发时间：{stamp}（北京时间）\n'
            '触发维度：IP\n'
            f'请求频率：滚动 60 秒内至少 {ALERT_THRESHOLD+1} 次（超过 {ALERT_THRESHOLD} 次）\n'
            f'访问账号：{actor}\n来源 IP：{ip}\n'
            f'最近请求：{method[:10]} {path[:255]}\n'
            f'本次处理：{"已限流，请求未进入业务处理" if blocked else "已记录频率告警"}\n'
            '统计包含该 IP 的成功、失败及被限流的请求；同一 IP 每 60 秒最多提醒一次。\n'
            f'{config["public_base_url"].rstrip("/")}/notifications')
    logs = [NotificationLog(recipient_id=u.id, event='request_rate_exceeded', object_type='security',
                            object_id=uid or 0, message=text) for u in admins]
    db.add_all(logs); db.flush()
    return tuple(log.id for log in logs)


def _cleanup(db, timestamp):
    global _next_cleanup_ms
    with _cleanup_lock:
        if timestamp < _next_cleanup_ms:
            return
        _next_cleanup_ms = timestamp + 60_000
    # Retain enough idle history to cover both windows and the alert cooldown.
    stale = list(db.scalars(select(RequestRateLimit.subject)
        .where(RequestRateLimit.updated_ms < timestamp - 120_000)
        .order_by(RequestRateLimit.updated_ms).limit(1000)))
    if stale:
        db.execute(delete(RequestRateLimit).where(RequestRateLimit.subject.in_(stale),
                   RequestRateLimit.updated_ms < timestamp - 120_000))


def check_request_rate(sessions, uid, ip, method, path, *, now_ms=None):
    if redis_runtime.configured():
        return _redis_check(sessions, uid, ip, method, path, now_ms=now_ms)
    return _database_check(sessions, uid, ip, method, path, now_ms=now_ms)


def _redis_check(sessions, uid, ip, method, path, *, now_ms=None):
    # uid is alert context only. Never combine different clients through an account.
    subjects = [f'ip:{ip}']
    keys = [redis_runtime.key(f'rate:{subject}:{suffix}') for subject in subjects for suffix in ('attempts', 'alert')]
    try:
        result = redis_runtime.client().eval(RATE_SCRIPT, len(keys), *keys,
            -1 if now_ms is None else int(now_ms), SHORT_WINDOW_MS, SHORT_LIMIT,
            ALERT_WINDOW_MS, ALERT_THRESHOLD, uuid.uuid4().hex)
    except RedisError as error:
        # Never silently switch individual replicas to independent quotas.
        raise RateLimitUnavailable('限流服务暂时不可用，请稍后重试') from error
    allowed, retry, stamp, indices = result
    ids = ()
    if indices:
        with sessions() as db:
            ids = _record_alert(db, uid, ip, method, path,
                                [subjects[int(i)-1] for i in indices], not allowed, int(stamp))
            db.commit()
    return RateDecision(bool(allowed), int(retry), ids)


def _database_check(sessions, uid, ip, method, path, *, now_ms=None):
    keys = [f'ip:{ip}']
    for attempt in range(3):
        timestamp = time.time_ns() // 1_000_000 if now_ms is None else int(now_ms)
        try:
            with sessions() as db:
                dialect = db.bind.dialect.name
                values = [{'subject':key,'attempts':'[]','alert_after_ms':0,'updated_ms':timestamp} for key in keys]
                if dialect == 'mysql':
                    statement = mysql_insert(RequestRateLimit).values(values)
                    statement = statement.on_duplicate_key_update(subject=RequestRateLimit.subject)
                elif dialect == 'sqlite':
                    statement = sqlite_insert(RequestRateLimit).values(values).on_conflict_do_nothing(index_elements=['subject'])
                else:
                    raise RuntimeError('请求频率校验仅支持 MySQL 和 SQLite')
                # Upsert acquires the write lock even when a subject is new;
                # sorted keys give every worker the same lock order.
                db.execute(statement)
                rows = list(db.scalars(select(RequestRateLimit).where(RequestRateLimit.subject.in_(keys))
                                       .order_by(RequestRateLimit.subject).with_for_update()))
                timestamp = max(timestamp, *(row.updated_ms for row in rows))
                histories, triggers = {}, []
                for row in rows:
                    history = [t for t in json.loads(row.attempts) if timestamp-ALERT_WINDOW_MS < t <= timestamp]
                    history.append(timestamp)
                    # Bounded retained attempts suffice for both thresholds. Larger
                    # attack volumes cannot grow the per-subject record.
                    history = history[-(ALERT_THRESHOLD+1):]
                    histories[row.subject] = history
                    row.attempts, row.updated_ms = json.dumps(history), timestamp
                    if len(history) > ALERT_THRESHOLD and timestamp >= row.alert_after_ms:
                        triggers.append(row.subject)
                        row.alert_after_ms = timestamp + ALERT_COOLDOWN_MS
                waits = []
                for history in histories.values():
                    short = [t for t in history if timestamp-SHORT_WINDOW_MS < t]
                    if len(short) > SHORT_LIMIT:
                        waits.append(short[-SHORT_LIMIT]+SHORT_WINDOW_MS-timestamp)
                    if len(history) > ALERT_THRESHOLD:
                        waits.append(history[-ALERT_THRESHOLD]+ALERT_WINDOW_MS-timestamp)
                blocked = bool(waits)
                retry = max(1, math.ceil(max(waits)/1000)) if blocked else 0
                ids = _record_alert(db, uid, ip, method, path, triggers, blocked, timestamp) if triggers else ()
                db.flush()
                _cleanup(db, timestamp)
                db.commit()
                return RateDecision(not blocked, retry, ids)
        except OperationalError as exc:
            code = exc.orig.args[0] if exc.orig.args else None
            retryable = code in (1205, 1213) or 'database is locked' in str(exc.orig)
            if attempt == 2 or not retryable:
                raise
