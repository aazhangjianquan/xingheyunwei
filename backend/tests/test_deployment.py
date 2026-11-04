"""Replica key bootstrapping and permission-safe License dashboard totals."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.database import Base, get_db
from app import main
from app.models import Approval, LicenseRequest, User, UserRole, now
from app.rate_limit import RateDecision
from app.security import make_token


class ReplicaKeysTests(unittest.TestCase):
    def test_concurrent_first_start_shares_keys_and_preserves_old_ciphertext(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, 'INSTANCE_DIR': folder}
            env.pop('APP_SECRET', None)
            env.pop('FERNET_KEY', None)
            script = 'from app.config import KEYS; from app.security import make_token, encrypt_secret; import json; print(json.dumps([KEYS, make_token(5), encrypt_secret("private")]))'
            workers = [subprocess.Popen([sys.executable, '-c', script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(8)]
            results = []
            for worker in workers:
                output, error = worker.communicate(timeout=30)
                self.assertEqual(worker.returncode, 0, error)
                results.append(json.loads(output))
            self.assertTrue(all(result[0] == results[0][0] for result in results))
            self.assertEqual(Path(folder, 'keys.json').stat().st_mode & 0o777, 0o600)
            check = 'from app.security import token_user_id, decrypt_secret; import json,sys; token,cipher=json.loads(sys.argv[1]); assert token_user_id(token)==5; assert decrypt_secret(cipher)=="private"'
            subprocess.run([sys.executable, '-c', check, json.dumps(results[0][1:])], env=env, check=True, capture_output=True)


class DashboardLicenseTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///' + self.folder.name + '/test.db')
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [patch('app.main.SessionLocal', self.sessions), patch('app.main.check_request_rate', return_value=RateDecision())]
        for item in self.patches: item.start()
        def dependency():
            with self.sessions() as db: yield db
        main.app.dependency_overrides[get_db] = dependency
        self.client = TestClient(main.app)
        self.ids = {}
        with self.sessions() as db:
            for name, role in [('owner', 'applicant'), ('other', 'applicant'), ('reviewer', 'applicant'), ('admin', 'admin'), ('manager', 'license_manager')]:
                user = User(username=name, display_name=name, password_hash='unused', role=role, role_grants=[UserRole(role=role)])
                db.add(user); db.flush(); self.ids[name] = user.id
            for index, status in enumerate(['pending', 'in_progress', 'generated', 'rejected', 'pending']):
                row = LicenseRequest(code=f'LIC-{index}', applicant_id=self.ids['owner'] if index < 4 else self.ids['other'],
                    project_name=f'项目{index}', customer='客户', product='产品', tenant_id=1, expire_time=2000000000000,
                    purpose='测试', status=status, license_key_ciphertext='must-not-be-selected-or-exposed')
                db.add(row); db.flush()
                if index == 0:
                    db.add(Approval(object_type='license', object_id=row.id, assignee_id=self.ids['reviewer'], stage='初审'))
                    self.pending_id = row.id
            db.commit()
        self.headers = {name: {'Authorization': 'Bearer ' + make_token(uid)} for name, uid in self.ids.items()}

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.pop(get_db, None)
        for item in reversed(self.patches): item.stop()
        self.engine.dispose()
        self.folder.cleanup()

    def test_license_counts_match_list_permissions_and_recent_contains_no_keys(self):
        for who, count, pending, completed in [('owner',4,2,1), ('other',1,1,0), ('reviewer',1,1,0), ('admin',5,3,1), ('manager',5,3,1)]:
            response = self.client.get('/api/dashboard', headers=self.headers[who])
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual((result['license_count'], result['pending_count'], result['completed_count']), (count,pending,completed), who)
            listing = self.client.get('/api/licenses', headers=self.headers[who]).json()
            self.assertEqual(result['license_count'], listing['total'])
            self.assertEqual(len(result['recent']), count)
            self.assertTrue(all(row['kind'] == 'license' for row in result['recent']))
            self.assertNotIn('must-not-be-selected-or-exposed', response.text)
            self.assertNotIn('license_key', response.text)

    def test_deleted_requests_leave_counts_and_tasks_without_deleting_history(self):
        response = self.client.delete(f'/api/licenses/{self.pending_id}', headers=self.headers['owner'])
        self.assertEqual(response.status_code, 200, response.text)
        result = self.client.get('/api/dashboard', headers=self.headers['owner']).json()
        self.assertEqual((result['license_count'],result['pending_count']), (3,1))
        tasks = self.client.get('/api/tasks', headers=self.headers['reviewer']).json()
        self.assertEqual((tasks['items'],tasks['total'],tasks['total_pages']), ([],0,1))
        with self.sessions() as db:
            self.assertIsNotNone(db.scalar(select(Approval).where(Approval.object_id == self.pending_id)))

    def test_admin_inbox_does_not_include_another_assignees_task(self):
        self.assertEqual(self.client.get('/api/tasks', headers=self.headers['admin']).json()['total'], 0)
        mine = self.client.get('/api/tasks', headers=self.headers['reviewer']).json()
        self.assertEqual(mine['total'], 1)
        self.assertEqual(mine['items'][0]['assignee_name'], 'reviewer')
