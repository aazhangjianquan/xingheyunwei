"""Disabled HTTP rate limiting, proxy audit addresses and legacy counter compatibility."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app import main
from app.database import Base, get_db
from app.feishu import send_notification
from app.models import AuditLog, NotificationLog, RequestRateLimit, SystemSetting, User, UserRole, WikiArticle
from app.rate_limit import check_request_rate, client_ip
from app.security import encrypt_secret, make_token


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='yunwei-rate-')
        self.engine=create_engine('sqlite:///'+str(Path(self.folder.name)/'rate.db'))
        Base.metadata.create_all(self.engine)
        self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        self.patches=[patch('app.main.SessionLocal',self.sessions),patch('app.main.send_notification'),
                      patch('app.rate_limit._next_cleanup_ms',0)]
        for p in self.patches: p.start()
        def dependency():
            with self.sessions() as db: yield db
        main.app.dependency_overrides[get_db]=dependency
        self.ids={}
        with self.sessions() as db:
            for name, roles, active in [('owner',['applicant'],True),('other',['applicant'],True),
                ('admin',['admin'],True),('second',['operator','admin'],True),('disabled',['admin'],False),('stale',['applicant'],True)]:
                u=User(username=name,display_name=name+'姓名',role='admin' if name=='stale' else roles[0],
                    role_grants=[UserRole(role=r) for r in roles],password_hash='unused',active=active,feishu_open_id='ou_'+name)
                db.add(u);db.flush();self.ids[name]=u.id
            db.commit()
        self.client=TestClient(main.app)
        self.headers={'Authorization':'Bearer '+make_token(self.ids['owner'])}

    def tearDown(self):
        self.client.close();main.app.dependency_overrides.pop(get_db,None)
        for p in reversed(self.patches):p.stop()
        self.engine.dispose();self.folder.cleanup()

    def check(self,stamp=100000,uid='owner',ip='203.0.113.8',sessions=None):
        return check_request_rate(sessions or self.sessions,self.ids[uid] if uid else None,ip,'GET','/api/projects',now_ms=stamp)

    def notifications(self):
        with self.sessions() as db:
            return list(db.scalars(select(NotificationLog).order_by(NotificationLog.id)))

    def test_twenty_first_request_is_blocked_and_sliding_window_recovers_at_ten_seconds(self):
        for _ in range(20):self.assertTrue(self.check().allowed)
        rejected=self.check()
        self.assertFalse(rejected.allowed);self.assertEqual(rejected.retry_after,10)
        self.assertFalse(self.check(109999).allowed)
        # All attempts count, including the two rejected ones; two remain at t=110000.
        self.assertTrue(self.check(110000).allowed)
        self.assertEqual(self.notifications(),[])

    def test_retry_after_reflects_attempts_required_to_expire_not_just_the_oldest_one(self):
        for _ in range(10):self.check(100000)
        for _ in range(10):self.check(105000)
        rejected=self.check(106000)
        self.assertEqual(rejected.retry_after,4)
        self.assertTrue(self.check(110000).allowed)

    def test_ip_quota_is_shared_across_accounts_and_worker_sessions(self):
        for i in range(20):self.assertTrue(self.check(uid='owner' if i%2 else 'other').allowed)
        second_worker=sessionmaker(self.engine,expire_on_commit=False)
        self.assertFalse(self.check(uid='other',sessions=second_worker).allowed)
        self.assertTrue(self.check(ip='203.0.113.99').allowed)

    def test_same_account_on_different_ips_has_independent_quotas(self):
        for _ in range(20):self.assertTrue(self.check(ip='203.0.113.8').allowed)
        self.assertFalse(self.check(ip='203.0.113.8').allowed)
        for _ in range(20):self.assertTrue(self.check(ip='203.0.113.9').allowed)
        self.assertFalse(self.check(ip='203.0.113.9').allowed)

    def test_platform_request_total_has_no_limit_or_alert(self):
        for i in range(150):self.assertTrue(self.check(ip=f'203.0.113.{i//15+1}').allowed)
        self.assertEqual(self.notifications(),[])

    def test_unauthenticated_and_invalid_token_callers_use_ip_quota(self):
        for _ in range(20):self.assertTrue(self.check(uid=None).allowed)
        self.assertFalse(self.check(uid=None).allowed)
        self.assertTrue(self.check(uid=None,ip='203.0.113.9').allowed)

    def test_minute_alert_starts_at_121_combines_dimensions_and_only_targets_active_admins(self):
        for _ in range(120):self.assertEqual(self.check().notification_ids,())
        self.assertEqual(self.notifications(),[])
        decision=self.check()
        self.assertFalse(decision.allowed)
        logs=self.notifications()
        self.assertEqual({x.recipient_id for x in logs},{self.ids['admin'],self.ids['second']})
        self.assertEqual(set(decision.notification_ids),{x.id for x in logs})
        for log in logs:
            self.assertEqual((log.event,log.object_type),('request_rate_exceeded','security'))
            self.assertIn('owner姓名（owner',log.message);self.assertIn('203.0.113.8',log.message)
            self.assertIn('至少 121 次',log.message);self.assertIn('触发维度：IP\n',log.message)
            self.assertIn('已限流',log.message)

    def test_rejected_requests_count_towards_alert_and_cooldown_prevents_a_message_storm(self):
        for _ in range(200):self.check()
        self.assertEqual(len(self.notifications()),2)
        for _ in range(121):self.check(150000)
        self.assertEqual(len(self.notifications()),2)
        self.check(160000)
        self.assertEqual(len(self.notifications()),4)
        with self.sessions() as db:
            self.assertTrue(all(len(json.loads(s.attempts))<=121 for s in db.scalars(select(RequestRateLimit))))

    def test_minute_limit_still_blocks_after_the_short_window_expires(self):
        for _ in range(120):self.check(100000)
        result=self.check(110000)
        self.assertFalse(result.allowed)
        self.assertEqual(result.retry_after,50)
        self.assertEqual(len(self.notifications()),2)
        self.assertTrue(self.check(160000).allowed)

    def test_shared_ip_quota_cannot_be_bypassed_by_switching_accounts(self):
        outcomes=[]
        for i in range(121):
            outcomes.append(check_request_rate(self.sessions,1000+i,'198.51.100.8','GET','/api/projects',now_ms=100000))
        self.assertEqual(sum(r.allowed for r in outcomes),20)
        logs=self.notifications()
        self.assertEqual(len(logs),2)
        self.assertIn('触发维度：IP\n',logs[0].message)

    def test_windows_do_not_reset_at_a_wall_clock_minute_boundary(self):
        for _ in range(20):self.check(59995)
        self.assertFalse(self.check(60001).allowed)

    def test_concurrent_workers_cannot_exceed_quota_or_duplicate_admin_alerts(self):
        def call(_):return self.check()
        with ThreadPoolExecutor(max_workers=12) as pool:
            outcomes=list(pool.map(call,range(140)))
        self.assertEqual(sum(r.allowed for r in outcomes),20)
        self.assertEqual(len(self.notifications()),2)
        self.assertEqual(sum(bool(r.notification_ids) for r in outcomes),1)

    def test_idle_cleanup_preserves_active_subjects_and_cooldown(self):
        with self.sessions() as db:
            db.add_all([RequestRateLimit(subject='ip:old',attempts='[]',alert_after_ms=0,updated_ms=1),
                        RequestRateLimit(subject='ip:recent',attempts='[]',alert_after_ms=210000,updated_ms=150000),
                        RequestRateLimit(subject='user:'+str(self.ids['owner']),attempts='[]',alert_after_ms=0,updated_ms=1)])
            db.commit()
        self.check(180001)
        with self.sessions() as db:
            self.assertIsNone(db.get(RequestRateLimit,'ip:old'))
            self.assertIsNotNone(db.get(RequestRateLimit,'ip:recent'))
            self.assertIsNone(db.get(RequestRateLimit,'user:'+str(self.ids['owner'])))

    def test_mutations_still_work_after_the_old_burst_limit_and_are_audited(self):
        for _ in range(20):
            self.assertEqual(self.client.get('/api/me',headers=self.headers).status_code,200)
        response=self.client.post('/api/wiki',headers={**self.headers,'Origin':'http://localhost:5173'},json={
            'title':'正常创建','category':'规范','content':'正文'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('Retry-After',response.headers)
        self.assertEqual(response.headers['access-control-allow-origin'],'http://localhost:5173')
        self.assertIn('Retry-After',response.headers['access-control-expose-headers'])
        self.assertEqual(response.headers['cache-control'],'no-store')
        with self.sessions() as db:
            self.assertIsNotNone(db.scalar(select(WikiArticle)))
            log=db.scalar(select(AuditLog).order_by(AuditLog.id.desc()))
            self.assertEqual((log.status_code,log.actor_id),(200,self.ids['owner']))
            self.assertIsNotNone(db.scalar(select(AuditLog).where(AuditLog.record_kind=='business')))
            self.assertIsNone(db.scalar(select(RequestRateLimit)))

    def test_more_than_120_requests_succeed_without_counters_or_frequency_alerts(self):
        sender=main.send_notification
        with patch('app.main.check_request_rate',side_effect=AssertionError('HTTP 请求不能调用旧限流器')) as limiter:
            for _ in range(130):
                response=self.client.get('/api/me',headers=self.headers)
                self.assertEqual(response.status_code,200,response.text)
                self.assertNotIn('Retry-After',response.headers)
            limiter.assert_not_called()
        sender.assert_not_called()
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(RequestRateLimit)))
            self.assertIsNone(db.scalar(select(NotificationLog)))

    def test_health_static_and_cors_preflights_do_not_consume_a_quota(self):
        for _ in range(12):
            self.assertEqual(self.client.get('/api/health').status_code,200)
            self.client.get('/assets/not-present.css')
            self.client.options('/api/me',headers={'Origin':'http://localhost:5173','Access-Control-Request-Method':'GET'})
        with self.sessions() as db:self.assertIsNone(db.scalar(select(RequestRateLimit)))

    def test_invalid_bearer_tokens_keep_returning_authentication_errors(self):
        for _ in range(25):self.assertEqual(self.client.get('/api/me',headers={'Authorization':'Bearer forged'}).status_code,401)
        self.assertEqual(self.client.get('/api/me',headers={'Authorization':'Bearer different-forgery'}).status_code,401)
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(RequestRateLimit)))

    def test_feishu_dispatch_uses_admin_open_ids_and_retains_unconfigured_records(self):
        for _ in range(121):decision=self.check()
        with patch('app.feishu.SessionLocal',self.sessions):
            for log_id in decision.notification_ids:send_notification(log_id)
        self.assertTrue(all(l.status=='unconfigured' for l in self.notifications()))
        with self.sessions() as db:
            for key,value in {'feishu_app_id':'cli_test','feishu_app_secret':'test-secret','feishu_notifications_enabled':True}.items():
                secret=key=='feishu_app_secret'
                raw=json.dumps(value)
                db.add(SystemSetting(key=key,value=encrypt_secret(raw) if secret else raw,is_secret=secret))
            db.commit()
        with patch('app.feishu.SessionLocal',self.sessions),patch('app.feishu.httpx.Client') as transport:
            transport.return_value.__enter__.return_value.post.return_value.json.return_value={'code':0,'tenant_access_token':'test-token'}
            for log_id in decision.notification_ids:send_notification(log_id)
            calls=transport.return_value.__enter__.return_value.post.call_args_list
            destinations={c.kwargs['json']['receive_id'] for c in calls if 'receive_id' in c.kwargs.get('json',{})}
        self.assertEqual(destinations,{'ou_admin','ou_second'})
        self.assertTrue(all(l.status=='sent' for l in self.notifications()))

    def test_revoking_admin_before_delivery_prevents_security_message_delivery(self):
        for _ in range(121):decision=self.check()
        with self.sessions() as db:
            user=db.get(User,self.ids['admin'])
            user.role='applicant';user.role_grants=[UserRole(role='applicant')]
            for key,value in {'feishu_app_id':'cli_test','feishu_app_secret':'test-secret','feishu_notifications_enabled':True}.items():
                raw=json.dumps(value);secret=key=='feishu_app_secret'
                db.add(SystemSetting(key=key,value=encrypt_secret(raw) if secret else raw,is_secret=secret))
            db.commit()
        with patch('app.feishu.SessionLocal',self.sessions),patch('app.feishu.httpx.Client') as transport:
            transport.return_value.__enter__.return_value.post.return_value.json.return_value={'code':0,'tenant_access_token':'test-token'}
            for log_id in decision.notification_ids:send_notification(log_id)
            calls=transport.return_value.__enter__.return_value.post.call_args_list
            destinations={c.kwargs['json']['receive_id'] for c in calls if 'receive_id' in c.kwargs.get('json',{})}
        self.assertEqual(destinations,{'ou_second'})
        log=next(l for l in self.notifications() if l.recipient_id==self.ids['admin'])
        self.assertIn('不再是系统管理员',log.error)


class ProxyAddressTests(unittest.TestCase):
    def address(self,peer,header='',trusted=''):
        request=Request({'type':'http','method':'GET','path':'/api/me','query_string':b'',
                         'headers':[(b'x-forwarded-for',header.encode())], 'client':(peer,12345)})
        with patch.dict(os.environ,{'TRUSTED_PROXY_CIDRS':trusted}):return client_ip(request)

    def test_untrusted_sources_cannot_spoof_forwarding_headers(self):
        self.assertEqual(self.address('203.0.113.8','192.0.2.1'),'203.0.113.8')
        self.assertEqual(self.address('203.0.113.8','192.0.2.1','127.0.0.1/32'),'203.0.113.8')

    def test_trusted_proxy_chain_uses_nearest_untrusted_hop(self):
        self.assertEqual(self.address('10.0.0.1','1.1.1.1, 203.0.113.8, 10.0.0.2','10.0.0.0/24'),'203.0.113.8')
        self.assertEqual(self.address('10.0.0.1','not-an-ip','10.0.0.0/24'),'10.0.0.1')

    def test_ipv6_and_ipv4_mapped_addresses_are_canonical(self):
        self.assertEqual(self.address('::ffff:203.0.113.8'),'203.0.113.8')
        self.assertEqual(self.address('2001:0db8:0:0:0:0:0:1'),'2001:db8::1')
        self.assertEqual(self.address('2001:db8::1','2001:db8:1::2','2001:db8::/64'),'2001:db8:1::2')
