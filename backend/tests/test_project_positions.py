"""Project appointments, legacy role retirement and in-flight compatibility."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select,func,delete
from sqlalchemy.orm import sessionmaker
from app import main,organization as org,flow_engine as flow
from app.database import Base,get_db
from app.models import (User,UserRole,Department,DepartmentMembership,DepartmentManager,DepartmentPosition,
    DepartmentPositionRole,DepartmentUserPosition,Project,ProjectMember,WorkflowTemplate,WorkflowVersion,
    WorkflowNode,WorkflowInstance,Approval,ChangeRequest,ResourceRequest,DeliveryProject,DeliveryCredential,LicenseRequest,SystemSetting)
from app.roles import role_ids
from app.project_position_upgrade import upgrade_project_positions
from app.security import make_token
from app.rate_limit import RateDecision


class ProjectPositionTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='project-positions-')
        self.engine=create_engine('sqlite:///'+str(Path(self.folder.name)/'test.sqlite'))
        Base.metadata.create_all(self.engine);self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        self.patches=[patch('app.main.SessionLocal',self.sessions),patch('app.main.check_request_rate',return_value=RateDecision()),patch('app.main.send_notification')]
        for p in self.patches:p.start()
        def dependency():
            with self.sessions() as db:yield db
        main.app.dependency_overrides[get_db]=dependency
        self.client=TestClient(main.app);self.ids={}
        with self.sessions() as db:
            dept=Department(name='研发');other=Department(name='其他');db.add_all([dept,other]);db.flush();self.did=dept.id
            for name in ['admin','head','owner','pm','ops','worker','outside','sales']:
                u=User(username=name,display_name=name,password_hash='retained',role='admin' if name=='admin' else 'applicant',department_id=other.id if name=='outside' else dept.id,department='其他' if name=='outside' else '研发',role_grants=[UserRole(role='admin' if name=='admin' else 'applicant')])
                db.add(u);db.flush();self.ids[name]=u.id;db.add(DepartmentMembership(user_id=u.id,department_id=u.department_id))
            db.add(DepartmentManager(user_id=self.ids['head'],department_id=dept.id))
            project=Project(name='项目甲',department='研发',department_id=dept.id,created_by=self.ids['admin']);db.add(project);db.flush();self.pid=project.id
            for who,role in [('pm','project_manager'),('owner','applicant'),('ops','ops_head'),('worker','operator')]:db.add(ProjectMember(project_id=project.id,user_id=self.ids[who],role=role))
            self.definition=flow.department_first_definition({'steps':[{'id':'first','name':'部门首审','type':'approval','mode':'any','assignee':{'source':'organization_manager'}},{'id':'ops_head','name':'运维负责人','type':'approval','mode':'any','assignee':{'source':'project_role','role':'ops_head'}},{'id':'end','name':'运维回填','type':'execution','mode':'all','assignee':{'source':'project_role','role':'operator'}}]})
            for kind in ['resource','change','delivery']:
                db.add(WorkflowTemplate(kind=kind,draft=flow.dump(self.definition)));db.add(WorkflowVersion(kind=kind,version=1,definition=flow.dump(self.definition)))
            db.commit()
        self.headers={name:{'Authorization':'Bearer '+make_token(uid)} for name,uid in self.ids.items()}
    def tearDown(self):
        self.client.close();main.app.dependency_overrides.pop(get_db,None)
        for p in reversed(self.patches):p.stop()
        self.engine.dispose();self.folder.cleanup()
    def req(self,method,path,body=None,who='admin'):
        return self.client.request(method,'/api'+path,headers=self.headers[who],**({'json':body} if body is not None else {}))
    def create(self):
        response=self.req('POST','/changes',{'project_id':self.pid,'application_department_id':self.did,'name':'变更验证','change_type':'配置变更','environment':'生产','window_start':'2027-01-01T10:00','window_end':'2027-01-01T11:00','problem_description':'项目岗位验证'},'owner')
        self.assertEqual(response.status_code,200,response.text);return response.json()['id']
    def members(self):
        return next(p['members'] for p in self.req('GET','/projects').json() if p['id']==self.pid)
    def test_catalog_separates_project_positions_and_hides_legacy_roles(self):
        roles=self.req('GET','/roles').json();self.assertEqual({r['id'] for r in roles},{'admin','applicant','license_manager','knowledge_admin','department_head'})
        positions=self.req('GET','/project-positions').json();ids={p['id'] for p in positions}
        self.assertTrue({'sales','presales','technical_lead','ops_head','operator'}<=ids);self.assertFalse({'manager','product_developer','sales_presales','department_head'}&ids)
        people=self.req('GET','/project-candidates',who='pm').json();self.assertTrue(self.ids['outside'] in {p['id'] for p in people});self.assertEqual(set(people[0]),{'id','display_name','department','active'})
    def test_create_project_without_department_and_assign_any_active_member(self):
        response=self.req('POST','/projects',{'name':'独立项目','active':True,'members':[{'user_id':self.ids['outside'],'role':'project_manager'},{'user_id':self.ids['owner'],'role':'technical_lead'}]})
        self.assertEqual(response.status_code,200,response.text);self.assertIsNone(response.json()['department_id']);self.assertTrue(all(m['eligible'] for m in response.json()['members']))
        self.assertEqual(len(self.req('GET','/projects',who='outside').json()),1)
    def test_project_manager_can_assign_other_positions_but_cannot_replace_manager(self):
        body={'members':self.members()+[{'user_id':self.ids['outside'],'role':'sales'}]}
        response=self.req('PUT',f'/projects/{self.pid}/members',body,'pm');self.assertEqual(response.status_code,200,response.text)
        body['members']=[m for m in body['members'] if m['role']!='project_manager']+[{'user_id':self.ids['outside'],'role':'project_manager'}]
        self.assertEqual(self.req('PUT',f'/projects/{self.pid}/members',body,'pm').status_code,403)
    def test_project_positions_do_not_grant_cross_project_access(self):
        self.assertEqual(self.req('GET','/projects',who='worker').json()[0]['id'],self.pid)
        self.assertEqual(self.req('GET','/projects',who='outside').json(),[])
        with self.sessions() as db:self.assertFalse(flow.project_manage_roles(db.get(Project,self.pid),db.get(User,self.ids['outside']),[]))
    def test_historical_delivery_credentials_use_the_related_project_operator(self):
        from app.security import encrypt_secret
        with self.sessions() as db:
            item=DeliveryProject(code='DEL-OLD-CREDENTIAL',name='历史交付',project_id=self.pid,owner_id=self.ids['owner'],
                customer='客户',cloud_type='公有云',environment='POC',deployment_mode='云平台',compute_type='云主机')
            db.add(item);db.flush()
            credential=DeliveryCredential(project_id=item.id,db_type='MySQL',host='db.internal',username='app',privilege='只读',password_ciphertext=encrypt_secret('test-only-value'))
            db.add(credential);db.flush();iid,cid=item.id,credential.id
            outside=db.get(User,self.ids['outside'])
            outside.role_grants.append(UserRole(role='operator'));db.commit()
        response=self.req('GET',f'/deliveries/{iid}/credentials/{cid}/reveal',who='worker')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['password'],'test-only-value')
        self.assertEqual(self.req('GET',f'/deliveries/{iid}/credentials/{cid}/reveal',who='outside').status_code,403)
    def test_resolve_uses_project_appointments_and_preserves_department_first_review(self):
        with self.sessions() as db:
            result=flow.resolve(db,'change',self.definition,{'application_department_id':self.did},db.get(User,self.ids['owner']),self.pid)
            self.assertEqual([[p['id'] for p in n['assignees']] for n in result['path']],[[self.ids['head']],[self.ids['ops']],[self.ids['worker']]])
            self.assertEqual(role_ids(db.get(User,self.ids['worker'])),['applicant'])
    def test_organization_job_removal_does_not_revoke_project_task(self):
        oid=self.create()
        with self.sessions() as db:
            job=DepartmentPosition(name='实施工程师',department_id=self.did,role_grants=[]);db.add(job);db.flush();db.add(DepartmentUserPosition(user_id=self.ids['worker'],department_id=self.did,position_id=job.id));db.commit()
        person=next(p for p in self.req('GET','/users').json() if p['id']==self.ids['worker'])
        response=self.req('PUT',f'/users/{self.ids["worker"]}/organization',{'department_id':self.did,'position_ids':[],'revision':person['organization_revision'],'set_primary':False})
        self.assertEqual(response.status_code,200,response.text)
        with self.sessions() as db:self.assertEqual(org.unavailable_workflow_nodes(db),set())
    def test_removing_project_handler_is_blocked_for_current_and_future_nodes(self):
        self.create();body={'members':[m for m in self.members() if m['role']!='operator']}
        response=self.req('PUT',f'/projects/{self.pid}/members',body)
        self.assertEqual(response.status_code,409,response.text)
        self.assertTrue(any(m['role']=='operator' for m in self.members()))
    def test_project_execution_completes_without_global_operator_role(self):
        oid=self.create()
        for who in ['head','ops','worker']:
            with self.sessions() as db:tid=db.scalar(select(Approval.id).where(Approval.object_id==oid,Approval.object_type=='change',Approval.action=='pending'))
            data={'task_id':tid,'action':'approve','comment':'项目任命验证'}
            if who=='worker':data.update(fulfillment='执行完成',implementation_plan='按计划执行',rollback_plan='恢复原配置')
            response=self.req('POST',f'/change/{oid}/action',data,who);self.assertEqual(response.status_code,200,response.text)
        with self.sessions() as db:self.assertEqual(db.get(ChangeRequest,oid).status,'completed')
    def test_retired_project_positions_and_new_department_role_grants_are_rejected(self):
        for role in ['manager','product_developer','department_head','sales_presales']:
            response=self.req('PUT',f'/projects/{self.pid}/members',{'members':self.members()+[{'user_id':self.ids['outside'],'role':role}]})
            self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(self.req('POST',f'/departments/{self.did}/positions',{'name':'旧角色授权','roles':['operator']}).status_code,422)
    def test_upgrade_does_not_restore_revoked_project_appointments_from_task_history(self):
        self.create()
        with self.sessions() as db:
            node=db.scalar(select(WorkflowNode).where(WorkflowNode.node_type=='execution'))
            node.assignment_policy=None
            db.execute(delete(ProjectMember).where(ProjectMember.project_id==self.pid,ProjectMember.user_id==self.ids['worker']))
            db.commit()
            upgrade_project_positions(db,main.OBJECTS);db.commit()
            self.assertIsNone(db.scalar(select(ProjectMember).where(ProjectMember.project_id==self.pid,ProjectMember.user_id==self.ids['worker'])))
            self.assertIn(node.id,org.unavailable_workflow_nodes(db))
    def test_upgrade_pending_license_uses_applicant_and_keeps_final_reviewer(self):
        with self.sessions() as db:
            item=LicenseRequest(code='LIC-UPGRADE',applicant_id=self.ids['owner'],project_id=self.pid,
                project_name='项目甲',customer='客户',product='产品',tenant_id=1,expire_time=1900000000000,
                purpose='验证在途授权迁移',status='pending',stage='license_final')
            db.add(item);db.flush()
            instance=WorkflowInstance(object_type='license',object_id=item.id,round=1,snapshot='{}',status='pending')
            db.add(instance);db.flush()
            node=WorkflowNode(instance_id=instance.id,position=0,name='最终审批',node_type='license_approval',
                mode='all',config=flow.dump({'assignee':{'source':'fixed','user_ids':[self.ids['ops']]},
                'assignees':[{'id':self.ids['ops']}]}),status='pending')
            db.add(node);db.flush()
            task=Approval(object_type='license',object_id=item.id,node_run_id=node.id,stage='license_final',
                assignee_id=self.ids['ops'],assignee_snapshot=flow.dump({'id':self.ids['ops']}))
            db.add(task);db.commit();original=item.applicant_id
            upgrade_project_positions(db,main.OBJECTS);db.commit()
            self.assertEqual(json.loads(node.assignment_policy),{'source':'fixed','owner_id':original})
            self.assertTrue(flow.eligible(db.get(User,self.ids['ops']),task))
            self.assertEqual(org.unavailable_workflow_nodes(db),set())
    def test_old_role_based_department_review_keeps_explicit_handler_without_changing_new_manager_review(self):
        oid=self.create()
        with self.sessions() as db:
            node=db.scalar(select(WorkflowNode).where(WorkflowNode.node_type=='approval',WorkflowNode.name=='运维负责人'))
            person=db.get(User,self.ids['ops']);person.role_grants.append(UserRole(role='department_head'))
            config=json.loads(node.config)
            config['assignee']={'source':'department_role','role':'department_head'}
            config['assignees']=[{'id':person.id,'name':person.display_name,'required_role':'department_head','roles':['department_head'],'department':'研发'}]
            node.config=flow.dump(config);node.assignment_policy=None;original=node.config
            db.commit();upgrade_project_positions(db,main.OBJECTS);db.commit()
            self.assertEqual(db.get(WorkflowNode,node.id).config,original)
            self.assertEqual(json.loads(node.assignment_policy)['source'],'fixed')
            first=db.scalar(select(WorkflowNode).where(WorkflowNode.name=='部门负责人审批'))
            self.assertIsNone(first.assignment_policy)
            self.assertEqual(org.unavailable_workflow_nodes(db),set())
    def test_completed_resource_contacts_keep_reminder_access_without_restoring_project_membership(self):
        from app import reminders
        with self.sessions() as db:
            item=ResourceRequest(code='RES-HISTORY',project_id=self.pid,project_name='项目甲',applicant_id=self.ids['owner'],
                environment='测试',resource_type='ECS',specification='1台',start_date='2026-11-01',end_date='2027-01-01',
                purpose='历史资源',capacity_basis='保留执行联系人',status='completed',stage='completed')
            db.add(item);db.flush()
            instance=WorkflowInstance(object_type='resource',object_id=item.id,round=1,snapshot='{}',status='completed')
            db.add(instance);db.flush()
            node=WorkflowNode(instance_id=instance.id,position=0,name='原运维回填',node_type='execution',mode='all',
                config=flow.dump({'assignee':{'source':'project_role','role':'operator'},'assignees':[{'id':self.ids['worker']}]}),status='completed')
            db.add(node);db.flush()
            db.add(Approval(object_type='resource',object_id=item.id,node_run_id=node.id,stage='operator',
                assignee_id=self.ids['worker'],actor_id=self.ids['worker'],action='approve',
                assignee_snapshot=flow.dump({'id':self.ids['worker'],'required_role':'operator','roles':['operator']})))
            db.execute(delete(ProjectMember).where(ProjectMember.project_id==self.pid,ProjectMember.user_id==self.ids['worker']))
            db.commit();upgrade_project_positions(db,main.OBJECTS);db.commit()
            person=db.get(User,self.ids['worker'])
            self.assertTrue(reminders.can_edit(db,'resource',item,person))
            self.assertFalse(reminders.can_edit(db,'resource',item,db.get(User,self.ids['outside'])))
            self.assertIsNone(db.scalar(select(ProjectMember).where(ProjectMember.project_id==self.pid,ProjectMember.user_id==person.id)))
            person.active=False;db.flush()
            self.assertFalse(reminders.can_edit(db,'resource',item,person))
    def test_new_resource_reminder_permissions_follow_project_operator_appointment(self):
        from app import reminders
        with self.sessions() as db:
            item=ResourceRequest(code='RES-NEW',project_id=self.pid,project_name='项目甲',applicant_id=self.ids['owner'],
                environment='测试',resource_type='ECS',specification='1台',start_date='2026-11-01',end_date='2027-01-01',
                purpose='项目资源',capacity_basis='项目执行人',status='completed',stage='completed')
            db.add(item);db.flush()
            instance=WorkflowInstance(object_type='resource',object_id=item.id,round=1,snapshot='{}',status='completed')
            db.add(instance);db.flush()
            node=WorkflowNode(instance_id=instance.id,position=0,name='项目运维回填',node_type='execution',mode='all',
                config='{}',assignment_policy=flow.dump({'source':'project_role','project_id':self.pid,'role':'operator','owner_id':self.ids['owner']}),status='completed')
            db.add(node);db.flush()
            db.add(Approval(object_type='resource',object_id=item.id,node_run_id=node.id,stage='operator',
                assignee_id=self.ids['worker'],actor_id=self.ids['worker'],action='approve'))
            db.flush();person=db.get(User,self.ids['worker'])
            self.assertTrue(reminders.can_edit(db,'resource',item,person))
            db.execute(delete(ProjectMember).where(ProjectMember.project_id==self.pid,ProjectMember.user_id==person.id));db.flush()
            self.assertFalse(reminders.can_edit(db,'resource',item,person))
    def test_migration_is_idempotent_and_preserves_jobs_history_and_all_sales_assignments(self):
        oid=self.create()
        with self.sessions() as db:
            user=db.get(User,self.ids['worker']);user.role_grants.append(UserRole(role='operator'))
            job=DepartmentPosition(name='实施工程师',department_id=self.did,role_grants=[DepartmentPositionRole(role='operator')]);db.add(job);db.flush();jid=job.id
            db.add(DepartmentUserPosition(user_id=user.id,department_id=self.did,position_id=jid))
            db.add_all([ProjectMember(project_id=self.pid,user_id=self.ids['sales'],role='sales_presales'),ProjectMember(project_id=self.pid,user_id=self.ids['outside'],role='department_head')]);db.commit()
            versions=[(v.id,v.definition) for v in db.scalars(select(WorkflowVersion))]
            instances=[(v.id,v.snapshot) for v in db.scalars(select(WorkflowInstance))]
            configs=[(v.id,v.config) for v in db.scalars(select(WorkflowNode))]
            tasks=[(v.id,v.assignee_snapshot,v.action) for v in db.scalars(select(Approval))]
            upgrade_project_positions(db,main.OBJECTS);db.commit()
            self.assertEqual(set(role_ids(db.get(User,user.id))),{'applicant'})
            self.assertEqual(db.get(DepartmentPosition,jid).name,'实施工程师');self.assertEqual(db.get(DepartmentPosition,jid).role_grants,[])
            self.assertIsNotNone(db.get(DepartmentUserPosition,(user.id,self.did,jid)))
            self.assertTrue({(self.ids['sales'],'sales'),(self.ids['sales'],'presales'),(self.ids['outside'],'technical_lead')}<={(m.user_id,m.role) for m in db.scalars(select(ProjectMember))})
            for vid,value in versions:self.assertEqual(db.get(WorkflowVersion,vid).definition,value)
            for iid,value in instances:self.assertEqual(db.get(WorkflowInstance,iid).snapshot,value)
            for nid,value in configs:self.assertEqual(db.get(WorkflowNode,nid).config,value)
            self.assertEqual(tasks,[(v.id,v.assignee_snapshot,v.action) for v in db.scalars(select(Approval))])
            count=db.scalar(select(func.count()).select_from(SystemSetting));upgrade_project_positions(db,main.OBJECTS);db.commit()
            self.assertEqual(count,db.scalar(select(func.count()).select_from(SystemSetting)))
            self.assertEqual(org.unavailable_workflow_nodes(db),set())
