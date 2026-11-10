"""Self-service profile/password changes and their authorization boundaries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import AuditLog, User, UserRole
from app.rate_limit import RateDecision
from app.roles import ROLES, RETIRED_HEAD_ROLES
from app.security import hash_password, make_token, verify_password


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='yunwei-profile-')
        self.engine = create_engine('sqlite:///' + str(Path(self.folder.name) / 'profile.db'))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [patch('app.main.SessionLocal', self.sessions),
                        patch('app.main.check_request_rate', return_value=RateDecision())]
        for item in self.patches: item.start()
        def dependency():
            with self.sessions() as db: yield db
        app.dependency_overrides[get_db] = dependency
        self.client = TestClient(app)
        self.old_password = 'OriginalPass123!'
        self.new_password = 'ChangedPass456!'
        self.ids = {}
        with self.sessions() as db:
            for username, role, name in [('owner', 'applicant', '原姓名'), ('other', 'applicant', '其他人'), ('admin', 'admin', '管理员')]:
                user = User(username=username, display_name=name, department='研发部', role=role,
                            password_hash=hash_password(self.old_password), role_grants=[UserRole(role=role)])
                db.add(user); db.flush(); self.ids[username] = user.id
            db.commit()
        self.headers = {who: {'Authorization': 'Bearer ' + make_token(uid)} for who, uid in self.ids.items()}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        for item in reversed(self.patches): item.stop()
        self.engine.dispose(); self.folder.cleanup()

    def change_password(self, **changes):
        return self.client.post('/api/me/password', headers=self.headers['owner'],
            json={'current_password': self.old_password, 'new_password': self.new_password, **changes})

    def test_every_role_and_multiple_roles_can_change_own_name_and_password(self):
        accounts = []
        with self.sessions() as db:
            original_hash = db.get(User, self.ids['owner']).password_hash
            for index, grants in enumerate([[role] for role in ROLES] + [['admin', 'operator', 'license_manager']]):
                person = User(username=f'profile-role-{index}', display_name='角色成员', department='研发部',
                              role=grants[0], password_hash=original_hash, role_grants=[UserRole(role=role) for role in grants])
                db.add(person); db.flush()
                accounts.append((person.id, person.username, grants))
            db.commit()
        for uid, username, grants in accounts:
            with self.subTest(roles=grants):
                headers = {'Authorization': 'Bearer ' + make_token(uid)}
                response = self.client.patch('/api/me', headers=headers, json={'display_name': '修改后的姓名'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['id'], uid)
                self.assertEqual(response.json()['display_name'], '修改后的姓名')
                effective = set(grants) - RETIRED_HEAD_ROLES - {'department_head'}
                self.assertEqual(set(response.json()['roles']), effective)
                self.assertEqual(set(response.json()['direct_roles']), set(grants))
                self.assertEqual(response.json()['username'], username)
                wrong = self.client.post('/api/me/password', headers=headers,
                    json={'current_password': 'incorrect-password', 'new_password': self.new_password})
                self.assertEqual(wrong.status_code, 400, wrong.text)
                changed = self.client.post('/api/me/password', headers=headers,
                    json={'current_password': self.old_password, 'new_password': self.new_password})
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertEqual(self.client.post('/api/login', json={'username': username, 'password': self.old_password}).status_code, 401)
                login = self.client.post('/api/login', json={'username': username, 'password': self.new_password})
                self.assertEqual(login.status_code, 200, login.text)
                self.assertEqual(login.json()['user']['display_name'], '修改后的姓名')
                self.assertEqual(set(login.json()['user']['roles']), effective)
                with self.sessions() as db:
                    rows = db.scalars(select(AuditLog).where(AuditLog.record_kind == 'business', AuditLog.actor_id == uid)).all()
                    self.assertEqual({row.action for row in rows}, {'update', 'change_password'})
                    self.assertTrue(all(row.target_id == str(uid) for row in rows))

    def test_ordinary_member_can_change_own_name_and_history_keeps_original_actor(self):
        response = self.client.patch('/api/me', headers=self.headers['owner'], json={'display_name': '  新姓名  '})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['display_name'], '新姓名')
        self.assertEqual(response.json()['roles'], ['applicant'])
        self.assertNotIn('password_hash', response.json())
        self.assertEqual(self.client.get('/api/me', headers=self.headers['owner']).json()['display_name'], '新姓名')
        self.assertEqual(self.client.get('/api/me', headers=self.headers['other']).json()['display_name'], '其他人')
        login = self.client.post('/api/login', json={'username': 'owner', 'password': self.old_password})
        self.assertEqual(login.json()['user']['display_name'], '新姓名')
        self.client.patch('/api/me', headers=self.headers['owner'], json={'display_name': '新姓名'})
        with self.sessions() as db:
            rows = db.scalars(select(AuditLog).where(AuditLog.record_kind == 'business')).all()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual((row.actor_id, row.actor_name, row.target_id), (self.ids['owner'], '原姓名', str(self.ids['owner'])))
            self.assertEqual(json.loads(row.changes), [{'field': 'display_name', 'label': '姓名', 'before': '原姓名', 'after': '新姓名'}])
            self.assertTrue(verify_password(self.old_password, db.get(User, self.ids['owner']).password_hash))

    def test_self_service_cannot_change_account_grants_or_target_another_user(self):
        extras = {'id': self.ids['other'], 'user_id': self.ids['other'], 'username': 'hijacked', 'department': '管理层',
                  'roles': ['admin'], 'role': 'admin', 'active': False, 'feishu_open_id': 'ou_other', 'password': self.new_password}
        for field, value in extras.items():
            with self.subTest(field=field):
                response = self.client.patch('/api/me', headers=self.headers['owner'], json={'display_name': '新姓名', field: value})
                self.assertEqual(response.status_code, 422, response.text)
        for uid in [self.ids['owner'], self.ids['other']]:
            response = self.client.patch(f'/api/users/{uid}', headers=self.headers['owner'], json={'display_name': '新姓名', 'password': self.new_password})
            self.assertEqual(response.status_code, 403, response.text)
        forged = self.change_password(user_id=self.ids['other'])
        self.assertEqual(forged.status_code, 422, forged.text)
        with self.sessions() as db:
            owner = db.get(User, self.ids['owner'])
            self.assertEqual((owner.display_name, owner.username, owner.department, owner.active), ('原姓名', 'owner', '研发部', True))
            self.assertEqual([r.role for r in owner.role_grants], ['applicant'])
            self.assertIsNone(db.scalar(select(AuditLog).where(AuditLog.record_kind == 'business')))

    def test_password_change_verifies_current_password_hashes_new_value_and_redacts_history(self):
        response = self.change_password()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(self.new_password, response.text)
        self.assertEqual(self.client.post('/api/login', json={'username': 'owner', 'password': self.old_password}).status_code, 401)
        login = self.client.post('/api/login', json={'username': 'owner', 'password': self.new_password})
        self.assertEqual(login.status_code, 200, login.text)
        # Password changes preserve the current session; later logins use the new password.
        self.assertEqual(self.client.get('/api/me', headers=self.headers['owner']).status_code, 200)
        self.assertEqual(self.client.post('/api/login', json={'username': 'other', 'password': self.old_password}).status_code, 200)
        with self.sessions() as db:
            owner = db.get(User, self.ids['owner'])
            self.assertTrue(verify_password(self.new_password, owner.password_hash))
            self.assertNotEqual(owner.password_hash, self.new_password)
            row = db.scalar(select(AuditLog).where(AuditLog.record_kind == 'business'))
            self.assertEqual((row.action, row.actor_id, row.target_id), ('change_password', self.ids['owner'], str(self.ids['owner'])))
            self.assertEqual(json.loads(row.changes), [{'field': 'password', 'label': '密码', 'before': None, 'after': '已设置 / 已更新'}])
            serialized = '\n'.join((r.changes or '') + r.summary + r.path for r in db.scalars(select(AuditLog)))
            for secret in [self.old_password, self.new_password, owner.password_hash]: self.assertNotIn(secret, serialized)

    def test_invalid_names_passwords_and_incorrect_current_password_have_no_effect(self):
        for value in ['', ' \t\n ', '名' * 81, None]:
            response = self.client.patch('/api/me', headers=self.headers['owner'], json={'display_name': value})
            self.assertEqual(response.status_code, 422, response.text)
        wrong = self.change_password(current_password='wrong-password')
        self.assertEqual(wrong.status_code, 400, wrong.text)
        self.assertEqual(wrong.json()['detail'], '当前密码不正确，请重新输入')
        same = self.change_password(new_password=self.old_password)
        self.assertEqual(same.status_code, 400, same.text)
        for value in ['Aa1!short', 'abcdefghijkl', 'Abcdefghijkl', '中文中文中文中文1234', 'Aa1!' * 33]:
            response = self.change_password(new_password=value)
            self.assertEqual(response.status_code, 422, response.text)
        with self.sessions() as db:
            owner = db.get(User, self.ids['owner'])
            self.assertEqual(owner.display_name, '原姓名')
            self.assertTrue(verify_password(self.old_password, owner.password_hash))
            self.assertIsNone(db.scalar(select(AuditLog).where(AuditLog.record_kind == 'business')))

    def test_authentication_and_active_account_are_required_for_both_endpoints(self):
        for headers in [{}, {'Authorization': 'Bearer invalid'}]:
            self.assertEqual(self.client.patch('/api/me', headers=headers, json={'display_name': '新姓名'}).status_code, 401)
            self.assertEqual(self.client.post('/api/me/password', headers=headers, json={'current_password': self.old_password, 'new_password': self.new_password}).status_code, 401)
        with self.sessions() as db:
            db.get(User, self.ids['owner']).active = False; db.commit()
        response = self.change_password()
        self.assertEqual(response.status_code, 401, response.text)
        self.assertIn('此账号已停用', response.json()['detail'])
        response = self.client.patch('/api/me', headers=self.headers['owner'], json={'display_name': '新姓名'})
        self.assertEqual(response.status_code, 401, response.text)

    def test_audit_failure_rolls_back_profile_and_password_changes(self):
        with patch('app.main.operation_audit.record', side_effect=HTTPException(503, '日志事务失败')):
            name = self.client.patch('/api/me', headers=self.headers['owner'], json={'display_name': '新姓名'})
            password = self.change_password()
        self.assertEqual(name.status_code, 503, name.text)
        self.assertEqual(password.status_code, 503, password.text)
        with self.sessions() as db:
            owner = db.get(User, self.ids['owner'])
            self.assertEqual(owner.display_name, '原姓名')
            self.assertTrue(verify_password(self.old_password, owner.password_hash))


if __name__ == '__main__': unittest.main()
