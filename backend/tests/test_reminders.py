"""Expiry scheduling, access, cancellation and transport checks on an isolated DB."""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import (Approval, AuditLog, LicenseRequest, NotificationLog, ReminderDelivery,
    ReminderPlan, ResourceRequest, SystemSetting, User, UserRole, WorkflowInstance, WorkflowNode, now)
from app.reminder_scheduler import run_once
from app import reminders
from app.security import encrypt_secret, make_token


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='expiry-reminders-')
        self.engine = create_engine('sqlite:///' + str(Path(self.folder.name) / 'db.sqlite'))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [patch('app.main.SessionLocal', self.sessions),
                        patch('app.feishu.SessionLocal', self.sessions)]
        for p in self.patches:
            p.start()
        def dependency():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = dependency
        self.client = TestClient(app)
        self.ids = {}
        with self.sessions() as db:
            for name, role in [('owner', 'applicant'), ('final', 'applicant'), ('middle', 'applicant'),
                               ('operator', 'operator'), ('operator2', 'operator'), ('unrelated', 'operator'), ('admin', 'admin')]:
                p = User(username=name, display_name=name, department='测试', role=role,
                    role_grants=[UserRole(role=role)], password_hash='unused', feishu_open_id='ou_' + name)
                db.add(p); db.flush(); self.ids[name] = p.id
            for key, value in {'site_name': '测试平台', 'public_base_url': 'https://test.example',
                    'feishu_notifications_enabled': True, 'feishu_app_id': 'fake-app', 'feishu_app_secret': 'fake-secret'}.items():
                raw = json.dumps(value)
                db.add(SystemSetting(key=key, value=encrypt_secret(raw) if key == 'feishu_app_secret' else raw,
                    is_secret=key == 'feishu_app_secret'))
            db.commit()
        self.headers = {name: {'Authorization': 'Bearer ' + make_token(uid)} for name, uid in self.ids.items()}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        for p in reversed(self.patches):
            p.stop()
        self.engine.dispose()
        self.folder.cleanup()

    def create(self, kind='license', days=10, actors=('operator',), with_tasks=True):
        with self.sessions() as db:
            if kind == 'license':
                item = LicenseRequest(code='LIC-' + str(days), applicant_id=self.ids['owner'], project_name='测试项目',
                    customer='客户', product='产品', tenant_id=1, expire_time=int((now() + timedelta(days=days)).timestamp() * 1000),
                    purpose='测试', status='generated', stage='completed', final_approver_id=self.ids['final'],
                    license_key_ciphertext=encrypt_secret('must-never-send-this-key'))
            else:
                end = (now().astimezone(reminders.BEIJING) + timedelta(days=days)).date()
                item = ResourceRequest(code='RES-' + str(days), applicant_id=self.ids['owner'], project_name='测试项目',
                    environment='测试', resource_type='ECS', specification='1台', start_date=str(end - timedelta(days=30)),
                    end_date=str(end), purpose='测试', capacity_basis='测试', status='completed', stage='completed')
            db.add(item); db.flush()
            instance = WorkflowInstance(object_type=kind, object_id=item.id, round=1, snapshot='{}', status='completed')
            db.add(instance); db.flush()
            node = WorkflowNode(instance_id=instance.id, position=0, name='最后处理',
                node_type='license_approval' if kind == 'license' else 'execution', mode='all', config='{}', status='completed')
            db.add(node); db.flush()
            if with_tasks:
                for name in (('final',) if kind == 'license' else actors):
                    db.add(Approval(object_type=kind, object_id=item.id, node_run_id=node.id,
                        stage='operator', assignee_id=self.ids[name], actor_id=self.ids[name], action='approve'))
            db.commit()
            return item.id

    def logs(self):
        with self.sessions() as db:
            return list(db.scalars(select(NotificationLog).order_by(NotificationLog.id)))

    def toggle(self, item_id, enabled, who='owner', revision=1, kind='licenses'):
        return self.client.patch(f'/api/{kind}/{item_id}/reminder', headers=self.headers[who],
            json={'enabled': enabled, 'expected_revision': revision})

    def deliver(self, log_id, *, fail=False):
        from app.feishu import deliver_notification
        real_client = httpx.Client
        calls = []
        def handler(request):
            calls.append(request)
            if '/auth/' in str(request.url):
                return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'fake-token', 'expire': 7200})
            return httpx.Response(200, json={'code': 1 if fail else 0})
        with patch('app.feishu.redis_runtime.configured', return_value=False), patch('app.feishu.httpx.Client',
                side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)):
            deliver_notification(log_id)
        return calls

    def test_calendar_month_and_resource_time(self):
        self.assertEqual(reminders.month_before(datetime(2027, 3, 31, 18, tzinfo=reminders.BEIJING)).day, 28)
        self.assertEqual(reminders.month_before(datetime(2028, 3, 31, 18, tzinfo=reminders.BEIJING)).day, 29)
        self.assertEqual(reminders.month_before(datetime(2027, 1, 31, tzinfo=reminders.BEIJING)).year, 2026)
        item = ResourceRequest(end_date='2026-12-20')
        expiry, nodes = reminders.dates('resource', item)
        self.assertEqual(nodes[0][1].astimezone(reminders.BEIJING).isoformat(), '2026-12-13T09:00:00+08:00')
        self.assertEqual(expiry.astimezone(reminders.BEIJING).isoformat(), '2026-12-21T00:00:00+08:00')

    def test_all_four_license_nodes_are_unique_per_recipient(self):
        item_id = self.create(days=70)
        run_once(self.sessions)
        with self.sessions() as db:
            plans = list(db.scalars(select(ReminderPlan).order_by(ReminderPlan.scheduled_at)))
        for index, plan in enumerate(plans, start=1):
            stamp = reminders.utc(plan.scheduled_at) + timedelta(seconds=1)
            self.assertEqual(run_once(self.sessions, stamp=stamp)['notifications'], 2)
            self.assertEqual(run_once(self.sessions, stamp=stamp)['notifications'], 0)
            self.assertEqual(len(self.logs()), index * 2)
            with self.sessions() as db:
                for log in db.scalars(select(NotificationLog).where(NotificationLog.status == 'pending')):
                    log.status = 'sent'
                db.commit()
        self.assertEqual({l.recipient_id for l in self.logs()}, {self.ids['owner'], self.ids['final']})
        self.assertTrue(all('must-never-send-this-key' not in l.message for l in self.logs()))

    def test_late_activation_only_current_node_and_real_remaining_time(self):
        self.create(days=10)
        self.assertEqual(run_once(self.sessions)['notifications'], 2)
        logs = self.logs()
        self.assertEqual({l.event for l in logs}, {'license_15d'})
        self.assertTrue(all('当前剩余约' in l.message and '还剩 15 天' not in l.message for l in logs))

    def test_permissions_revision_audit_and_cancelled_worker(self):
        item_id = self.create()
        run_once(self.sessions)
        for who in ('middle', 'unrelated', 'admin'):
            self.assertIn(self.toggle(item_id, False, who).status_code, (403, 404))
        self.assertEqual(self.toggle(item_id, False, 'admin').status_code, 403)
        response = self.toggle(item_id, False, 'final')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['revision'], 2)
        self.assertEqual(self.toggle(item_id, True, revision=1).status_code, 409)
        self.assertTrue(all(l.status == 'cancelled' for l in self.logs()))
        self.assertEqual(self.deliver(self.logs()[0].id), [])
        retry = self.client.post(f'/api/admin/notifications/{self.logs()[0].id}/retry', headers=self.headers['admin'])
        self.assertEqual(retry.status_code, 409)
        with self.sessions() as db:
            record = db.scalar(select(AuditLog).where(AuditLog.record_kind == 'business'))
            self.assertIn('reminder_enabled', record.changes)

    def test_reenable_preserves_delivery_ids_and_sent_nodes(self):
        item_id = self.create()
        run_once(self.sessions)
        ids = [l.id for l in self.logs()]
        self.toggle(item_id, False)
        response = self.toggle(item_id, True, revision=2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([l.id for l in self.logs()], ids)
        self.assertTrue(all(l.status == 'pending' for l in self.logs()))
        for log in self.logs():
            self.assertEqual(len(self.deliver(log.id)), 2)
        self.toggle(item_id, False, revision=3)
        self.toggle(item_id, True, revision=4)
        self.assertEqual(run_once(self.sessions)['notifications'], 0)
        self.assertTrue(all(l.status == 'sent' for l in self.logs()))
        self.assertEqual(self.deliver(ids[0]), [])

    def test_resource_actual_operators_and_closed_or_previous_round_ignored(self):
        item_id = self.create('resource', days=3, actors=('operator', 'operator2'))
        with self.sessions() as db:
            instance = reminders.latest_instance(db, 'resource', db.get(ResourceRequest, item_id))
            node = db.scalar(select(WorkflowNode).where(WorkflowNode.instance_id == instance.id))
            db.add(Approval(object_type='resource', object_id=item_id, node_run_id=node.id, stage='operator',
                assignee_id=self.ids['unrelated'], action='closed'))
            db.commit()
        run_once(self.sessions)
        self.assertEqual({l.recipient_id for l in self.logs()}, {self.ids['owner'], self.ids['operator'], self.ids['operator2']})
        self.assertEqual(self.toggle(item_id, False, 'operator', kind='resource').status_code, 200)
        self.assertEqual(self.toggle(item_id, True, 'unrelated', kind='resource', revision=2).status_code, 403)
        with self.sessions() as db:
            db.add(WorkflowInstance(object_type='resource', object_id=item_id, round=2, snapshot='{}', status='pending'))
            db.get(ResourceRequest, item_id).status = 'pending'
            db.commit()
        run_once(self.sessions)
        self.assertEqual(self.toggle(item_id, True, 'operator', kind='resource', revision=2).status_code, 403)

    def test_changed_deadline_and_deleted_or_expired_records_stop_old_messages(self):
        item_id = self.create()
        run_once(self.sessions)
        old_ids = [l.id for l in self.logs()]
        with self.sessions() as db:
            db.get(LicenseRequest, item_id).expire_time += 86400000
            db.commit()
        self.assertEqual(self.deliver(old_ids[0]), [])
        run_once(self.sessions)
        self.assertEqual(len(self.logs()), 4)
        self.assertTrue(all(l.status == 'cancelled' for l in self.logs()[:2]))
        with self.sessions() as db:
            db.get(LicenseRequest, item_id).deleted_at = now()
            db.commit()
        run_once(self.sessions)
        self.assertTrue(all(l.status == 'cancelled' for l in self.logs()))
        self.create(days=-1)
        self.assertEqual(run_once(self.sessions)['notifications'], 0)

    def test_retry_budget_and_configuration_recovery(self):
        item_id = self.create()
        run_once(self.sessions)
        log_id = self.logs()[0].id
        for _ in range(3):
            self.deliver(log_id, fail=True)
        run_once(self.sessions)
        with self.sessions() as db:
            log = db.get(NotificationLog, log_id)
            self.assertEqual((log.status, log.attempt_count, log.next_attempt_at), ('failed', 3, None))
            db.get(SystemSetting, 'feishu_notifications_enabled').value = 'false'
            db.commit()
        other_id = self.logs()[1].id
        self.assertEqual(self.deliver(other_id), [])
        self.assertEqual(self.logs()[1].status, 'unconfigured')
        with self.sessions() as db:
            db.get(SystemSetting, 'feishu_notifications_enabled').value = 'true'
            db.commit()
        run_once(self.sessions)
        self.assertEqual(self.logs()[1].status, 'pending')

    def test_preview_rolls_back_and_missing_legacy_operator_can_be_filled(self):
        item_id = self.create('resource', days=2, with_tasks=False)
        self.assertEqual(run_once(self.sessions, preview=True)['notifications'], 1)
        self.assertEqual(self.logs(), [])
        response = self.client.patch(f'/api/resource/{item_id}/reminder/contacts', headers=self.headers['admin'],
            json={'user_ids': [self.ids['operator']], 'reason': '核对历史执行记录'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['missing_operator'])
        self.assertEqual({l.recipient_id for l in self.logs()}, {self.ids['owner'], self.ids['operator']})

    def test_superseded_node_skips_queued_message_and_creates_current_only(self):
        self.create(days=20)
        run_once(self.sessions)
        self.assertEqual({l.event for l in self.logs()}, {'license_1m'})
        with self.sessions() as db:
            upcoming = db.scalar(select(ReminderPlan).where(ReminderPlan.rule == 'license_15d'))
        result = run_once(self.sessions, stamp=reminders.utc(upcoming.scheduled_at) + timedelta(seconds=1))
        self.assertEqual(result['notifications'], 2)
        self.assertTrue(all(l.status == 'skipped' for l in self.logs()[:2]))
        self.assertEqual({l.event for l in self.logs()[2:]}, {'license_15d'})

    def test_operator_role_revocation_blocks_delivery_but_owner_still_receives(self):
        self.create('resource', days=2)
        run_once(self.sessions)
        with self.sessions() as db:
            person = db.get(User, self.ids['operator'])
            person.role, person.role_grants = 'applicant', [UserRole(role='applicant')]
            db.commit()
        for log in self.logs():
            calls = self.deliver(log.id)
            self.assertEqual(len(calls), 2 if log.recipient_id == self.ids['owner'] else 0)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(NotificationLog.status).where(NotificationLog.recipient_id == self.ids['operator'])), 'unconfigured')
        self.assertEqual(run_once(self.sessions)['notifications'], 0)

    def test_list_filter_and_pending_execution_permissions(self):
        item_id = self.create('resource', days=2)
        with self.sessions() as db:
            item = db.get(ResourceRequest, item_id)
            item.status = 'in_progress'
            instance = reminders.latest_instance(db, 'resource', item)
            node = db.scalar(select(WorkflowNode).where(WorkflowNode.instance_id == instance.id))
            node.status = 'pending'
            task = db.scalar(select(Approval).where(Approval.node_run_id == node.id))
            task.action, task.actor_id = 'pending', None
            task.assignee_snapshot = json.dumps({'id': self.ids['operator'], 'required_role': 'operator', 'roles': ['operator']})
            db.commit()
        self.assertEqual(self.toggle(item_id, False, 'operator', kind='resource').status_code, 200)
        response = self.client.get('/api/resources?reminder=disabled', headers=self.headers['owner'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(self.client.get('/api/resources?reminder=enabled', headers=self.headers['owner']).json()['total'], 0)
        self.assertEqual(run_once(self.sessions)['notifications'], 0)


if __name__ == '__main__':
    unittest.main()
