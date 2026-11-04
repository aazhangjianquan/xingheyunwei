"""Query growth, permission parity and durable non-blocking audit storage."""
import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from starlette.responses import Response
from app.database import get_db
from app import main
from app.models import AuditLog, Project, ProjectMember, User, UserRole
from app.security import make_token

from app.rate_limit import RateDecision


class StartupQueriesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='yunwei-startup-check-')
        cls.engine = create_engine('sqlite:///' + cls.folder.name + '/startup.db')
        cls.sessions = sessionmaker(cls.engine, expire_on_commit=False)
        cls.patches = [patch('app.main.engine', cls.engine), patch('app.main.SessionLocal', cls.sessions),
                       patch('app.auth_feishu.SessionLocal', cls.sessions),
                       patch('app.main.check_request_rate', return_value=RateDecision())]
        for p in cls.patches: p.start()
        def dependency():
            with cls.sessions() as db: yield db
        main.app.dependency_overrides[get_db] = dependency
        cls.client = TestClient(main.app)
        cls.client.__enter__()
        with cls.sessions() as db:
            cls.admin_id = db.scalar(select(User.id).where(User.username == 'admin'))
        cls.headers = {'Authorization': 'Bearer ' + make_token(cls.admin_id)}
        cls.add_projects(0, 20)

    @classmethod
    def add_projects(cls, start, count):
        with cls.sessions() as db:
            for i in range(start, start + count):
                person = User(username=f'perf{i}', display_name=f'性能成员{i}', role='project_manager',
                              password_hash='unused', role_grants=[UserRole(role='project_manager')])
                db.add(person); db.flush()
                project = Project(name=f'性能项目{i}', department='性能测试', created_by=cls.admin_id)
                db.add(project); db.flush()
                db.add(ProjectMember(project_id=project.id, user_id=person.id, role='project_manager'))
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        main.app.dependency_overrides.pop(get_db, None)
        for p in reversed(cls.patches): p.stop()
        cls.engine.dispose(); cls.folder.cleanup()

    def measured(self, path):
        statements = []
        def count(conn, cursor, statement, parameters, context, executemany): statements.append(statement)
        event.listen(self.engine, 'before_cursor_execute', count)
        try:
            response = self.client.get(path, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            return response, {'sql':len(statements), 'selects':sum(s.lstrip().upper().startswith('SELECT') for s in statements)}
        finally:
            event.remove(self.engine, 'before_cursor_execute', count)

    def test_a_project_and_bootstrap_queries_stay_bounded(self):
        report = {'scope':'isolated SQLite, 22 projects; query counts, not host latency', 'requests':{}}
        for path, maximum in [('/api/auth/options',3), ('/api/me',4), ('/api/projects',8)]:
            response, counts = self.measured(path)
            self.assertLessEqual(counts['sql'], maximum, path)
            report['requests'][path] = counts
            if path == '/api/projects': self.assertEqual(len(response.json()), 22)
        Path('reports/startup-query-after.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))

    def test_b_more_projects_do_not_add_queries_per_project_or_member(self):
        _, before = self.measured('/api/projects')
        self.add_projects(20, 20)
        response, after = self.measured('/api/projects')
        self.assertEqual(len(response.json()), 42)
        self.assertEqual(after, before)

    def test_c_batch_response_keeps_project_and_revoked_role_permissions(self):
        with self.sessions() as db:
            member = db.scalar(select(User).where(User.username == 'perf0'))
            uid = member.id
            project_id = db.scalar(select(Project.id).where(Project.name == '性能项目0'))
        headers = {'Authorization':'Bearer ' + make_token(uid)}
        result = self.client.get('/api/projects', headers=headers).json()
        self.assertEqual([p['id'] for p in result], [project_id])
        self.assertTrue(result[0]['can_manage']); self.assertTrue(result[0]['members'][0]['eligible'])
        with self.sessions() as db:
            member = db.get(User, uid)
            member.role = 'applicant'; member.role_grants = [UserRole(role='applicant')]
            db.commit()
        self.assertEqual(self.client.get('/api/projects', headers=headers).json(), [])
        projects = self.client.get('/api/projects', headers=self.headers).json()
        project = next(p for p in projects if p['id'] == project_id)
        self.assertFalse(project['members'][0]['eligible'])

    def test_d_public_bootstrap_does_not_load_or_decrypt_private_settings(self):
        with patch('app.settings.decrypt_secret', side_effect=AssertionError('不应解密飞书密钥')):
            self.assertEqual(self.client.get('/projects').status_code, 404)
            options = self.client.get('/api/auth/options').json()
        self.assertEqual(set(options), {'site_name','feishu_login_enabled','local_login_enabled'})

    def test_e_audits_are_committed_for_success_unauthorized_and_forbidden_requests(self):
        with self.sessions() as db:
            member_id = db.scalar(select(User.id).where(User.username == 'perf1'))
        for path, headers, status in [('/api/me',self.headers,200), ('/api/me',{},401),
                                     ('/api/admin/settings',{'Authorization':'Bearer '+make_token(member_id)},403)]:
            response = self.client.get(path, headers=headers)
            self.assertEqual(response.status_code, status)
            self.assertRegex(response.headers['server-timing'], r'app;dur=\d+\.\d+, audit;dur=\d+\.\d+')
            with self.sessions() as db:
                record = db.scalar(select(AuditLog).order_by(AuditLog.id.desc()))
                self.assertEqual((record.path,record.status_code), (path,status))


class AuditConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_audit_does_not_block_health_and_is_awaited_before_response(self):
        started, release = threading.Event(), threading.Event()
        def slow_audit(*args):
            started.set()
            if not release.wait(2): raise AssertionError('审计存储阻塞了事件循环')
        async def respond(request): return Response('ok')
        def request(path):
            return Request({'type':'http','method':'GET','path':path,'headers':[],
                            'scheme':'http','server':('testserver',80),'query_string':b''})
        with patch('app.main.write_request_audit', side_effect=slow_audit), patch('app.main.check_request_rate', return_value=RateDecision()):
            slow = asyncio.create_task(main.audit_all_requests(request('/api/me'), respond))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                health = await asyncio.wait_for(main.audit_all_requests(request('/api/health'), respond), .5)
                self.assertEqual(health.status_code,200)
                self.assertFalse(slow.done(), '审计提交前不能返回成功')
            finally:
                release.set()
                await slow
