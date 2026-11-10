"""Business histories, transaction boundaries, secret exclusion and legacy queries."""
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app import audit, main
from app.database import Base, get_db
from app.migrations import upgrade
from app.models import (AuditLog, DeliveryCredential, Project, ProjectMember, ResourceRequest, User, UserRole, WikiArticle,
                        WorkflowTemplate, WorkflowVersion, DepartmentManager)
from app.security import encrypt_secret, make_token

from app.rate_limit import RateDecision


class BusinessAuditTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='yunwei-business-audit-')
        self.engine = create_engine('sqlite:///' + str(Path(self.folder.name) / 'audit.db'))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [patch('app.main.SessionLocal', self.sessions), patch('app.main.send_notification'),
                        patch('app.main.check_request_rate', return_value=RateDecision())]
        for item in self.patches: item.start()
        def dependency():
            with self.sessions() as db: yield db
        main.app.dependency_overrides[get_db] = dependency
        self.client = TestClient(main.app)
        self.ids = {}
        with self.sessions() as db:
            for username, name, roles in [('admin', '管理员', ['admin']), ('owner', '张三', ['applicant']),
                ('reviewer', '李四', ['department_head']), ('final', '王五', ['operator']),
                ('replacement', '赵六', ['department_head']), ('manager', '项目负责人', ['project_owner', 'project_manager'])]:
                person = User(username=username, display_name=name, department='研发部', role=roles[0],
                              password_hash='not-a-password', role_grants=[UserRole(role=r) for r in roles])
                db.add(person); db.flush(); self.ids[username] = person.id
            project = Project(name='业务平台100%_', department='研发部', created_by=self.ids['admin'])
            db.add(project); db.flush(); self.project_id=project.id
            for name, role in [('owner','applicant'), ('final','operator'), ('manager','project_owner'), ('manager','project_manager')]:
                db.add(ProjectMember(project_id=project.id, user_id=self.ids[name], role=role))
            from app import organization as org
            org.seed_organization(db)
            db.add(DepartmentManager(department_id=project.department_id, user_id=self.ids['reviewer']))
            for kind in ['resource', 'change', 'delivery', 'license']:
                definition = {'steps':[
                    {'id':'review','name':'部门审核','type':'approval','mode':'all','assignee':{'source':'fixed','user_ids':[self.ids['reviewer']]}},
                    {'id':'final','name':'最终审批' if kind=='license' else '执行交付', 'type':'license_approval' if kind=='license' else 'execution',
                     'mode':'all','assignee':{'source':'fixed','user_ids':[self.ids['final']]}}
                ]}
                raw=json.dumps(definition, ensure_ascii=False)
                db.add(WorkflowTemplate(kind=kind, draft=raw, revision=1))
                db.add(WorkflowVersion(kind=kind, version=1, definition=raw, published_by=self.ids['admin']))
            db.commit()
        self.headers={name:{'Authorization':'Bearer '+make_token(uid)} for name, uid in self.ids.items()}
        self.bodies = {
            'resource': dict(project_id=self.project_id, project_name='伪造项目名', environment='生产', resource_type='ECS',
                specification='2核4G', start_date='2030-01-01', end_date='2031-01-01', purpose='部署应用', capacity_basis='容量依据'),
            'change': dict(project_id=self.project_id, name='网关配置更新', environment='生产', change_type='配置变更',
                window_start='2030-01-01T10:00', window_end='2030-01-01T11:00', problem_description='需要解决的问题及影响范围'),
            'delivery': dict(project_id=self.project_id, name='华东客户交付', customer='客户', cloud_type='专有云',
                deployment_mode='云平台', environment='POC', compute_type='虚拟机'),
            'license': dict(project_id=self.project_id, customer='客户', product='产品', tenant_id=1,
                expire_time=int(time.time()*1000)+86400000, account_number=-1, agent_number=-1, purpose='项目授权')}

    def tearDown(self):
        self.client.close(); main.app.dependency_overrides.pop(get_db, None)
        for item in reversed(self.patches): item.stop()
        self.engine.dispose(); self.folder.cleanup()

    def create(self, kind):
        path={'resource':'resources','change':'changes','delivery':'deliveries','license':'licenses'}[kind]
        response=self.client.post('/api/'+path,json=self.bodies[kind],headers=self.headers['owner'])
        self.assertIn(response.status_code,[200,201],response.text)
        return response.json()

    def logs(self, **params):
        response=self.client.get('/api/audit',params=params,headers=self.headers['admin'])
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def details(self, row):
        response=self.client.get(f'/api/audit/{row["id"]}',headers=self.headers['admin'])
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def read(self, kind, item):
        path={'resource':'resources','change':'changes','delivery':'deliveries','license':'licenses'}[kind]
        return self.client.get(f'/api/{path}/{item["id"]}',headers=self.headers['admin']).json()

    def act(self, kind, item, who='reviewer', action='approve', **values):
        task=next(a for a in self.read(kind,item)['approvals'] if a['action']=='pending')
        return self.client.post(f'/api/{kind}/{item["id"]}/action',headers=self.headers[who],
            json={'task_id':task['id'],'action':action,'comment':'审批意见',**values})

    def test_admin_can_submit_all_business_kinds_with_project_department_approval(self):
        with self.sessions() as db:
            db.get(User,self.ids['admin']).department='平台管理'
            for version in db.scalars(select(WorkflowVersion)).all():
                definition=json.loads(version.definition)
                definition['steps'][0]['assignee']={'source':'department_role','role':'department_head'}
                db.add(WorkflowVersion(kind=version.kind,version=2,definition=json.dumps(definition),published_by=self.ids['admin']))
            db.commit()
        for kind,path in [('resource','resources'),('change','changes'),('delivery','deliveries'),('license','licenses')]:
            with self.subTest(kind=kind):
                response=self.client.post('/api/'+path,json=self.bodies[kind],headers=self.headers['admin'])
                self.assertIn(response.status_code,[200,201],response.text)
                item=self.read(kind,response.json())
                owner_id=item['applicant_id'] if kind in {'resource','license'} else item['owner_id']
                self.assertEqual(owner_id,self.ids['admin'])
                self.assertEqual((item['owner_department'],item['approval_department']),('平台管理','研发部'))
                self.assertEqual(item['approval_department_source'],'selected_membership')
                task=next(a for a in item['approvals'] if a['action']=='pending')
                self.assertEqual(task['assignee_id'],self.ids['reviewer'])
                self.assertEqual(self.logs(target_type=kind,action='create')['items'][0]['actor_username'],'admin')

    def test_creation_records_real_object_identity_and_default_view_has_no_read_noise(self):
        for kind in self.bodies:
            item=self.create(kind)
            rows=self.logs(target_type=kind,action='create')['items']
            self.assertEqual(len(rows),1)
            row=rows[0]
            self.assertEqual((row['actor_name'],row['actor_username']),('张三','owner'))
            self.assertEqual((row['target_id'],row['target_code']),(str(item['id']),item['code']))
            self.assertIn(item['code'],row['summary'])
            self.assertEqual(row['record_kind'],'business')
            changes=self.details(row)['changes']
            self.assertTrue(any(c['field']=='project' and '业务平台100%_' in c['after'] for c in changes))
            self.assertTrue(any(c['field']=='stage' and c['after']=='部门负责人审批' for c in changes))
        self.assertEqual(self.logs()['total'],4)
        self.assertGreater(self.logs(record_kind='request')['total'],4)

    def test_reject_and_resubmit_show_exact_before_after_changes(self):
        for kind in self.bodies:
            item=self.create(kind)
            self.assertEqual(self.act(kind,item,action='reject').status_code,200)
            updated={**self.bodies[kind]}
            key='specification' if kind=='resource' else 'product' if kind=='license' else 'name'
            updated[key]='修改后的值'
            path=f'/api/licenses/{item["id"]}/resubmit' if kind=='license' else f'/api/{kind}/{item["id"]}/resubmit'
            response=self.client.post(path,json=updated,headers=self.headers['owner'])
            self.assertEqual(response.status_code,200,response.text)
            row=self.logs(target_type=kind,action='resubmit')['items'][0]
            diff={c['field']:c for c in self.details(row)['changes']}
            self.assertEqual(diff[key]['before'],self.bodies[kind][key])
            self.assertEqual(diff[key]['after'],'修改后的值')
            self.assertEqual(diff['status']['before'],'已驳回')
            self.assertEqual(self.logs(target_type=kind,action='reject')['items'][0]['actor_name'],'李四')

    def test_approval_generation_and_failure_preserve_transaction_and_never_log_key(self):
        item=self.create('license')
        self.assertEqual(self.act('license',item).status_code,200)
        with patch('app.licenses.generate_key',side_effect=HTTPException(503,'生成服务暂不可用')):
            failed=self.act('license',item,who='final')
        self.assertEqual(failed.status_code,503)
        self.assertEqual(self.logs(action='generate')['total'],0)
        key='SECRET_SIGNED_LICENSE_BODY_AND_KEY'
        with patch('app.licenses.generate_key',return_value=key):
            succeeded=self.act('license',item,who='final')
        self.assertEqual(succeeded.status_code,200,succeeded.text)
        row=self.logs(action='generate')['items'][0]
        self.assertEqual(row['actor_name'],'王五')
        self.assertEqual(row['target_code'],item['code'])
        self.assertNotIn(key,json.dumps(self.details(row)))
        self.assertEqual(self.logs(action='approve')['total'],1)
        failures=self.logs(record_kind='request',result='failure')['items']
        self.assertTrue(any(r['status_code']==503 for r in failures))

    def test_failed_creations_and_unauthorized_requests_do_not_claim_business_success(self):
        bad={**self.bodies['resource'],'end_date':'2000-01-01'}
        response=self.client.post('/api/resources',json=bad,headers=self.headers['owner'])
        self.assertEqual(response.status_code,400)
        for path in ['/api/audit','/api/audit/1']:
            self.assertEqual(self.client.get(path,headers=self.headers['owner']).status_code,403)
            self.assertEqual(self.client.get(path).status_code,401)
        self.assertEqual(self.logs()['total'],0)
        self.assertGreater(self.logs(record_kind='request',result='failure')['total'],0)

    def test_business_failure_after_log_creation_rolls_back_both_rows(self):
        with patch('app.main.workflow_notifications',side_effect=HTTPException(503,'事务未完成')):
            response=self.client.post('/api/resources',json=self.bodies['resource'],headers=self.headers['owner'])
        self.assertEqual(response.status_code,503)
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(ResourceRequest)))
            self.assertIsNone(db.scalar(select(AuditLog).where(AuditLog.record_kind=='business')))
        self.assertEqual(self.logs(record_kind='request',result='failure')['total'],1)

    def test_long_multibyte_fields_do_not_overflow_mysql_text_or_block_the_application(self):
        body={**self.bodies['resource']}
        for field in ['specification','purpose','capacity_basis','ports','whitelist']:
            body[field]='容量数据🙂'*2000
        response=self.client.post('/api/resources',json=body,headers=self.headers['owner'])
        self.assertEqual(response.status_code,200,response.text)
        with self.sessions() as db:
            row=db.scalar(select(AuditLog).where(AuditLog.record_kind=='business'))
            self.assertLessEqual(len(row.changes.encode('utf-8')),60000)
            self.assertIn('已截断',row.changes)
            self.assertIn('容量数据',row.changes)

    def test_project_member_removal_and_changes_keep_names_and_roles(self):
        manager=self.ids['manager']
        members=[{'user_id':manager,'role':'project_owner'},{'user_id':manager,'role':'project_manager'}]
        response=self.client.put(f'/api/projects/{self.project_id}/members',json={'members':members},headers=self.headers['manager'])
        self.assertEqual(response.status_code,200,response.text)
        row=self.logs(action='members')['items'][0]
        changed=next(c for c in self.details(row)['changes'] if c['field']=='members')
        self.assertIn('张三',changed['before']); self.assertNotIn('张三',changed['after'])
        self.assertIn('项目经理',changed['after'])
        self.client.put(f'/api/projects/{self.project_id}/members',json={'members':members},headers=self.headers['manager'])
        self.assertEqual(self.logs(action='members')['total'],1)

    def test_member_updates_and_settings_secrets_are_markers_only(self):
        password='Secure@Password2030'
        response=self.client.post('/api/users',headers=self.headers['admin'],json={
            'username':'newmember','display_name':'新成员','department_id':self.client.post('/api/departments',headers=self.headers['admin'],json={'name':'账号测试部'}).json()['id'],'roles':['applicant'],'password':password})
        self.assertEqual(response.status_code,200,response.text)
        uid=response.json()['id']
        self.client.patch(f'/api/users/{uid}',headers=self.headers['admin'],json={'active':False,'password':'NewSecure@2030','roles':['applicant','license_manager']})
        row=self.logs(target_type='user',action='disable')['items'][0]
        rendered=json.dumps(self.details(row),ensure_ascii=False)
        self.assertNotIn(password,rendered); self.assertNotIn('NewSecure@2030',rendered)
        self.assertNotIn('password_hash',rendered); self.assertIn('已设置 / 已更新',rendered)
        self.assertIn('License 管理',rendered)
        response=self.client.put('/api/admin/settings',headers=self.headers['admin'],json={'values':{
            'feishu_app_id':'cli_app','feishu_app_secret':'DO_NOT_LOG_APP_SECRET'}})
        self.assertEqual(response.status_code,200,response.text)
        row=self.logs(target_type='settings')['items'][0]
        self.assertNotIn('DO_NOT_LOG_APP_SECRET',json.dumps(self.details(row)))

    def test_credential_writes_are_retired_and_delivery_can_finish_without_any_database_account(self):
        item=self.create('delivery')
        for who in ['owner','admin','final']:
            response=self.client.post(f'/api/deliveries/{item["id"]}/credentials',headers=self.headers[who],json={
                'db_type':'MySQL','host':'db.internal:3306','username':'db_user','password':'PRIVATE_DB_PASSWORD','privilege':'普通账号'})
            self.assertEqual(response.status_code,410,response.text)
            self.assertIn('已取消数据库账号回填',response.json()['detail'])
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(DeliveryCredential)))
        self.assertEqual(self.logs(action='credentials')['items'],[])
        self.assertEqual(self.act('delivery',item).status_code,200)
        response=self.act('delivery',item,who='final',fulfillment='password=PRIVATE_FULFILLMENT')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'completed')
        self.assertNotIn('PRIVATE_FULFILLMENT',json.dumps(self.details(self.logs(action='complete')['items'][0])))

    def test_historical_credentials_remain_readable_and_never_enter_audit_plaintext(self):
        item=self.create('delivery')
        with self.sessions() as db:
            credential=DeliveryCredential(project_id=item['id'],db_type='MySQL',host='db.internal:3306',
                username='db_user',password_ciphertext=encrypt_secret('PRIVATE_DB_PASSWORD'),privilege='普通账号')
            db.add(credential);db.flush();credential_id=credential.id;db.commit()
        revealed=self.client.get(f'/api/deliveries/{item["id"]}/credentials/{credential_id}/reveal',headers=self.headers['owner'])
        self.assertEqual(revealed.json()['password'],'PRIVATE_DB_PASSWORD')
        self.assertNotIn('PRIVATE_DB_PASSWORD',json.dumps(self.details(self.logs(action='view_secret')['items'][0])))

    def test_transfer_records_actual_old_and_new_handler(self):
        item=self.create('resource')
        with self.sessions() as db:
            reviewer=db.get(User,self.ids['reviewer'])
            db.add(DepartmentManager(department_id=reviewer.department_id,user_id=self.ids['replacement']));db.commit()
        task=next(a for a in self.read('resource',item)['approvals'] if a['action']=='pending')
        response=self.client.post(f'/api/admin/tasks/{task["id"]}/reassign',headers=self.headers['admin'],
            json={'assignee_id':self.ids['replacement'],'reason':'审批人休假'})
        self.assertEqual(response.status_code,200,response.text)
        row=self.logs(action='transfer')['items'][0]
        changes={c['field']:c for c in self.details(row)['changes']}
        self.assertIn('李四',changes['handler']['before']); self.assertIn('赵六',changes['handler']['after'])
        self.assertEqual(changes['reason']['after'],'审批人休假')

    def test_article_changes_noop_and_deleted_object_snapshots_survive(self):
        space=self.client.post('/api/wiki/spaces',headers=self.headers['admin'],json={'key':'AUDIT','name':'审计测试空间'}).json()
        self.client.post(f'/api/wiki/spaces/{space["id"]}/members',headers=self.headers['admin'],json={'user_id':self.ids['owner'],'role':'editor'})
        created=self.client.post('/api/wiki',headers=self.headers['owner'],json={'title':'原文章','category':'规范','content':'原内容','space_id':space['id']}).json()
        edited={**created,'title':'新文章','category':'规范','content':'新内容'}
        self.assertEqual(self.client.put(f'/api/wiki/{created["id"]}',json=edited,headers=self.headers['owner']).status_code,200)
        self.client.put(f'/api/wiki/{created["id"]}',json=edited,headers=self.headers['owner'])
        self.assertEqual(self.logs(action='update')['total'],1)
        with self.sessions() as db:
            obj=db.get(WikiArticle,created['id']); person=db.get(User,self.ids['owner'])
            before=audit.snapshot(db,'wiki',obj)
            audit.record(db,person,'delete','wiki',obj,before=before,after={})
            db.delete(obj); db.commit()
            person.display_name='改名后的张三'; db.commit()
        row=self.logs(action='delete')['items'][0]
        self.assertEqual((row['actor_name'],row['target_name']),('张三','新文章'))
        self.assertNotIn('新内容',json.dumps(self.details(row),ensure_ascii=False))
        self.assertEqual(self.logs(actor='张三',action='delete')['total'],1)
        self.assertEqual(self.logs(actor='改名后的张三',action='delete')['total'],0)

    def test_pagination_literal_search_and_beijing_date_boundaries(self):
        with self.sessions() as db:
            for i, stamp in enumerate(['2030-01-01T15:59:59','2030-01-01T16:00:00','2030-01-02T15:59:59','2030-01-02T16:00:00']):
                db.add(AuditLog(record_kind='business',actor_id=self.ids['owner'],actor_name='张三',actor_username='owner',
                    method='POST',path='/api/resources',action='create',target_type='resource',target_id=str(i),
                    target_code=f'RES-{i}',target_name='100%_项目' if i in [1,2] else '别的项目',summary='创建单据',status_code=200,
                    created_at=datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)))
            db.commit()
        params={'from_date':'2030-01-02','to_date':'2030-01-02','q':'100%_','page_size':1}
        first=self.logs(**params)
        self.assertEqual((first['total'],first['total_pages']), (2,2))
        self.assertEqual(first['items'][0]['target_code'],'RES-2')
        second=self.logs(**params,page=2)
        self.assertEqual(second['items'][0]['target_code'],'RES-1')
        self.assertNotIn('changes',second['items'][0])
        self.assertEqual(self.logs(**params,page=100)['page'],2)
        self.assertEqual(self.client.get('/api/audit',params={'from_date':'2030-01-03','to_date':'2030-01-01'},headers=self.headers['admin']).status_code,400)

    def test_legacy_rows_remain_request_logs_and_queries_do_not_modify_history(self):
        with self.sessions() as db:
            old=AuditLog(actor_id=self.ids['owner'],method='POST',path='/api/resources',action='POST /api/resources',
                         target_type='resources',target_id='',status_code=200)
            db.add(old); db.commit(); old_id=old.id
        self.assertEqual(self.logs()['total'],0)
        old=self.details({'id':old_id})
        self.assertEqual((old['record_kind'],old['changes']),('request',[]))
        self.assertEqual((old['target_code'],old['target_name']),('',''))
        self.assertIn('调用了接口',old['summary'])
        self.assertEqual(self.logs(record_kind='request',actor='张三')['total'],1)


