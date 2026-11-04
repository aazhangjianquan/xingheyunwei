"""Read-only deployed checks; pipe into `docker compose exec -T api python`."""
import json
from pathlib import Path
import re

import httpx
from sqlalchemy import func, inspect, select

from app import reminders, settings
from app.database import SessionLocal, engine
from app.models import LicenseRequest, NotificationLog, ReminderDelivery, ReminderPlan, ResourceRequest, User
from app.roles import has_role
from app.security import make_token

report = {'passed': False, 'checks': [], 'business': {}, 'notification_statuses': {}}
inspector = inspect(engine)
for model in (LicenseRequest, ResourceRequest):
    fields = {c['name'] for c in inspector.get_columns(model.__tablename__)}
    assert {'reminder_enabled', 'reminder_revision', 'reminder_updated_by', 'reminder_updated_at'} <= fields
assert {'reminder_plans', 'reminder_deliveries', 'resource_reminder_contacts'} <= set(inspector.get_table_names())
report['checks'].append('两类业务提醒字段与三张新增表已迁移')

with SessionLocal() as db, httpx.Client(base_url='http://frontend', timeout=15) as client:
    health = client.get('/api/health')
    health.raise_for_status()
    html = client.get('/').text
    paths = re.findall(r'<script[^>]+src="([^"]+)"', html)
    assert paths
    javascript = '\n'.join(client.get(path).text for path in paths if path.startswith('/assets/'))
    for marker in ('到期前 1 个自然月', '开启资源结束飞书提醒', '关闭后，本单据所有接收人', '补录 / 修改历史运维责任人'):
        assert marker in javascript, '部署资源缺少功能：' + marker
    report['checks'].append('Nginx 页面与构建资源已包含 License、资源提醒开关及历史人员补录')
    admin = next(p for p in db.scalars(select(User).where(User.active.is_(True))) if has_role(p, 'admin'))
    headers = {'Authorization': 'Bearer ' + make_token(admin.id)}
    for kind, model, section in [('license', LicenseRequest, 'licenses'), ('resource', ResourceRequest, 'resources')]:
        listing = client.get('/api/' + section, headers=headers)
        listing.raise_for_status()
        assert all('reminder_enabled' in row for row in listing.json()['items'])
        item = db.scalar(select(model).where(model.deleted_at.is_(None)).order_by(model.id.desc()).limit(1))
        if item:
            response = client.get(f'/api/{section}/{item.id}', headers=headers)
            response.raise_for_status()
            state = response.json()['reminder']
            assert {'enabled', 'can_edit', 'revision', 'recipients', 'rules', 'records'} <= set(state)
            owner = db.get(User, item.applicant_id)
            if owner.active:
                owner_headers = {'Authorization': 'Bearer ' + make_token(owner.id)}
                state_response = client.get(f'/api/{"licenses" if kind == "license" else "resource"}/{item.id}/reminder', headers=owner_headers)
                state_response.raise_for_status()
                assert state_response.json()['can_edit'] is True
        disabled = client.get('/api/' + section + '?reminder=disabled', headers=headers)
        disabled.raise_for_status()
        assert all(row['reminder_enabled'] is False for row in disabled.json()['items'])
        report['business'][kind] = {'visible_total': listing.json()['total'],
            'active_plans': db.scalar(select(func.count()).select_from(ReminderPlan).where(
                ReminderPlan.object_type == kind, ReminderPlan.state == 'active'))}
    report['checks'].append('两类列表、详情、开关读取、申请人权限和提醒筛选接口通过')
    duplicates = db.execute(select(ReminderDelivery.plan_id, ReminderDelivery.recipient_id, func.count())
        .group_by(ReminderDelivery.plan_id, ReminderDelivery.recipient_id).having(func.count() > 1)).all()
    assert not duplicates
    report['checks'].append('按计划与接收人检查未发现重复投递记录')
    report['feishu_enabled'] = bool(settings.value(db, 'feishu_notifications_enabled'))
    report['notification_statuses'] = dict(db.execute(select(NotificationLog.status, func.count())
        .join(ReminderDelivery, ReminderDelivery.notification_id == NotificationLog.id).group_by(NotificationLog.status)).all())
    missing = 0
    for item in db.scalars(select(ResourceRequest).where(ResourceRequest.deleted_at.is_(None), ResourceRequest.status == 'completed')):
        if reminders.info(db, 'resource', item, admin)['missing_operator']:
            missing += 1
    report['historical_resources_missing_operator'] = missing

report['passed'] = True
Path('/app/reports/reminders-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
