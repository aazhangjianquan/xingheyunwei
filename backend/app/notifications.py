"""Shared durable notification creation for HTTP requests and schedulers."""
from .models import NotificationLog
from . import settings


def create_notification(db, recipient_id, event, kind, obj, text):
    section = 'licenses' if kind == 'license' else kind
    link = f"{settings.value(db, 'public_base_url').rstrip('/')}/{section}/{obj.id}"
    log = NotificationLog(recipient_id=recipient_id, event=event, object_type=kind,
        object_id=obj.id, message=f"【{settings.value(db, 'site_name')}】{text}\n{link}")
    db.add(log)
    db.flush()
    return log
