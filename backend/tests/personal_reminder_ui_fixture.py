"""Dedicated loopback UI fixture. Run directly, never import into the deployed app."""
import os
from pathlib import Path
import tempfile


def run():
    with tempfile.TemporaryDirectory(prefix='personal-reminder-ui-') as folder:
        root = Path(folder)
        os.environ.update(INSTANCE_DIR=str(root / 'instance'), DATABASE_URL='sqlite:///' + str(root / 'db.sqlite'),
            DATABASE_CONFIG_MODE='environment', REQUIRE_SHARED_KEYS='false', ATTACHMENT_STORAGE='local',
            SEED_DEMO_DATA='false', REDIS_URL='')
        from contextlib import asynccontextmanager
        from datetime import timedelta
        from sqlalchemy import select
        from app.main import app
        from app.database import Base, engine, SessionLocal
        from app.models import PersonalReminder, User, UserRole, now
        from app.personal_reminders import scan_due
        from app.security import hash_password
        import uvicorn

        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            for username, name, role, open_id in [
                ('pr-owner', '提醒创建者', 'applicant', 'ou_fixture_owner'),
                ('pr-recipient', '被提醒人', 'applicant', 'ou_fixture_recipient'),
                ('pr-missing', '未配置飞书的成员', 'applicant', None),
                ('pr-admin', '其他管理员', 'admin', 'ou_fixture_admin')]:
                db.add(User(username=username, display_name=name, role=role, role_grants=[UserRole(role=role)],
                    password_hash=hash_password('PersonalReminder123!'), department='测试部门', feishu_open_id=open_id))
            db.commit()

        @asynccontextmanager
        async def lifespan(_):
            yield
        app.router.lifespan_context = lifespan

        @app.post('/_test/trigger/{reminder_id}')
        def trigger(reminder_id: int):
            with SessionLocal() as db:
                reminder = db.scalar(select(PersonalReminder).where(PersonalReminder.id == reminder_id))
                if reminder:
                    reminder.remind_at = now() - timedelta(minutes=1)
                    db.commit()
            return scan_due(SessionLocal, now())

        # Only this isolated fixture exposes a clock-control endpoint; there is no Worker or real notification channel.
        try:
            uvicorn.run(app, host='127.0.0.1', port=18075)
        finally:
            engine.dispose()


if __name__ == '__main__':
    run()
