"""Isolated configuration and authentication checks; no external messages are sent."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

temporary = tempfile.TemporaryDirectory(prefix="yunwei-auth-check-")
os.environ["DATABASE_URL"] = "sqlite:///" + temporary.name + "/auth.db"
os.environ["INSTANCE_DIR"] = temporary.name + "/instance"
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.database import get_db
from app.main import app
from app.models import SystemSetting, User, Project

# Use our own engine even when another test imported app.database first.
auth_engine = create_engine('sqlite:///' + temporary.name + '/isolated-auth.db')
SessionLocal = sessionmaker(auth_engine, expire_on_commit=False)

from app.rate_limit import RateDecision


class AuthConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patches = [patch('app.main.engine', auth_engine), patch('app.main.SessionLocal', SessionLocal),
                       patch('app.auth_feishu.SessionLocal', SessionLocal), patch('app.feishu.SessionLocal', SessionLocal),
                       patch('app.admin.engine', auth_engine),
                       patch('app.main.check_request_rate', return_value=RateDecision())]
        for item in cls.patches: item.start()
        def dependency():
            with SessionLocal() as db: yield db
        app.dependency_overrides[get_db] = dependency
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.admin = cls.login("admin")
        cls.applicant = cls.login("applicant")
        with SessionLocal() as db:
            cls.project_id = db.scalar(select(Project.id).where(Project.name == "星河数据分析平台"))
        result = cls.client.put("/api/admin/settings", headers=cls.admin, json={"values": {
            "public_base_url": "http://testserver", "feishu_app_id": "cli_test_app",
            "feishu_app_secret": "test-app-secret", "feishu_notifications_enabled": False,
        }})
        assert result.status_code == 200, result.text
        with SessionLocal() as db:
            person = db.scalar(select(User).where(User.username == "applicant"))
            person.feishu_open_id = "ou_applicant"
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        app.dependency_overrides.pop(get_db, None)
        for item in reversed(cls.patches): item.stop()
        auth_engine.dispose()

    @classmethod
    def login(cls, username):
        response = cls.client.post("/api/login", json={"username": username, "password": "Demo@2026"})
        assert response.status_code == 200, response.text
        return {"Authorization": "Bearer " + response.json()["token"]}


    def test_restart_keeps_admin_configured_brand_and_session(self):
        original = self.client.get("/api/admin/settings", headers=self.admin).json()["site_name"]
        try:
            for name in ["元知星辰", "云维审计", "星河运维协助平台"]:
                result = self.client.put("/api/admin/settings", headers=self.admin, json={"values": {"site_name": name}})
                self.assertEqual(result.status_code, 200)
                with TestClient(app) as restarted:
                    self.assertEqual(restarted.get("/api/health").status_code, 200)
                    self.assertEqual(restarted.get("/api/auth/options").json()["site_name"], name)
                    self.assertEqual(restarted.get("/api/me", headers=self.admin).status_code, 200)
        finally:
            self.client.put("/api/admin/settings", headers=self.admin, json={"values": {"site_name": original}})

    def test_standalone_api_has_no_html_or_static_asset_fallback(self):
        for path in ['/projects', '/wiki', '/resources/1', '/assets/old.js']:
            result = self.client.get(path)
            self.assertEqual(result.status_code, 404)
            self.assertNotIn('text/html', result.headers.get('content-type', ''))

    def test_public_settings_stay_current_without_html_bootstrap_or_secrets(self):
        original = self.client.get('/api/auth/options').json()['site_name']
        try:
            name = '</script><script>alert("test")</script>&<title>'
            self.client.put('/api/admin/settings', headers=self.admin, json={'values': {'site_name': name}})
            response = self.client.get('/api/auth/options')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['site_name'], name)
            self.assertEqual(set(response.json()), {'site_name', 'feishu_login_enabled', 'local_login_enabled'})
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertNotIn('test-app-secret', response.text)
        finally:
            self.client.put('/api/admin/settings', headers=self.admin, json={'values': {'site_name': original}})

    def test_secret_is_encrypted_masked_and_retained(self):
        result = self.client.get("/api/admin/settings", headers=self.admin)
        self.assertTrue(result.json()["feishu_app_secret_set"])
        self.assertNotIn("test-app-secret", result.text)
        with SessionLocal() as db:
            row = db.get(SystemSetting, "feishu_app_secret")
            self.assertTrue(row.is_secret)
            self.assertNotIn("test-app-secret", row.value)
        response = self.client.put("/api/admin/settings", headers=self.admin, json={"values": {"feishu_app_secret": ""}})
        self.assertTrue(response.json()["feishu_app_secret_set"])

    def test_members_cannot_read_or_write_settings(self):
        self.assertEqual(self.client.get("/api/admin/settings", headers=self.applicant).status_code, 403)
        self.assertEqual(self.client.put("/api/admin/settings", headers=self.applicant, json={"values": {"site_name": "bad"}}).status_code, 403)

    def test_feishu_is_notifications_only_and_legacy_login_flags_are_migrated(self):
        with SessionLocal() as db:
            for key, value in {"feishu_login_enabled": True, "local_login_enabled": False}.items():
                db.get(SystemSetting, key).value = json.dumps(value)
            db.commit()
        # Old database flags cannot hide the password login or reopen OAuth.
        options = self.client.get('/api/auth/options').json()
        self.assertFalse(options['feishu_login_enabled'])
        self.assertTrue(options['local_login_enabled'])
        self.assertEqual(self.client.post('/api/login', json={'username':'applicant','password':'Demo@2026'}).status_code, 200)
        with TestClient(app) as restarted:
            self.assertEqual(restarted.get('/api/me', headers=self.admin).status_code, 200)
        with SessionLocal() as db:
            self.assertEqual(db.get(SystemSetting, 'feishu_login_enabled').value, 'false')
            self.assertEqual(db.get(SystemSetting, 'local_login_enabled').value, 'true')
            self.assertEqual(db.scalar(select(User).where(User.username == 'applicant')).feishu_open_id, 'ou_applicant')
        for method, path in [('GET','/api/auth/feishu/start'),('POST','/api/auth/feishu/bind'),('GET','/api/auth/feishu/callback'),('POST','/api/auth/ticket')]:
            result = self.client.request(method, path, headers=self.applicant)
            self.assertEqual(result.status_code, 410)
            self.assertIn('账号密码登录', result.json()['detail'])

    def test_notification_settings_do_not_require_oauth_and_cannot_disable_password_login(self):
        result = self.client.put('/api/admin/settings', headers=self.admin, json={'values':{'feishu_notifications_enabled':True}})
        self.assertEqual(result.status_code, 200, result.text)
        try:
            self.assertTrue(result.json()['feishu_notifications_enabled'])
            self.assertFalse(result.json()['feishu_login_enabled'])
            self.assertNotIn('feishu_callback_url', result.json())
            self.assertNotIn('feishu_tenant_key', result.json())
            for changes in [{'feishu_login_enabled':True},{'local_login_enabled':False}]:
                blocked = self.client.put('/api/admin/settings', headers=self.admin, json={'values':changes})
                self.assertEqual(blocked.status_code, 400)
                self.assertIn('飞书仅用于消息通知', blocked.json()['detail'])
            self.assertEqual(self.client.post('/api/login', json={'username':'applicant','password':'Demo@2026'}).status_code, 200)
        finally:
            self.client.put('/api/admin/settings', headers=self.admin, json={'values':{'feishu_notifications_enabled':False}})

    def test_feishu_credential_check_explains_message_permissions_without_oauth_setup(self):
        with patch('app.admin.httpx.Client') as transport:
            transport.return_value.__enter__.return_value.post.return_value.json.return_value = {'code':0,'tenant_access_token':'test-token'}
            result = self.client.post('/api/admin/feishu/check', headers=self.admin)
        self.assertEqual(result.status_code, 200)
        self.assertIn('无需配置登录回调地址', result.json()['message'])


    def test_disabled_password_account_and_existing_session_report_the_actual_reason(self):
        with SessionLocal() as db:
            uid = db.scalar(select(User.id).where(User.username == "applicant"))
        self.assertEqual(self.client.patch(f"/api/users/{uid}", headers=self.admin, json={"active": False}).status_code, 200)
        try:
            for password in ["Demo@2026", "wrong-password"]:
                result = self.client.post("/api/login", json={"username": "applicant", "password": password})
                self.assertEqual(result.status_code, 403)
                self.assertEqual(result.json(), {"detail": "此账号已停用，请联系管理员启用后再登录"})
            for path in ["/api/me", "/api/projects"]:
                result = self.client.get(path, headers=self.applicant)
                self.assertEqual(result.status_code, 401)
                self.assertEqual(result.json()["detail"], "此账号已停用，请联系管理员启用后再登录")
        finally:
            self.client.patch(f"/api/users/{uid}", headers=self.admin, json={"active": True})
        self.assertEqual(self.client.post("/api/login", json={"username": "applicant", "password": "Demo@2026"}).status_code, 200)

    def test_bad_credentials_still_report_credential_errors(self):
        for username in ["applicant", "nonexistent-account"]:
            result = self.client.post("/api/login", json={"username": username, "password": "wrong-password"})
            self.assertEqual(result.status_code, 401)
            self.assertEqual(result.json(), {"detail": "用户名或密码错误"})


    def test_admin_reassignment_changes_actual_operator_permission(self):
        def submit(headers, kind, item_id, body):
            d = self.client.get(f"/api/resources/{item_id}", headers=self.admin).json()
            task = next(a for a in d["approvals"] if a["action"] == "pending")
            return self.client.post(f"/api/{kind}/{item_id}/action", headers=headers, json={**body,"task_id":task["id"]})
        response = self.client.post("/api/resources", headers=self.applicant, json={"project_id":self.project_id,"project_name": "转交审批验证", "environment": "测试", "resource_type": "Redis", "specification": "1GB", "start_date": "2026-10-26", "end_date": "2027-10-26", "purpose": "验证审批转交", "capacity_basis": "10 个用户"})
        self.assertEqual(response.status_code, 200)
        item_id = response.json()["id"]
        for username in ["dept", "opslead", "leader"]:
            self.assertEqual(submit(self.login(username), "resource", item_id, {"action": "approve"}).status_code, 200)
        detail = self.client.get(f"/api/resources/{item_id}", headers=self.admin).json()
        approval = next(a for a in detail["approvals"] if a["action"] == "pending")
        with SessionLocal() as db:
            new_operator = db.scalar(select(User).where(User.username == "operator2"))
            new_operator_id = new_operator.id
        result = self.client.post(f"/api/admin/approvals/{approval['id']}/reassign", headers=self.admin, json={"assignee_id": new_operator_id,"reason":"回归验收转交"})
        self.assertEqual(result.status_code, 200)
        old = submit(self.login("operator"), "resource", item_id, {"action": "approve", "fulfillment": "不应成功"})
        self.assertEqual(old.status_code, 403)
        new = submit(self.login("operator2"), "resource", item_id, {"action": "approve", "fulfillment": "新处理人回填成功"})
        self.assertEqual(new.status_code, 200)
        self.assertEqual(new.json()["status"], "completed")

    def test_rejected_task_cannot_be_reused(self):
        response = self.client.post("/api/resources", headers=self.applicant, json={"project_id":self.project_id,"project_name": "驳回流程验证", "environment": "测试", "resource_type": "ECS", "specification": "2核4GB", "start_date": "2026-10-26", "end_date": "2027-10-26", "purpose": "验证驳回", "capacity_basis": "10个用户"})
        item_id = response.json()["id"]
        task = next(a for a in self.client.get(f"/api/resources/{item_id}", headers=self.admin).json()["approvals"] if a["action"] == "pending")
        dept = self.login("dept")
        result = self.client.post(f"/api/resource/{item_id}/action", headers=dept, json={"task_id":task["id"],"action": "reject", "comment": "容量依据需补充"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "rejected")
        repeat = self.client.post(f"/api/resource/{item_id}/action", headers=dept, json={"task_id":task["id"],"action": "approve"})
        self.assertEqual(repeat.status_code, 409)

    def test_first_template_upgrade_preserves_existing_records(self):
        from sqlalchemy import create_engine, inspect, text
        from app.migrations import upgrade
        old = create_engine("sqlite:///:memory:")
        try:
            with old.begin() as conn:
                conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(80), feishu_open_id VARCHAR(100))"))
                conn.execute(text("CREATE TABLE approvals (id INTEGER PRIMARY KEY, actor_id INTEGER)"))
                conn.execute(text("INSERT INTO users VALUES (1, 'existing-user', '')"))
                conn.execute(text("INSERT INTO approvals VALUES (1, 1)"))
            upgrade(old)
            upgrade(old)  # 第二次升级不应重复加列或索引。
            self.assertIn("assignee_id", {c["name"] for c in inspect(old).get_columns("approvals")})
            with old.connect() as conn:
                self.assertEqual(conn.execute(text("SELECT username FROM users WHERE id=1")).scalar(), "existing-user")
                self.assertIsNone(conn.execute(text("SELECT feishu_open_id FROM users WHERE id=1")).scalar())
                self.assertEqual(conn.execute(text("SELECT actor_id FROM approvals WHERE id=1")).scalar(), 1)
        finally:
            old.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
