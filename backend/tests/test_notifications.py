"""Notification lifecycle checks use an isolated database and mocked Feishu transport."""
import json
from pathlib import Path
import tempfile
import time
import unittest
import httpx
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.database import Base, get_db
from app.main import app
from app.models import (Approval, NotificationLog, Project, ProjectMember, SystemSetting,
                        User, UserRole, WorkflowTemplate, WorkflowVersion)
from app.security import encrypt_secret, make_token

from app.rate_limit import RateDecision


class WorkflowNotificationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='yunwei-notification-')
        self.engine = create_engine('sqlite:///' + str(Path(self.folder.name) / 'test.db'))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.audit_patch = patch('app.main.SessionLocal', self.sessions)
        self.audit_patch.start()
        self.limit_patch = patch('app.main.check_request_rate', return_value=RateDecision())
        self.limit_patch.start()
        self.send_patch = patch('app.main.send_notification')
        self.sender = self.send_patch.start()
        self.retry_patch = patch('app.admin.send_notification')
        self.retry_patch.start()
        def dependency():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = dependency
        self.client = TestClient(app)
        self.ids = {}
        with self.sessions() as db:
            for name, role in [('admin','admin'),('owner','applicant'),('first','applicant'),
                               ('second','applicant'),('final','applicant'),('replacement','applicant'),('operator','operator')]:
                user = User(username=name, display_name=name + '姓名', department='测试部门', role=role,
                            role_grants=[UserRole(role=role)], password_hash='unused', feishu_open_id='ou_' + name)
                db.add(user); db.flush(); self.ids[name] = user.id
            project = Project(name='通知测试项目', department='测试部门', created_by=self.ids['admin'])
            db.add(project); db.flush(); self.project_id = project.id
            db.add(ProjectMember(project_id=project.id, user_id=self.ids['owner'], role='applicant'))
            for kind in ['resource','change','delivery','license']:
                definition = self.definition(kind)
                raw = json.dumps(definition)
                db.add(WorkflowTemplate(kind=kind, draft=raw))
                db.add(WorkflowVersion(kind=kind, version=1, definition=raw, published_by=self.ids['admin']))
            for key, value in {'public_base_url':'https://ops.example.com','site_name':'通知验收平台',
                               'feishu_app_id':'test-app','feishu_app_secret':'test-secret',
                               'feishu_notifications_enabled':True,'feishu_login_enabled':False}.items():
                secret = key == 'feishu_app_secret'
                raw = json.dumps(value)
                db.add(SystemSetting(key=key, value=encrypt_secret(raw) if secret else raw, is_secret=secret))
            db.commit()
        self.headers = {name:{'Authorization':'Bearer ' + make_token(uid)} for name, uid in self.ids.items()}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        self.send_patch.stop()
        self.retry_patch.stop()
        self.audit_patch.stop()
        self.limit_patch.stop()
        self.engine.dispose()
        self.folder.cleanup()

    def definition(self, kind, countersign=False):
        return {'steps':[
            {'id':'first','name':'业务初审','type':'approval','mode':'all',
             'assignee':{'source':'fixed','user_ids':[self.ids['first'], self.ids['second']] if countersign else [self.ids['first']]}},
            {'id':'final','name':'最终审批' if kind == 'license' else '运维执行',
             'type':'license_approval' if kind == 'license' else 'execution','mode':'all',
             'assignee':{'source':'fixed','user_ids':[self.ids['final'] if kind == 'license' else self.ids['operator']]}},
        ]}

    def create(self, kind='license'):
        common = {'project_id':self.project_id}
        bodies = {
            'license':{**common,'project_name':'通知测试项目','customer':'客户','product':'产品','tenant_id':1,
                       'expire_time':int(time.time()*1000)+86400000,'purpose':'测试','account_number':-1,'agent_number':-1},
            'resource':{**common,'environment':'测试','resource_type':'ECS','specification':'2核4GB','start_date':'2030-01-01',
                        'end_date':'2031-01-01','purpose':'测试','capacity_basis':'100人'},
            'change':{**common,'name':'变更通知测试','change_type':'配置变更','window_start':'2030-01-01T12:00',
                      'window_end':'2030-01-01T13:00','problem_description':'变更背景与影响范围',
                      'plan':'创建时不应保存实施方案','rollback_plan':'创建时不应保存回滚方案'},
            'delivery':{**common,'name':'交付通知测试','customer':'客户','cloud_type':'专有云','deployment_mode':'云平台',
                        'environment':'POC','compute_type':'云主机'},
        }
        paths = {'license':'licenses','resource':'resources','change':'changes','delivery':'deliveries'}
        response = self.client.post('/api/' + paths[kind], headers=self.headers['owner'], json=bodies[kind])
        self.assertIn(response.status_code, (200,201), response.text)
        return response.json(), bodies[kind]

    def logs(self, kind, item_id):
        with self.sessions() as db:
            return list(db.scalars(select(NotificationLog).where(NotificationLog.object_type==kind,
                        NotificationLog.object_id==item_id).order_by(NotificationLog.id)))

    def action(self, kind, item_id, person, action='approve', comment=''):
        with self.sessions() as db:
            task_id = db.scalar(select(Approval.id).where(Approval.object_type==kind, Approval.object_id==item_id,
                              Approval.assignee_id==self.ids[person], Approval.action=='pending'))
        payload = {'task_id':task_id,'action':action,'comment':comment,'fulfillment':'已完成，敏感回填不应进入通知'}
        if kind == 'change':
            payload.update(implementation_plan='执行阶段方案', rollback_plan='执行阶段回滚方案')
        return self.client.post(f'/api/{kind}/{item_id}/action', headers=self.headers[person], json=payload)

    def test_submit_next_approver_and_completion_notify_the_right_people_for_all_four_flows(self):
        for kind in ['resource','change','delivery','license']:
            with self.subTest(kind=kind):
                item, _ = self.create(kind)
                item_id = item['id']
                if kind == 'change':
                    self.assertEqual((item['problem_description'], item['plan'], item['rollback_plan']),
                                     ('变更背景与影响范围', '', ''))
                logs = self.logs(kind,item_id)
                self.assertEqual([(l.event,l.recipient_id) for l in logs], [('submitted',self.ids['owner']),('approval_required',self.ids['first'])])
                self.assertIn('下一位处理人：first姓名', logs[0].message)
                self.assertIn('申请人：owner姓名', logs[1].message)
                self.assertIn('业务初审', logs[1].message)
                self.assertIn(item['code'], logs[0].message)
                self.assertIn('https://ops.example.com/', logs[0].message)
                self.assertEqual(self.action(kind,item_id,'first').status_code,200)
                logs = self.logs(kind,item_id)
                next_person = 'final' if kind=='license' else 'operator'
                self.assertEqual([(l.event,l.recipient_id) for l in logs[-2:]], [('status_changed',self.ids['owner']),('approval_required',self.ids[next_person])])
                self.assertIn('下一位处理人：'+next_person+'姓名', logs[-2].message)
                self.assertIn('待审批' if kind=='license' else '待执行', logs[-1].message)
                with patch('app.licenses.generate_key',return_value='{"license":{"body":"private-body","key":"private-key"}}'):
                    result = self.action(kind,item_id,next_person)
                self.assertEqual(result.status_code,200,result.text)
                if kind == 'change':
                    self.assertEqual((result.json()['plan'], result.json()['rollback_plan']),
                                     ('执行阶段方案', '执行阶段回滚方案'))
                logs = self.logs(kind,item_id)
                self.assertEqual(len(logs),5)
                self.assertEqual((logs[-1].event,logs[-1].recipient_id),('completed',self.ids['owner']))
                self.assertIn('流程已完成',logs[-1].message)
                self.assertNotIn('下一位处理人',logs[-1].message)
                messages = '\n'.join(l.message for l in logs)
                for secret in ['private-body','private-key','敏感回填']:
                    self.assertNotIn(secret,messages)

    def test_countersign_does_not_resend_to_pending_reviewers(self):
        with self.sessions() as db:
            db.scalar(select(WorkflowVersion).where(WorkflowVersion.kind=='license')).definition = json.dumps(self.definition('license',countersign=True))
            db.commit()
        item, _ = self.create()
        logs = self.logs('license',item['id'])
        self.assertEqual(len(logs),3)
        self.assertIn('first姓名、second姓名', logs[0].message)
        self.assertEqual(self.action('license',item['id'],'first').status_code,200)
        logs = self.logs('license',item['id'])
        self.assertEqual(len(logs),4)
        self.assertEqual(logs[-1].recipient_id,self.ids['owner'])
        self.assertIn('下一位处理人：second姓名',logs[-1].message)
        self.assertEqual(self.action('license',item['id'],'second').status_code,200)
        self.assertEqual(len(self.logs('license',item['id'])),6)

    def test_reject_resubmit_and_transfer_update_the_applicant(self):
        item, body = self.create()
        item_id = item['id']
        self.assertEqual(self.action('license',item_id,'first',action='reject',comment='请补充用途').status_code,200)
        logs = self.logs('license',item_id)
        self.assertEqual(len(logs),3)
        self.assertEqual((logs[-1].event,logs[-1].recipient_id),('rejected',self.ids['owner']))
        self.assertIn('驳回原因：请补充用途',logs[-1].message)
        result = self.client.post(f'/api/licenses/{item_id}/resubmit',headers=self.headers['owner'],json=body)
        self.assertEqual(result.status_code,200,result.text)
        with self.sessions() as db:
            task_id = db.scalar(select(Approval.id).where(Approval.object_type=='license',Approval.object_id==item_id,Approval.action=='pending'))
        result = self.client.post(f'/api/admin/tasks/{task_id}/reassign',headers=self.headers['admin'],
                                  json={'assignee_id':self.ids['replacement'],'reason':'处理人调整'})
        self.assertEqual(result.status_code,200,result.text)
        logs = self.logs('license',item_id)
        self.assertEqual([(l.event,l.recipient_id) for l in logs[-2:]], [('approval_reassigned',self.ids['owner']),('approval_required',self.ids['replacement'])])
        self.assertIn('下一位处理人：replacement姓名',logs[-2].message)
        self.assertEqual(self.action('license',item_id,'replacement').status_code,200)

    def test_notification_delivery_works_without_feishu_login_and_can_be_retried(self):
        from app.feishu import send_notification
        item, _ = self.create()
        log = self.logs('license',item['id'])[0]
        with patch('app.feishu.SessionLocal',self.sessions), patch('app.feishu.httpx.Client') as client:
            sender = client.return_value.__enter__.return_value
            sender.post.side_effect = [
                type('Response',(),{'raise_for_status':lambda s:None,'json':lambda s:{'code':0,'tenant_access_token':'test-token'}})(),
                type('Response',(),{'raise_for_status':lambda s:None,'json':lambda s:{'code':0}})(),
            ]
            send_notification(log.id)
            self.assertEqual(sender.post.call_count,2)
            sent = sender.post.call_args.kwargs['json']
            self.assertEqual(sent['receive_id'],'ou_owner')
            self.assertIn('下一位处理人：first姓名',json.loads(sent['content'])['text'])
        with self.sessions() as db:
            self.assertEqual(db.get(NotificationLog,log.id).status,'sent')
            db.get(SystemSetting,'feishu_notifications_enabled').value = 'false'
            db.commit()
        other = self.logs('license',item['id'])[1]
        with patch('app.feishu.SessionLocal',self.sessions), patch('app.feishu.httpx.Client') as client:
            send_notification(other.id)
            client.assert_not_called()
        with self.sessions() as db:
            self.assertEqual(db.get(NotificationLog,other.id).status,'unconfigured')
        result = self.client.post(f'/api/admin/notifications/{other.id}/retry',headers=self.headers['admin'])
        self.assertEqual(result.status_code,200)

    def test_delivery_failures_and_missing_recipients_are_recorded_without_external_messages(self):
        from app.feishu import send_notification
        item, _ = self.create()
        log = self.logs('license',item['id'])[0]
        with patch('app.feishu.SessionLocal',self.sessions), patch('app.feishu.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.side_effect = httpx.ConnectError('mocked network failure')
            send_notification(log.id)
        with self.sessions() as db:
            self.assertEqual(db.get(NotificationLog,log.id).status,'failed')
            db.get(User,self.ids['owner']).feishu_open_id = None
            db.commit()
        with patch('app.feishu.SessionLocal',self.sessions), patch('app.feishu.httpx.Client') as client:
            send_notification(log.id)
            client.assert_not_called()
        with self.sessions() as db:
            self.assertEqual(db.get(NotificationLog,log.id).status,'unconfigured')
            self.assertIn('open_id',db.get(NotificationLog,log.id).error)
            owner = db.get(User,self.ids['owner'])
            owner.feishu_open_id = 'ou_owner'
            owner.active = False
            db.commit()
        with patch('app.feishu.SessionLocal',self.sessions), patch('app.feishu.httpx.Client') as client:
            send_notification(log.id)
            client.assert_not_called()
        with self.sessions() as db:
            self.assertIn('已停用',db.get(NotificationLog,log.id).error)
        # Message transport failures do not advance, reject or erase a workflow.
        self.assertEqual(self.action('license',item['id'],'first').status_code,200)

    def test_retry_rejects_already_queued_and_accepts_historical_pending_messages(self):
        item, _ = self.create()
        log = self.logs('license', item['id'])[0]
        response = self.client.post(f'/api/admin/notifications/{log.id}/retry', headers=self.headers['admin'])
        self.assertEqual(response.status_code, 400)
        self.assertIn('已在队列', response.json()['detail'])
        with self.sessions() as db:
            db.get(NotificationLog, log.id).dispatch_ready = False
            db.commit()
        response = self.client.post(f'/api/admin/notifications/{log.id}/retry', headers=self.headers['admin'])
        self.assertEqual(response.status_code, 200, response.text)
        with self.sessions() as db:
            self.assertTrue(db.get(NotificationLog, log.id).dispatch_ready)
