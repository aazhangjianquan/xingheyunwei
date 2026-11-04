"""Redis Streams transport with MySQL notification_logs as a durable outbox."""
import logging
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import or_, select
from . import redis_runtime
from .models import NotificationLog, now

logger = logging.getLogger(__name__)
GROUP = 'feishu_delivery'
ENQUEUE = """
if redis.call('EXISTS', KEYS[2]) == 1 then return '' end
local id = redis.call('XADD', KEYS[1], '*', 'log_id', ARGV[1])
redis.call('SET', KEYS[2], '1', 'EX', 60)
return id
"""


def stream_key():
    return redis_runtime.key('notifications:stream')


def enqueue(log_id):
    return redis_runtime.client().eval(ENQUEUE, 2, stream_key(),
        redis_runtime.key(f'notifications:queued:{log_id}'), str(log_id))


def dispatch(log_id):
    try:
        enqueue(log_id)
    except RedisError:
        # The committed SQL record survives; the worker will enqueue it later.
        logger.warning('通知入队暂不可用，已保留数据库待发送记录 id=%s', log_id)


def ensure_group():
    try:
        redis_runtime.client().xgroup_create(stream_key(), GROUP, id='0-0', mkstream=True)
    except ResponseError as error:
        if 'BUSYGROUP' not in str(error):
            raise


def reconcile(sessions, limit=100):
    stamp = now()
    with sessions() as db:
        ids = list(db.scalars(select(NotificationLog.id).where(NotificationLog.dispatch_ready.is_(True), or_(
            NotificationLog.status == 'pending',
            (NotificationLog.status == 'failed') & (NotificationLog.next_attempt_at.is_not(None)) &
            (NotificationLog.next_attempt_at <= stamp)
        )).order_by(NotificationLog.created_at, NotificationLog.id).limit(limit)))
    for log_id in ids:
        enqueue(log_id)
    return len(ids)


def consume(entries, deliver):
    """Only acknowledge after durable processing; crashes leave reclaimable jobs."""
    connection = redis_runtime.client()
    for entry_id, fields in entries:
        log_id = int(fields['log_id'])
        deliver(log_id, scheduled=True)
        with connection.pipeline(transaction=True) as batch:
            batch.xack(stream_key(), GROUP, entry_id)
            batch.xdel(stream_key(), entry_id)
            batch.delete(redis_runtime.key(f'notifications:queued:{log_id}'))
            batch.execute()

