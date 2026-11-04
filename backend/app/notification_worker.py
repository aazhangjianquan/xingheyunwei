"""Run with python -m app.notification_worker; workers can be independently scaled."""
import logging
import signal
import socket
import sys
import time
import uuid

from . import redis_runtime
from .database import SessionLocal
from .feishu import deliver_notification
from .notification_queue import GROUP, consume, ensure_group, reconcile, stream_key

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def heartbeat_key():
    return redis_runtime.key('worker:heartbeat:' + socket.gethostname())


def healthcheck():
    if not redis_runtime.client().exists(heartbeat_key()):
        raise SystemExit(1)


def run():
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    consumer = socket.gethostname() + '-' + uuid.uuid4().hex[:8]
    next_scan, next_claim = 0, 0
    claim_cursor = '0-0'
    while not stopped:
        try:
            connection = redis_runtime.client()
            ensure_group()
            stamp = time.monotonic()
            if stamp >= next_scan:
                reconcile(SessionLocal)
                next_scan = stamp + 5
            if stamp >= next_claim:
                claimed = connection.xautoclaim(stream_key(), GROUP, consumer, 60_000,
                                               start_id=claim_cursor, count=1)
                claim_cursor = claimed[0]
                consume(claimed[1], deliver_notification)
                next_claim = stamp + 5
            for _, entries in connection.xreadgroup(GROUP, consumer, {stream_key(): '>'}, count=1, block=1000):
                consume(entries, deliver_notification)
            connection.set(heartbeat_key(), 'ok', ex=60)
        except Exception:
            # Do not log exception text: connection URLs may contain credentials.
            logger.warning('通知 Worker 暂不可用，任务保留并将在连接恢复后重试')
            time.sleep(2)
    try:
        redis_runtime.client().delete(heartbeat_key())
    except Exception:
        pass


if __name__ == '__main__':
    healthcheck() if '--healthcheck' in sys.argv else run()
