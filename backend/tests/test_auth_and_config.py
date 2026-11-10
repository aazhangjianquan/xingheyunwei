"""Isolated configuration and authentication checks; no external messages are sent."""
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
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
    @contextmanager
    def task_login(self, *, base='http://testserver'):
        original = self.client.get('/api/admin/settings', headers=self.admin).json()
        values = {'feishu_task_login_enabled': True, 'public_base_url': base}
        self.assertEqual(self.client.put('/api/admin/settings', headers=self.admin,
                         json={'values': values}).status_code, 200)
        browser = TestClient(app, base_url=base, headers={'User-Agent': 'Mozilla/5.0 Feishu/7.40.0'})
        try:
            yield browser
        finally:
            browser.close()
            self.client.put('/api/admin/settings', headers=self.admin,
                            json={'values': {key: original[key] for key in values}})

    def start_task_login(self, browser, path='/resource/7'):
        result = browser.get('/api/auth/feishu/task/start', params={'next': path}, follow_redirects=False)
        self.assertEqual(result.status_code, 303)
        url = urlsplit(result.headers['location'])
        self.assertEqual(url.netloc, 'accounts.feishu.cn')
        params = parse_qs(url.query)
        self.assertEqual(params['client_id'], ['cli_test_app'])
        self.assertTrue(params['redirect_uri'][0].endswith('/api/auth/feishu/callback'))
        return params['state'][0], result

    def finish_task_login(self, browser, state, *, open_id='ou_applicant'):
        with patch('app.auth_feishu.feishu_identity', return_value=open_id):
            result = browser.get('/api/auth/feishu/callback', params={'state': state, 'code': 'official-code'},
                                 follow_redirects=False)
        fragment = parse_qs(urlsplit(result.headers['location']).fragment)
        return fragment, result

    def test_task_login_exchanges_actual_identity_preserves_task_and_keeps_business_permissions(self):
        from app.models import LoginTicket, OAuthState
        with self.task_login() as browser:
            state, start = self.start_task_login(browser)
            self.assertIn('HttpOnly', start.headers['set-cookie'])
            self.assertIn('SameSite=lax', start.headers['set-cookie'])
            fragment, callback = self.finish_task_login(browser, state)
            self.assertEqual(urlsplit(callback.headers['location']).path, '/resource/7')
            self.assertEqual(urlsplit(callback.headers['location']).query, '')
            ticket = fragment['feishu_ticket'][0]
            result = browser.post('/api/auth/ticket', json={'ticket': ticket}, headers={'Origin': 'http://testserver'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['user']['username'], 'applicant')
            self.assertEqual(result.json()['next'], '/resource/7')
            self.assertNotIn('ou_applicant', result.text)
            headers = {'Authorization': 'Bearer ' + result.json()['token']}
            self.assertEqual(browser.get('/api/me', headers=headers).json()['username'], 'applicant')
            self.assertEqual(browser.get('/api/admin/settings', headers=headers).status_code, 403)
            # 临时票据、授权码和完整身份均不以明文保存在状态表中。
            with SessionLocal() as db:
                self.assertIsNone(db.get(OAuthState, state))
                self.assertIsNone(db.get(LoginTicket, ticket))

    def test_task_login_external_browser_and_unsafe_redirects(self):
        with self.task_login() as browser:
            result = browser.get('/api/auth/feishu/task/start', params={'next': '/resource/7'},
                                 headers={'User-Agent': 'Mozilla/5.0 Chrome/130.0'}, follow_redirects=False)
            self.assertEqual(result.headers['location'], 'http://testserver/resource/7')
            self.assertNotIn('set-cookie', result.headers)
            for target in ['https://evil.example', '//evil.example', '/resource/7?next=https://evil.example',
                           '/resource/7#bad', '/resource/../settings', '/resource/0', '/api/admin/settings',
                           '/resource/7\\evil', '/licenses/7%0aLocation:evil']:
                result = browser.get('/api/auth/feishu/task/start', params={'next': target}, follow_redirects=False)
                self.assertEqual(result.status_code, 400, target)

    def test_task_login_old_ip_entry_moves_to_public_domain_before_creating_browser_cookie(self):
        with self.task_login(base='https://ops.example.test') as browser:
            result = browser.get('/api/auth/feishu/task/start', params={'next': '/resource/7'},
                                 headers={'Host': '101.126.67.61'}, follow_redirects=False)
            self.assertEqual(result.status_code, 303)
            self.assertEqual(result.headers['location'],
                             'https://ops.example.test/api/auth/feishu/task/start?next=%2Fresource%2F7')
            self.assertNotIn('set-cookie', result.headers)
            state, start = self.start_task_login(browser)
            self.assertIn('Secure', start.headers['set-cookie'])
            fragment, _ = self.finish_task_login(browser, state)
            result = browser.post('/api/auth/ticket', json={'ticket': fragment['feishu_ticket'][0]},
                                  headers={'Origin': 'https://ops.example.test'})
            self.assertEqual(result.status_code, 200)

    def test_task_login_state_is_browser_bound_expiring_and_single_use(self):
        from app.models import OAuthState
        with self.task_login() as browser:
            state, _ = self.start_task_login(browser)
            with TestClient(app) as other:
                result = other.get('/api/auth/feishu/callback', params={'state': state, 'code': 'stolen'}, follow_redirects=False)
                self.assertIn('feishu_error=expired', result.headers['location'])
            fragment, _ = self.finish_task_login(browser, state)
            self.assertIn('feishu_ticket', fragment)
            # 保存原浏览器 cookie 验证 state 原子消费，而不是仅依赖 cookie 已变化。
            state, _ = self.start_task_login(browser)
            original_cookie = browser.cookies.get('yunwei_feishu_task')
            self.finish_task_login(browser, state)
            browser.cookies.set('yunwei_feishu_task', original_cookie, domain='testserver.local', path='/api/auth')
            fragment, _ = self.finish_task_login(browser, state)
            self.assertEqual(fragment, {'feishu_error': ['expired']})
            state, _ = self.start_task_login(browser)
            with SessionLocal() as db:
                from app.auth_feishu import digest
                db.get(OAuthState, digest(state)).expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
                db.commit()
            fragment, _ = self.finish_task_login(browser, state)
            self.assertEqual(fragment, {'feishu_error': ['expired']})

    def test_task_login_ticket_is_origin_browser_bound_and_single_use(self):
        with self.task_login() as browser:
            state, _ = self.start_task_login(browser)
            fragment, _ = self.finish_task_login(browser, state)
            ticket = fragment['feishu_ticket'][0]
            body = {'ticket': ticket}
            cookie = browser.cookies.get('yunwei_feishu_task')
            for origin in ['https://evil.example', 'null', '']:
                denied = browser.post('/api/auth/ticket', json=body, headers={'Origin': origin})
                self.assertEqual(denied.status_code, 403)
            with TestClient(app) as other:
                denied = other.post('/api/auth/ticket', json=body, headers={'Origin': 'http://testserver'})
                self.assertEqual(denied.status_code, 400)
            self.assertEqual(browser.post('/api/auth/ticket', json=body, headers={'Origin': 'http://testserver'}).status_code, 200)
            browser.cookies.set('yunwei_feishu_task', cookie, domain='testserver.local', path='/api/auth')
            replay = browser.post('/api/auth/ticket', json=body, headers={'Origin': 'http://testserver'})
            self.assertEqual(replay.status_code, 401)

    def test_task_login_parallel_exchange_issues_exactly_one_session(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        with self.task_login() as browser:
            state, _ = self.start_task_login(browser)
            fragment, _ = self.finish_task_login(browser, state)
            cookie = browser.cookies.get('yunwei_feishu_task')
            barrier = Barrier(2)
            def consume():
                with TestClient(app) as other:
                    barrier.wait(timeout=10)
                    return other.post('/api/auth/ticket', json={'ticket': fragment['feishu_ticket'][0]},
                        headers={'Origin': 'http://testserver', 'Cookie': 'yunwei_feishu_task=' + cookie}).status_code
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: consume(), range(2)))
            self.assertEqual(sorted(results), [200, 401])

    def test_task_login_another_authenticated_browser_cannot_use_stolen_state_or_ticket(self):
        with self.task_login() as browser:
            state, _ = self.start_task_login(browser)
            other = TestClient(app, headers={'User-Agent': 'Lark/7.40.0'})
            try:
                self.start_task_login(other)
                wrong, _ = self.finish_task_login(other, state)
                self.assertEqual(wrong, {'feishu_error': ['expired']})
                fragment, _ = self.finish_task_login(browser, state)
                self.start_task_login(other)
                denied = other.post('/api/auth/ticket', json={'ticket': fragment['feishu_ticket'][0]},
                                    headers={'Origin': 'http://testserver'})
                self.assertEqual(denied.status_code, 401)
                allowed = browser.post('/api/auth/ticket', json={'ticket': fragment['feishu_ticket'][0]},
                                       headers={'Origin': 'http://testserver'})
                self.assertEqual(allowed.status_code, 200)
            finally:
                other.close()

    def test_task_login_forwarded_message_logs_in_as_actual_viewer(self):
        with SessionLocal() as db:
            person = db.scalar(select(User).where(User.username == 'operator'))
            original = person.feishu_open_id
            person.feishu_open_id = 'ou_actual_operator'
            db.commit()
        try:
            with self.task_login() as browser:
                state, _ = self.start_task_login(browser)
                fragment, _ = self.finish_task_login(browser, state, open_id='ou_actual_operator')
                result = browser.post('/api/auth/ticket', json={'ticket': fragment['feishu_ticket'][0]},
                                      headers={'Origin': 'http://testserver'})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()['user']['username'], 'operator')
        finally:
            with SessionLocal() as db:
                db.scalar(select(User).where(User.username == 'operator')).feishu_open_id = original
                db.commit()

    def test_task_login_callback_failure_is_recoverable_and_does_not_issue_ticket(self):
        from fastapi import HTTPException
        with self.task_login() as browser:
            state, _ = self.start_task_login(browser)
            with patch('app.auth_feishu.feishu_identity', side_effect=HTTPException(502, '认证失败')):
                result = browser.get('/api/auth/feishu/callback', params={'state': state, 'code': 'invalid-code'},
                                     follow_redirects=False)
            self.assertEqual(result.status_code, 303)
            self.assertEqual(result.headers['location'], 'http://testserver/resource/7#feishu_error=verification')
            state, _ = self.start_task_login(browser)
            fragment, _ = self.finish_task_login(browser, state)
            self.assertIn('feishu_ticket', fragment)

    def test_task_login_denial_unbound_and_disabled_accounts_do_not_get_tickets(self):
        with self.task_login() as browser:
            state, _ = self.start_task_login(browser)
            result = browser.get('/api/auth/feishu/callback', params={'state': state, 'error': 'access_denied'}, follow_redirects=False)
            self.assertIn('feishu_error=denied', result.headers['location'])
            state, _ = self.start_task_login(browser)
            fragment, _ = self.finish_task_login(browser, state, open_id='ou_not_bound')
            self.assertEqual(fragment, {'feishu_error': ['unbound']})
            with SessionLocal() as db:
                person = db.scalar(select(User).where(User.username == 'applicant'))
                person.active = False
                db.commit()
            try:
                state, _ = self.start_task_login(browser)
                fragment, _ = self.finish_task_login(browser, state)
                self.assertEqual(fragment, {'feishu_error': ['disabled']})
            finally:
                with SessionLocal() as db:
                    db.scalar(select(User).where(User.username == 'applicant')).active = True
                    db.commit()

    def test_task_login_rechecks_identity_binding_and_account_at_exchange(self):
        for changes in [{'active': False}, {'feishu_open_id': None}, {'feishu_open_id': 'ou_new_binding'}]:
            with self.subTest(changes=changes), self.task_login() as browser:
                state, _ = self.start_task_login(browser)
                fragment, _ = self.finish_task_login(browser, state)
                with SessionLocal() as db:
                    person = db.scalar(select(User).where(User.username == 'applicant'))
                    for key, value in changes.items(): setattr(person, key, value)
                    db.commit()
                try:
                    result = browser.post('/api/auth/ticket', json={'ticket': fragment['feishu_ticket'][0]},
                                          headers={'Origin': 'http://testserver'})
                    self.assertEqual(result.status_code, 403)
                finally:
                    with SessionLocal() as db:
                        person = db.scalar(select(User).where(User.username == 'applicant'))
                        person.active, person.feishu_open_id = True, 'ou_applicant'
                        db.commit()

    def test_task_login_expired_ticket_and_disabled_switch_cannot_issue_session(self):
        from app.models import LoginTicket
        from app.auth_feishu import digest
        with self.task_login() as browser:
            state, _ = self.start_task_login(browser)
            fragment, _ = self.finish_task_login(browser, state)
            ticket = fragment['feishu_ticket'][0]
            with SessionLocal() as db:
                key = digest(ticket + ':' + browser.cookies.get('yunwei_feishu_task').strip('"'))
                db.get(LoginTicket, key).expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
                db.commit()
            self.assertEqual(browser.post('/api/auth/ticket', json={'ticket': ticket}, headers={'Origin': 'http://testserver'}).status_code, 401)
            state, _ = self.start_task_login(browser)
            fragment, _ = self.finish_task_login(browser, state)
            self.client.put('/api/admin/settings', headers=self.admin, json={'values': {'feishu_task_login_enabled': False}})
            self.assertEqual(browser.post('/api/auth/ticket', json={'ticket': fragment['feishu_ticket'][0]},
                             headers={'Origin': 'http://testserver'}).status_code, 410)
            fallback = browser.get('/api/auth/feishu/task/start', params={'next': '/resource/7'}, follow_redirects=False)
            self.assertIn('feishu_error=unavailable', fallback.headers['location'])

    def test_task_login_https_cookie_and_restart_keep_independent_switch(self):
        with self.task_login(base='https://testserver') as browser:
            _, result = self.start_task_login(browser, '/licenses/9')
            self.assertIn('Secure', result.headers['set-cookie'])
            self.assertEqual(self.client.get('/api/admin/settings', headers=self.admin).json()['feishu_task_callback_url'],
                             'https://testserver/api/auth/feishu/callback')
            with TestClient(app) as restarted:
                options = restarted.get('/api/auth/options').json()
                self.assertTrue(options['feishu_task_login_enabled'])
                self.assertFalse(options['feishu_login_enabled'])
                self.assertTrue(options['local_login_enabled'])

    def test_task_login_uses_official_oauth_endpoints_and_sanitizes_failures(self):
        from app.auth_feishu import feishu_identity
        config = {'feishu_app_id': 'cli_test', 'feishu_app_secret': 'secret', 'feishu_tenant_key': ''}
        with patch('app.auth_feishu.httpx.Client') as transport:
            client = transport.return_value.__enter__.return_value
            client.post.return_value.json.return_value = {'code': 0, 'access_token': 'user-secret'}
            client.get.return_value.json.return_value = {'code': 0, 'data': {'open_id': 'ou_applicant'}}
            self.assertEqual(feishu_identity(config, 'official-code', 'https://ops.example/callback'), 'ou_applicant')
            self.assertTrue(client.post.call_args.args[0].endswith('/authen/v2/oauth/token'))
            self.assertEqual(client.post.call_args.kwargs['json']['code'], 'official-code')
            self.assertEqual(client.get.call_args.kwargs['headers'], {'Authorization': 'Bearer user-secret'})
            client.get.return_value.json.return_value = {'code': 999, 'msg': 'user-secret official-code'}
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as failure:
                feishu_identity(config, 'official-code', 'https://ops.example/callback')
            self.assertNotIn('user-secret', failure.exception.detail)
            self.assertNotIn('official-code', failure.exception.detail)

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
            from app.models import DepartmentManager, DepartmentMembership
            from app import organization as org
            owner=db.scalar(select(User).where(User.username=='applicant'))
            head=db.scalar(select(User).where(User.username=='dept'))
            if owner.department_id not in org.membership_ids(head):
                db.add(DepartmentMembership(user_id=head.id,department_id=owner.department_id))
            if not db.get(DepartmentManager,(owner.department_id,head.id)):
                db.add(DepartmentManager(department_id=owner.department_id,user_id=head.id))
            db.commit()
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
            self.assertEqual(set(response.json()), {'site_name', 'feishu_login_enabled', 'local_login_enabled', 'feishu_task_login_enabled'})
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
            result = self.client.request(method, path, headers=self.applicant,
                                         **({'json': {'ticket': 'unused-ticket-' * 3}} if method == 'POST' and path.endswith('/ticket') else {}))
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
                self.assertIn('任务免密请使用独立开关', blocked.json()['detail'])
            self.assertEqual(self.client.post('/api/login', json={'username':'applicant','password':'Demo@2026'}).status_code, 200)
        finally:
            self.client.put('/api/admin/settings', headers=self.admin, json={'values':{'feishu_notifications_enabled':False}})

    def test_feishu_credential_check_explains_message_permissions_without_oauth_setup(self):
        with patch('app.admin.httpx.Client') as transport:
            transport.return_value.__enter__.return_value.post.return_value.json.return_value = {'code':0,'tenant_access_token':'test-token'}
            result = self.client.post('/api/admin/feishu/check', headers=self.admin)
        self.assertEqual(result.status_code, 200)
        self.assertIn('授权回调地址', result.json()['message'])


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
        for username in ["dept", "opslead"]:
            self.assertEqual(submit(self.login(username), "resource", item_id, {"action": "approve"}).status_code, 200)
        detail = self.client.get(f"/api/resources/{item_id}", headers=self.admin).json()
        approval = next(a for a in detail["approvals"] if a["action"] == "pending")
        with SessionLocal() as db:
            new_operator = db.scalar(select(User).where(User.username == "operator2"))
            new_operator_id = new_operator.id
            from app.models import ProjectMember
            db.add(ProjectMember(project_id=self.project_id,user_id=new_operator_id,role='operator'));db.commit()
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
