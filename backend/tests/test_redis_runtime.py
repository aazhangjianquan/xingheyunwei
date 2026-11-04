"""Integration against a real, isolated Redis. Never flush shared databases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
import httpx
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app import main, redis_runtime
from app.database import Base, get_db
from app.feishu import deliver_notification, send_notification, tenant_token, token_cache_key
from app.migrations import upgrade
from app.models import NotificationLog, RequestRateLimit, SystemSetting, User, WikiArticle, now
from app.notification_queue import GROUP, consume, dispatch, ensure_group, reconcile, stream_key
from app.rate_limit import check_request_rate, RateLimitUnavailable
from app.security import decrypt_secret, encrypt_secret, make_token


@unittest.skipUnless(os.getenv('REDIS_TEST_URL'), '需要独立 REDIS_TEST_URL 集成环境')
class RedisIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.namespace = 'test_' + uuid.uuid4().hex
        self.environment = patch.dict(os.environ, {'REDIS_URL': os.environ['REDIS_TEST_URL'],
            'REDIS_PASSWORD': '', 'REDIS_NAMESPACE': self.namespace})
        self.environment.start()
        self.connection = redis_runtime.client()
        self.connection.ping()
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///' + self.folder.name + '/test.db')
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [patch('app.main.SessionLocal', self.sessions), patch('app.feishu.SessionLocal', self.sessions)]
        for item in self.patches: item.start()
        with self.sessions() as db:
            owner = User(username='owner', display_name='申请人', role='applicant', password_hash='unused', feishu_open_id='ou_owner')
            admin = User(username='admin', display_name='管理员', role='admin', password_hash='unused', feishu_open_id='ou_admin')
            db.add_all([owner, admin]); db.flush()
            self.owner, self.admin = owner.id, admin.id
            for key, value in {'feishu_app_id': 'cli_test', 'feishu_app_secret': 'private-test-secret', 'feishu_notifications_enabled': True}.items():
                raw = json.dumps(value)
                db.add(SystemSetting(key=key, value=encrypt_secret(raw) if key=='feishu_app_secret' else raw, is_secret=key=='feishu_app_secret'))
            db.commit()

    def tearDown(self):
        keys = list(self.connection.scan_iter('{' + self.namespace + '}:*'))
        if keys: self.connection.delete(*keys)
        for item in reversed(self.patches): item.stop()
        main.app.dependency_overrides.pop(get_db, None)
        self.environment.stop()
        self.engine.dispose()
        self.folder.cleanup()

    def check(self, stamp=100000, uid=None, ip='203.0.113.8'):
        return check_request_rate(self.sessions, self.owner if uid is None else uid,
                                  ip, 'GET', '/api/projects', now_ms=stamp)

    def log(self):
        with self.sessions() as db:
            record = NotificationLog(recipient_id=self.owner, event='submitted', object_type='resource',
                                     object_id=1, message='申请已提交，请登录平台查看')
            db.add(record); db.commit()
            return record.id

    def transport(self):
        context = patch('app.feishu.httpx.Client')
        transport = context.start()
        self.addCleanup(context.stop)
        transport.return_value.__enter__.return_value.post.return_value.json.return_value = {
            'code': 0, 'tenant_access_token': 'sensitive-token', 'expire': 7200}
        return transport.return_value.__enter__.return_value

    def test_exact_burst_boundary_and_recovery_use_shared_redis(self):
        for _ in range(20): self.assertTrue(self.check().allowed)
        decision = self.check()
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.retry_after, 10)
        self.assertTrue(self.check(110000).allowed)
        with self.sessions() as db: self.assertIsNone(db.scalar(select(RequestRateLimit)))

    def test_minute_limit_includes_rejections_and_returns_longer_wait(self):
        for _ in range(120): self.check()
        result = self.check(110000)
        self.assertFalse(result.allowed)
        self.assertEqual(result.retry_after, 50)
        with self.sessions() as db:
            logs = list(db.scalars(select(NotificationLog)))
            self.assertEqual(len(logs), 1)
            self.assertIn('至少 121 次', logs[0].message)
        self.assertTrue(self.check(160000).allowed)

    def test_concurrent_clients_accept_exactly_twenty_and_alert_once(self):
        with ThreadPoolExecutor(max_workers=16) as pool:
            decisions = list(pool.map(lambda _: self.check(), range(160)))
        self.assertEqual(sum(result.allowed for result in decisions), 20)
        self.assertEqual(sum(bool(result.notification_ids) for result in decisions), 1)
        for subject in ('ip:203.0.113.8',):
            key = redis_runtime.key(f'rate:{subject}:attempts')
            self.assertEqual(self.connection.zcard(key), 121)
            self.assertGreater(self.connection.pttl(key), 59000)
            self.assertLessEqual(self.connection.pttl(key), 61000)

    def test_ips_are_independent_even_for_the_same_account(self):
        for _ in range(20):self.assertTrue(self.check(ip='203.0.113.8').allowed)
        self.assertFalse(self.check(ip='203.0.113.8').allowed)
        for _ in range(20):self.assertTrue(self.check(ip='203.0.113.9').allowed)
        self.assertFalse(self.check(ip='203.0.113.9').allowed)
        self.assertEqual(list(self.connection.scan_iter(redis_runtime.key('rate:user:*'))), [])

    def test_many_ips_can_exceed_total_threshold_without_any_global_block(self):
        with ThreadPoolExecutor(max_workers=16) as pool:
            decisions = list(pool.map(lambda i: self.check(ip=f'198.51.100.{i//20+1}'), range(160)))
        self.assertTrue(all(result.allowed for result in decisions))
        self.assertTrue(all(not result.notification_ids for result in decisions))

    def test_one_ip_cannot_reset_its_quota_by_switching_accounts(self):
        for i in range(20):self.assertTrue(self.check(uid=100+i).allowed)
        self.assertFalse(self.check(uid=200).allowed)
        self.assertTrue(self.check(uid=200, ip='203.0.113.9').allowed)

    def test_http_requests_have_no_ip_quota_and_do_not_write_redis_rate_keys(self):
        def dependency():
            with self.sessions() as db: yield db
        main.app.dependency_overrides[get_db] = dependency
        first = TestClient(main.app, client=('203.0.113.8', 50000))
        second = TestClient(main.app, client=('203.0.113.9', 50000))
        headers = {'Authorization': 'Bearer '+make_token(self.owner)}
        try:
            with patch.dict(os.environ, {'TRUSTED_PROXY_CIDRS': ''}):
                for _ in range(20):self.assertEqual(first.get('/api/me', headers=headers).status_code, 200)
                self.assertEqual(first.get('/api/me', headers=headers).status_code, 200)
                self.assertEqual(second.get('/api/me', headers=headers).status_code, 200)
                spoofed = {**headers, 'X-Forwarded-For': '203.0.113.99'}
                self.assertEqual(first.get('/api/me', headers=spoofed).status_code, 200)
                self.assertEqual(list(self.connection.scan_iter('{' + self.namespace + '}:rate:*')), [])
        finally:
            first.close(); second.close()

    def test_redis_unavailable_rejects_mutations_without_sql_counter_fallback(self):
        def dependency():
            with self.sessions() as db: yield db
        main.app.dependency_overrides[get_db] = dependency
        with patch('app.redis_runtime.client', side_effect=RedisConnectionError('unavailable')):
            with self.assertRaises(RateLimitUnavailable): self.check()
        with self.sessions() as db: self.assertIsNone(db.scalar(select(RequestRateLimit)))

    def test_business_request_does_not_depend_on_redis_rate_checks(self):
        def dependency():
            with self.sessions() as db: yield db
        main.app.dependency_overrides[get_db] = dependency
        client = TestClient(main.app)
        try:
            with patch('app.redis_runtime.client', side_effect=RedisConnectionError('unavailable')):
                response = client.post('/api/wiki', headers={'Authorization': 'Bearer '+make_token(self.owner)},
                                       json={'title': '允许写入', 'category': '测试', 'content': '正文'})
            self.assertEqual(response.status_code, 200,response.text)
            self.assertNotIn('Retry-After',response.headers)
            with self.sessions() as db:
                self.assertIsNotNone(db.scalar(select(WikiArticle)))
                self.assertIsNone(db.scalar(select(RequestRateLimit)))
        finally: client.close()

    def test_feishu_token_cache_is_shared_encrypted_and_rotated_by_credentials(self):
        transport = self.transport()
        self.assertEqual(tenant_token(transport, 'cli_test', 'private-test-secret'), 'sensitive-token')
        self.assertEqual(tenant_token(transport, 'cli_test', 'private-test-secret'), 'sensitive-token')
        self.assertEqual(transport.post.call_count, 1)
        key = token_cache_key('cli_test', 'private-test-secret')
        self.assertNotIn('private-test-secret', key)
        cached = self.connection.get(key)
        self.assertNotIn('sensitive-token', cached)
        self.assertEqual(decrypt_secret(cached), 'sensitive-token')
        self.assertGreater(self.connection.ttl(key), 7000)
        tenant_token(transport, 'cli_test', 'rotated-secret')
        self.assertEqual(transport.post.call_count, 2)

    def test_notification_dispatch_only_enqueues_ids_and_duplicate_delivery_is_safe(self):
        log_id = self.log()
        transport = self.transport()
        ensure_group()
        send_notification(log_id)
        send_notification(log_id)
        transport.post.assert_not_called()
        self.assertEqual(self.connection.xlen(stream_key()), 1)
        messages = self.connection.xreadgroup(GROUP, 'worker-a', {stream_key(): '>'}, count=1)[0][1]
        self.assertEqual(messages[0][1], {'log_id': str(log_id)})
        consume(messages, deliver_notification)
        self.assertEqual(self.connection.xlen(stream_key()), 0)
        deliver_notification(log_id, scheduled=True)
        self.assertEqual(transport.post.call_count, 2)  # token + one message
        sent = transport.post.call_args.kwargs['json']
        self.assertEqual(len(sent['uuid']), 36)
        with self.sessions() as db:
            record = db.get(NotificationLog, log_id)
            self.assertEqual((record.status, record.attempt_count), ('sent', 1))

    def test_worker_reclaims_unacknowledged_jobs_after_a_consumer_crashes(self):
        log_id = self.log()
        transport = self.transport()
        ensure_group(); dispatch(log_id)
        self.connection.xreadgroup(GROUP, 'dead-worker', {stream_key(): '>'}, count=1)
        claimed = self.connection.xautoclaim(stream_key(), GROUP, 'replacement', 0, start_id='0-0')
        consume(claimed[1], deliver_notification)
        self.assertEqual(transport.post.call_count, 2)
        self.assertEqual(self.connection.xpending(stream_key(), GROUP)['pending'], 0)

    def test_database_outbox_recovers_when_enqueue_or_redis_data_is_lost(self):
        log_id = self.log()
        with patch('app.redis_runtime.client', side_effect=RedisConnectionError('unavailable')):
            dispatch(log_id)
        self.assertEqual(reconcile(self.sessions), 1)
        self.assertEqual(self.connection.xlen(stream_key()), 1)
        self.connection.delete(stream_key(), redis_runtime.key(f'notifications:queued:{log_id}'))
        self.assertEqual(reconcile(self.sessions), 1)
        ensure_group()
        transport = self.transport()
        entries = self.connection.xreadgroup(GROUP, 'restored-worker', {stream_key(): '>'}, count=1)[0][1]
        consume(entries, deliver_notification)
        self.assertEqual(transport.post.call_count, 2)

    def test_historical_pending_messages_wait_for_explicit_manual_retry(self):
        log_id = self.log()
        with self.sessions() as db:
            db.get(NotificationLog, log_id).dispatch_ready = False
            db.commit()
        self.assertEqual(reconcile(self.sessions), 0)
        transport = self.transport()
        deliver_notification(log_id, scheduled=True)
        transport.post.assert_not_called()
        with self.sessions() as db:
            self.assertEqual(db.get(NotificationLog, log_id).status, 'pending')

    def test_delivery_retries_are_delayed_bounded_and_recoverable(self):
        log_id = self.log()
        transport = self.transport()
        transport.post.side_effect = httpx.ConnectError('test network unavailable')
        for attempt in range(1, 4):
            deliver_notification(log_id, scheduled=True)
            with self.sessions() as db:
                record = db.get(NotificationLog, log_id)
                self.assertEqual((record.status, record.attempt_count), ('failed', attempt))
                if attempt < 3:
                    self.assertIsNotNone(record.next_attempt_at)
                    self.assertEqual(reconcile(self.sessions), 0)
                    record.next_attempt_at = now()-timedelta(seconds=1)
                    db.commit()
                else: self.assertIsNone(record.next_attempt_at)
        self.assertEqual(reconcile(self.sessions), 0)
        deliver_notification(log_id, scheduled=True)
        self.assertEqual(transport.post.call_count, 3)


class RedisSchemaUpgradeTests(unittest.TestCase):
    def test_outbox_upgrade_preserves_records_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine('sqlite:///'+str(Path(folder)/'old.db'))
            with engine.begin() as db:
                db.execute(text('CREATE TABLE notification_logs (id INTEGER PRIMARY KEY, status VARCHAR(30), message TEXT)'))
                db.execute(text("INSERT INTO notification_logs VALUES (1, 'sent', '历史通知')"))
            upgrade(engine); upgrade(engine)
            with engine.connect() as db:
                record = db.execute(text('SELECT message, attempt_count, next_attempt_at FROM notification_logs WHERE id=1')).one()
                self.assertEqual(tuple(record), ('历史通知', 0, None))
            self.assertIn('ix_notifications_dispatch', {i['name'] for i in inspect(engine).get_indexes('notification_logs')})
            engine.dispose()
