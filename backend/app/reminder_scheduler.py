"""Durable expiry scheduler: python -m app.reminder_scheduler [--once|--preview]."""
import json
import logging
import signal
import sys
import time
from pathlib import Path
from threading import Event

from sqlalchemy import or_, select

from .database import SessionLocal
from .models import ReminderPlan, now
from .reminders import BEIJING, MODELS, schedule_due, sync_plans

logger = logging.getLogger(__name__)
HEARTBEAT = Path('/tmp/reminder-scheduler-heartbeat')


def run_once(sessions=SessionLocal, *, preview=False, stamp=None):
    stamp = stamp or now()
    counts = {'scanned': 0, 'plans': 0, 'notifications': 0, 'errors': 0}
    for kind, model in MODELS.items():
        cursor = 0
        while True:
            with sessions() as db:
                active = select(ReminderPlan.object_id).where(ReminderPlan.object_type == kind, ReminderPlan.state == 'active')
                not_expired = model.expire_time > int(stamp.timestamp() * 1000) if kind == 'license' else (
                    model.end_date >= stamp.astimezone(BEIJING).date().isoformat())
                ids = list(db.scalars(select(model.id).where(model.id > cursor, or_(
                    (model.deleted_at.is_(None)) & (model.status == ('generated' if kind == 'license' else 'completed')) & not_expired,
                    model.id.in_(active))).order_by(model.id).limit(200)))
            if not ids:
                break
            for item_id in ids:
                try:
                    with sessions() as db:
                        item = db.scalar(select(model).where(model.id == item_id).with_for_update())
                        if not item:
                            continue
                        plans = sync_plans(db, kind, item, stamp)
                        counts['plans'] += len(plans)
                        counts['notifications'] += schedule_due(db, kind, item, plans, stamp)
                        counts['scanned'] += 1
                        db.rollback() if preview else db.commit()
                except Exception:
                    counts['errors'] += 1
                    logger.exception('提醒计划处理失败 kind=%s id=%s', kind, item_id)
            cursor = ids[-1]
    from .personal_reminders import scan_due
    personal = scan_due(sessions, stamp, preview=preview)
    for key in ('scanned', 'notifications', 'errors'):
        counts[key] += personal[key]
    return counts


def run():
    stopped = Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stopped.set())
    while not stopped.is_set():
        try:
            result = run_once()
            logger.info('到期提醒扫描 %s', json.dumps(result))
            if not result['errors']:
                HEARTBEAT.write_text(str(time.time()))
        except Exception:
            logger.exception('到期提醒调度失败，将在下一周期恢复')
        stopped.wait(60)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    if '--healthcheck' in sys.argv:
        raise SystemExit(0 if HEARTBEAT.exists() and time.time() - float(HEARTBEAT.read_text()) < 180 else 1)
    if '--preview' in sys.argv or '--once' in sys.argv:
        result = run_once(preview='--preview' in sys.argv)
        print(json.dumps(result))
        raise SystemExit(1 if result['errors'] else 0)
    run()
