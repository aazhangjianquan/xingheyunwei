"""Private reminders: ownership, shared files, scheduling and send-time revocation."""
import json
import os
from datetime import timedelta
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
from app.models import (NotificationLog, PersonalReminder, PersonalReminderAttachment,
                        PersonalReminderRecipient, SystemSetting, User, now)
from app.personal_reminders import scan_due
from app.reminder_scheduler import run_once
from app.security import encrypt_secret, make_token


class PersonalReminderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='personal-reminders-')
        self.root = Path(self.folder.name)
        self.engine = create_engine('sqlite:///' + str(self.root / 'db.sqlite'))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [patch('app.main.SessionLocal', self.sessions), patch('app.feishu.SessionLocal', self.sessions),
                        patch('app.attachments.INSTANCE_DIR', self.root / 'instance'),
                        patch.dict(os.environ, {'ATTACHMENT_STORAGE': 'local'})]
        for p in self.patches: p.start()
        def dependency():
            with self.sessions() as db: yield db
        app.dependency_overrides[get_db] = dependency
        self.client = TestClient(app)
        self.ids = {}
        with self.sessions() as db:
            for name, role in [('owner', 'applicant'), ('recipient', 'applicant'), ('other', 'applicant'),
                               ('admin', 'admin'), ('missing', 'applicant'), ('disabled', 'applicant')]:
                user = User(username=name, display_name=name, department='测试', role=role, password_hash='unused',
                            feishu_open_id=None if name == 'missing' else 'ou_' + name, active=name != 'disabled')
                db.add(user); db.flush(); self.ids[name] = user.id
            for key, value in {'site_name': '测试平台', 'public_base_url': 'https://test.example',
                    'feishu_notifications_enabled': True, 'feishu_app_id': 'fake-app', 'feishu_app_secret': 'fake-secret'}.items():
                raw = json.dumps(value)
                db.add(SystemSetting(key=key, value=encrypt_secret(raw) if key == 'feishu_app_secret' else raw,
                    is_secret=key == 'feishu_app_secret'))
            db.commit()
        self.headers = {name: {'Authorization': 'Bearer ' + make_token(uid)} for name, uid in self.ids.items()}

    def tearDown(self):
        self.client.close(); app.dependency_overrides.pop(get_db, None)
        for p in reversed(self.patches): p.stop()
        self.engine.dispose(); self.folder.cleanup()

    def create(self, who='owner', recipient_names=('owner', 'recipient'), **changes):
        payload = {'content': '私密提醒\n请检查项目资料', 'remind_at': (now() + timedelta(hours=2)).isoformat(),
                   'recipient_ids': [self.ids[n] for n in recipient_names]}
        payload.update(changes)
        return self.client.post('/api/personal-reminders', json=payload, headers=self.headers[who])

    def activate(self, reminder_id, who='owner'):
        return self.client.post(f'/api/personal-reminders/{reminder_id}/activate', json={}, headers=self.headers[who])

    def due(self, reminder_id):
        with self.sessions() as db:
            db.get(PersonalReminder, reminder_id).remind_at = now() - timedelta(minutes=1)
            db.commit()
        return run_once(self.sessions)

    def upload(self, reminder_id, who='owner', filename='提醒说明.txt', content='提醒附件'.encode()):
        return self.client.post(f'/api/personal-reminders/{reminder_id}/attachments',
            params={'filename': filename}, content=content, headers={**self.headers[who], 'Content-Type': 'application/octet-stream'})

    def test_people_only_eligible_and_no_ids_exposed(self):
        response = self.client.get('/api/personal-reminders/people', headers=self.headers['owner'])
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual({p['name'] for p in rows}, {'owner', 'recipient', 'other', 'admin'})
        self.assertTrue(next(p for p in rows if p['name'] == 'owner')['is_self'])
        self.assertNotIn('ou_', response.text)
        self.assertEqual(self.client.get('/api/personal-reminders/people').status_code, 401)

    def test_input_validation_and_self_only(self):
        for names in [('missing',), ('disabled',)]:
            self.assertEqual(self.create(recipient_names=names).status_code, 422)
        for changes in [{'content': '   '}, {'recipient_ids': []}, {'recipient_ids': [99999]},
                        {'remind_at': '2020-01-01T09:00:00+08:00'}, {'remind_at': '2099-01-01T09:00:00'},
                        {'owner_id': self.ids['other']}]:
            self.assertEqual(self.create(**changes).status_code, 422, changes)
        row = self.create(recipient_names=('owner', 'owner')).json()
        self.assertEqual([p['id'] for p in row['recipients']], [self.ids['owner']])

    def test_beijing_time_is_persisted_as_utc(self):
        row = self.create(remind_at='2099-01-02T09:30:00+08:00').json()
        self.assertEqual(row['remind_at'], '2099-01-02T01:30:00+00:00')

    def test_only_owner_can_view_manage_even_admin(self):
        row = self.create().json(); rid = row['id']
        for name in ['recipient', 'other', 'admin']:
            response = self.client.get('/api/personal-reminders', headers=self.headers[name])
            self.assertEqual(response.json()['total'], 0)
            base = f'/api/personal-reminders/{rid}'
            for response in [self.client.get(base, headers=self.headers[name]),
                             self.client.post(base + '/activate', headers=self.headers[name], json={}),
                             self.client.post(base + '/cancel', headers=self.headers[name], json={}),
                             self.client.delete(base, headers=self.headers[name]), self.upload(rid, name)]:
                self.assertEqual(response.status_code, 404, (name, response.text))
        self.assertEqual(self.client.get(f'/api/personal-reminders/{rid}', headers=self.headers['owner']).status_code, 200)

    def test_activation_rechecks_people_time_and_draft_edit(self):
        rid = self.create().json()['id']
        with self.sessions() as db:
            db.get(User, self.ids['recipient']).feishu_open_id = None; db.commit()
        self.assertEqual(self.activate(rid).status_code, 422)
        response = self.client.put(f'/api/personal-reminders/{rid}', headers=self.headers['owner'],
            json={'content': '仅自己', 'remind_at': (now()+timedelta(hours=1)).isoformat(), 'recipient_ids': [self.ids['owner']]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.activate(rid).json()['state'], 'scheduled')
        self.assertEqual(self.activate(rid).status_code, 409)
        self.assertEqual(self.upload(rid).status_code, 409)

    def test_draft_not_sent_and_scan_is_idempotent(self):
        rid = self.create().json()['id']
        self.assertEqual(scan_due(self.sessions, now()+timedelta(days=1))['notifications'], 0)
        self.activate(rid)
        with self.sessions() as db:
            db.get(PersonalReminder, rid).remind_at = now()-timedelta(minutes=1); db.commit()
        self.assertEqual(scan_due(self.sessions, now(), preview=True)['notifications'], 2)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(NotificationLog)), 0)
        self.assertEqual(run_once(self.sessions)['notifications'], 2)
        self.assertEqual(run_once(self.sessions)['notifications'], 0)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(NotificationLog)), 2)
            self.assertEqual(db.get(PersonalReminder, rid).state, 'dispatched')
            logs = list(db.scalars(select(NotificationLog)))
            self.assertIn('/personal-reminders/' + str(rid) + '/files', logs[0].message)
            self.assertIn('北京时间', logs[0].message)

    def test_files_shared_after_trigger_without_detail_access(self):
        rid = self.create().json()['id']
        response = self.upload(rid); self.assertEqual(response.status_code, 201, response.text)
        fid = response.json()['id']; base = f'/api/personal-reminders/{rid}/attachments'
        self.assertEqual(self.client.get(base, headers=self.headers['recipient']).status_code, 404)
        self.activate(rid); self.due(rid)
        for name, expected in [('owner', 200), ('recipient', 200), ('other', 404), ('admin', 404)]:
            self.assertEqual(self.client.get(base, headers=self.headers[name]).status_code, expected)
            response = self.client.get(f'{base}/{fid}/download', headers=self.headers[name])
            self.assertEqual(response.status_code, expected)
            if expected == 200: self.assertEqual(response.content, '提醒附件'.encode())
            self.assertEqual(self.client.get(f'{base}/{fid}/preview', headers=self.headers[name]).status_code, expected)
        self.assertEqual(self.client.get(f'/api/personal-reminders/{rid}', headers=self.headers['recipient']).status_code, 404)
        other_rid = self.create().json()['id']
        self.assertEqual(self.client.get(f'/api/personal-reminders/{other_rid}/attachments/{fid}/download', headers=self.headers['owner']).status_code, 404)

    def test_upload_validation_and_draft_survives_failure(self):
        rid = self.create().json()['id']
        for filename, body, status in [('bad.exe', b'file', 400), ('empty.txt', b'', 400), ('bad.pdf', b'not-pdf', 400)]:
            self.assertEqual(self.upload(rid, filename=filename, content=body).status_code, status)
        with patch('app.personal_reminders.MAX_ATTACHMENTS', 1):
            self.assertEqual(self.upload(rid).status_code, 201)
            self.assertEqual(self.upload(rid).status_code, 422)
        self.assertEqual(self.client.get(f'/api/personal-reminders/{rid}', headers=self.headers['owner']).json()['state'], 'draft')

    def test_cancel_delete_prevent_pending_send_and_file_access(self):
        rid = self.create().json()['id']; self.upload(rid); self.activate(rid); self.due(rid)
        self.assertEqual(self.client.post(f'/api/personal-reminders/{rid}/cancel', headers=self.headers['owner']).status_code, 200)
        self.assertEqual(self.client.get(f'/api/personal-reminders/{rid}/attachments', headers=self.headers['recipient']).status_code, 404)
        with self.sessions() as db:
            self.assertEqual({l.status for l in db.scalars(select(NotificationLog))}, {'cancelled'})
        self.assertEqual(self.client.delete(f'/api/personal-reminders/{rid}', headers=self.headers['owner']).status_code, 200)
        self.assertEqual(self.client.get('/api/personal-reminders', headers=self.headers['owner']).json()['total'], 0)
        self.assertEqual(self.client.get(f'/api/personal-reminders/{rid}/attachments', headers=self.headers['owner']).status_code, 404)

    def deliver(self, log_id):
        from app.feishu import deliver_notification
        real_client = httpx.Client
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'fake-token', 'expire': 7200})
        with patch('app.feishu.redis_runtime.configured', return_value=False), patch('app.feishu.httpx.Client',
                side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)):
            deliver_notification(log_id)
        return calls

    def test_transport_revalidates_cancellation_owner_and_recipient(self):
        for condition in ['cancel', 'owner_disabled', 'recipient_unconfigured', 'valid']:
            rid = self.create(recipient_names=('recipient',)).json()['id']; self.activate(rid); self.due(rid)
            with self.sessions() as db:
                log_id = db.scalar(select(NotificationLog.id).where(NotificationLog.object_id == rid))
                if condition == 'cancel': db.get(PersonalReminder, rid).state = 'cancelled'
                if condition == 'owner_disabled': db.get(User, self.ids['owner']).active = False
                if condition == 'recipient_unconfigured': db.get(User, self.ids['recipient']).feishu_open_id = None
                db.commit()
            calls = self.deliver(log_id)
            self.assertEqual(len(calls), 2 if condition == 'valid' else 0, condition)
            with self.sessions() as db:
                self.assertEqual(db.get(NotificationLog, log_id).status, 'sent' if condition == 'valid' else 'unconfigured' if condition == 'recipient_unconfigured' else 'cancelled')
                db.get(User, self.ids['owner']).active = True
                db.get(User, self.ids['recipient']).feishu_open_id = 'ou_recipient'; db.commit()

    def test_private_notification_hidden_from_unrelated_admin_and_retry_blocked(self):
        rid = self.create().json()['id']; self.activate(rid); self.due(rid)
        for name, count in [('owner', 1), ('recipient', 1), ('admin', 0), ('other', 0)]:
            response = self.client.get('/api/notifications?page=1', headers=self.headers[name])
            self.assertEqual(response.json()['total'], count, response.text)
        with self.sessions() as db:
            log = db.scalar(select(NotificationLog).limit(1)); log.status = 'failed'; log_id = log.id; db.commit()
        response = self.client.post(f'/api/admin/notifications/{log_id}/retry', headers=self.headers['admin'])
        self.assertEqual(response.status_code, 404)
        response = self.client.post(f'/api/personal-reminders/{rid}/retry/{self.ids["owner"]}', headers=self.headers['recipient'])
        self.assertEqual(response.status_code, 404)

    def test_pagination_is_owner_scoped(self):
        for _ in range(3): self.create()
        self.create(who='other')
        response = self.client.get('/api/personal-reminders?page=2&page_size=2', headers=self.headers['owner']).json()
        self.assertEqual((response['total'], response['total_pages'], len(response['items'])), (3, 2, 1))

    def test_owner_retry_reuses_same_delivery_and_cancel_blocks_retry(self):
        rid = self.create(recipient_names=('recipient',)).json()['id']; self.activate(rid); self.due(rid)
        with self.sessions() as db:
            log = db.scalar(select(NotificationLog).where(NotificationLog.object_id == rid))
            log.status, log.attempt_count, log_id = 'failed', 3, log.id
            db.commit()
        path = f'/api/personal-reminders/{rid}/retry/{self.ids["recipient"]}'
        with patch('app.feishu.send_notification') as send:
            response = self.client.post(path, json={}, headers=self.headers['owner'])
            self.assertEqual(response.status_code, 200, response.text)
            send.assert_called_once_with(log_id)
        with self.sessions() as db:
            log = db.get(NotificationLog, log_id)
            self.assertEqual((log.status, log.attempt_count), ('pending', 0))
            self.assertEqual(db.scalar(select(func.count()).select_from(NotificationLog)), 1)
        self.client.post(f'/api/personal-reminders/{rid}/cancel', headers=self.headers['owner'])
        self.assertEqual(self.client.post(path, json={}, headers=self.headers['owner']).status_code, 409)


if __name__ == '__main__': unittest.main()