class AuditUpgradeTests(unittest.TestCase):
    def test_original_audit_table_migrates_idempotently_without_guessing_business_details(self):
        with tempfile.TemporaryDirectory(prefix='audit-upgrade-') as folder:
            engine=create_engine('sqlite:///'+folder+'/old.db')
            with engine.begin() as conn:
                conn.execute(text('''CREATE TABLE audit_logs (id INTEGER PRIMARY KEY, actor_id INTEGER,
                    method VARCHAR(10) NOT NULL,path VARCHAR(255) NOT NULL,action VARCHAR(80) NOT NULL,
                    target_type VARCHAR(50) NOT NULL,target_id VARCHAR(50) NOT NULL,status_code INTEGER NOT NULL,
                    ip_address VARCHAR(80) NOT NULL,created_at DATETIME NOT NULL)'''))
                conn.execute(text("""INSERT INTO audit_logs VALUES (1,NULL,'DELETE','/api/old/7','DELETE /api/old/7',
                    'old','7',200,'127.0.0.1','2026-10-26 08:00:00')"""))
            upgrade(engine); upgrade(engine)
            with engine.connect() as conn:
                row=conn.execute(text('SELECT * FROM audit_logs')).mappings().one()
                self.assertEqual((row['record_kind'],row['target_id'],row['target_name'],row['changes']),('request','7','',None))
                self.assertEqual(row['path'],'/api/old/7')
            self.assertIn('ix_audit_logs_kind_time',{i['name'] for i in inspect(engine).get_indexes('audit_logs')})
            engine.dispose()
