"""v1.0.7: independent organization review, positional grants and safe revocation."""
import json
import time
import unittest
from unittest.mock import patch
from sqlalchemy import select, func
from fastapi import HTTPException
try:
    from . import test_organization as fixtures
except ImportError:
    import test_organization as fixtures
from app import flow_engine as flow, organization as org, main
from app.models import (User, UserRole, Department, DepartmentMembership, DepartmentManager,
                        DepartmentPosition, DepartmentUserPosition, ProjectMember, Approval,
                        WorkflowVersion, WorkflowTemplate, WorkflowInstance, SystemSetting)
from app.roles import effective_role_ids, direct_role_ids, SYSTEM_ROLES, RETIRED_HEAD_ROLES


class PositionApprovalTests(unittest.TestCase):
    def setUp(self):
        fixtures.OrganizationTests.setUp(self)
        notification = patch('app.main.send_notification')
        notification.start(); self.patches.append(notification)
    tearDown = fixtures.OrganizationTests.tearDown
    request = fixtures.OrganizationTests.request
    department = fixtures.OrganizationTests.department
    appoint = fixtures.OrganizationTests.appoint
    position = fixtures.OrganizationTests.position
    member = fixtures.OrganizationTests.member
    assign = fixtures.OrganizationTests.assign
    hierarchy = fixtures.OrganizationTests.hierarchy
    approval_definition = fixtures.OrganizationTests.approval_definition

    def publish(self, kind, definition=None):
        if definition is None:
            definition = self.approval_definition()
            if kind == 'license':
                definition['steps'][-1] = {'id':'final','name':'授权最终审批','type':'license_approval',
                    'mode':'all','assignee':{'source':'fixed','user_ids':[self.ids['outsider']]}}
        with self.sessions() as db:
            if not db.get(WorkflowTemplate, kind):
                db.add(WorkflowTemplate(kind=kind,draft=json.dumps(definition)))
                db.commit()
        current = self.request('GET',f'/admin/flows/{kind}').json()
        saved = self.request('PUT',f'/admin/flows/{kind}/draft',body={'definition':definition,'revision':current['revision']})
        self.assertEqual(saved.status_code,200,saved.text)
        result = self.request('POST',f'/admin/flows/{kind}/publish',body={'revision':saved.json()['revision']})
        self.assertEqual(result.status_code,200,result.text)
        return definition

    def body(self, kind, **extra):
        result = {'project_id':self.project_id,'application_department_id':self.depts['甲部']}
        if kind == 'resource':
            with self.sessions() as db:
                main.resource_catalog.seed(db);db.commit()
            result.update(environment='测试',resource_type='ECS',specification='CPU 2核',start_date='2027-01-01',end_date='2027-02-01',purpose='申请',capacity_basis='业务使用')
        elif kind == 'change':
            result.update(environment='生产',name='变更',change_type='配置变更',window_start='2027-01-01T10:00',window_end='2027-01-01T11:00',problem_description='调整配置')
        elif kind == 'delivery':
            result.update(name='交付',customer='客户',cloud_type='专有云',deployment_mode='虚拟机',environment='POC',compute_type='虚拟机',accelerator='无')
        else:
            result.update(customer='客户',product='产品',tenant_id=1,expire_time=int(time.time()*1000)+86400000,purpose='授权')
        result.update(extra)
        return result

    def create(self,kind='change',who='employee',**extra):
        path={'resource':'resources','change':'changes','delivery':'deliveries','license':'licenses'}[kind]
        response=self.request('POST','/'+path,who,self.body(kind,**extra))
        self.assertIn(response.status_code,[200,201],response.text)
        return response.json()['id']

    def tasks_for(self,kind,item):
        with self.sessions() as db:
            return list(db.scalars(select(Approval).where(Approval.object_type==kind,Approval.object_id==item,Approval.action=='pending').order_by(Approval.id)))

    def act(self,kind,item,who,**extra):
        task=next(t for t in self.tasks_for(kind,item) if t.assignee_id==self.ids[who])
        return self.request('POST',f'/{kind}/{item}/action',who,{'task_id':task.id,'action':'approve',**extra})

    def test_four_kinds_preview_submission_and_completion_agree(self):
        self.appoint()
        for kind in ['resource','change','delivery','license']:
            with self.subTest(kind=kind):
                definition=self.publish(kind)
                preview=self.request('POST',f'/admin/flows/{kind}/preview',body={
                    'definition':definition,'applicant_id':self.ids['employee'],'project_id':self.project_id,
                    'data':self.body(kind)})
                self.assertEqual(preview.status_code,200,preview.text)
                item=self.create(kind)
                task=self.tasks_for(kind,item)[0]
                self.assertEqual(task.assignee_id,self.ids['head'])
                self.assertTrue(json.loads(task.assignee_snapshot)['organization_manager'])
                self.assertEqual(self.act(kind,item,'head').status_code,200)
                if kind=='license':
                    with patch('app.licenses.generate_key',return_value='{"license":{"body":"signed","key":"public"}}'):
                        result=self.act(kind,item,'outsider')
                    self.assertEqual(result.status_code,200,result.text)
                    self.assertEqual(result.json()['status'],'generated')
                    self.assertIsNone(self.request('GET',f'/licenses/{item}').json()['license_key'])
                    self.assertTrue(self.request('GET',f'/licenses/{item}','employee').json()['license_key'])
                else:
                    result=self.act(kind,item,'operator',fulfillment='执行完成',implementation_plan='执行',rollback_plan='回滚')
                    self.assertEqual(result.status_code,200,result.text)
                    self.assertEqual(result.json()['status'],'completed')
                with self.sessions() as db:
                    snapshot=json.loads(db.scalar(select(WorkflowInstance).where(WorkflowInstance.object_type==kind,WorkflowInstance.object_id==item)).snapshot)
                    self.assertEqual(snapshot,preview.json())
                    self.assertEqual(snapshot['context']['department_id'],self.depts['甲部'])

    def test_multiple_departments_require_choice_and_reject_foreign_or_inactive(self):
        self.publish('change')
        self.assign([],department=self.depts['乙部'],target='employee',set_primary=False)
        for value in [None,self.depts['平台'],99999]:
            result=self.request('POST','/changes','employee',self.body('change',application_department_id=value))
            self.assertEqual(result.status_code,422,result.text)
        item=self.create(application_department_id=self.depts['乙部'])
        self.assertEqual(self.tasks_for('change',item)[0].assignee_id,self.ids['admin'])
        with self.sessions() as db:
            db.get(Department,self.depts['乙部']).active=False;db.commit()
        result=self.request('POST','/changes','employee',self.body('change',application_department_id=self.depts['乙部']))
        self.assertEqual(result.status_code,422)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(WorkflowInstance)),1)

    def test_nearest_heads_then_ancestors_then_independent_admin(self):
        root,parent,child=self.hierarchy()
        with self.sessions() as db:
            owner=db.get(User,self.ids['employee'])
            self.assertEqual([p['id'] for p in flow.organization_reviewers(db,owner,child['id'])],[self.ids['pm']])
            db.get(User,self.ids['pm']).active=False
            self.assertEqual([p['id'] for p in flow.organization_reviewers(db,owner,child['id'])],[self.ids['head']])
            db.get(User,self.ids['head']).active=False
            self.assertEqual([p['id'] for p in flow.organization_reviewers(db,owner,child['id'])],[self.ids['second']])
            db.get(User,self.ids['second']).active=False
            fallback=flow.organization_reviewers(db,owner,child['id'])
            self.assertEqual([p['id'] for p in fallback],[self.ids['admin']])
            self.assertTrue(fallback[0]['admin_fallback'])
            self.assertIsNone(fallback[0].get('department_id'))

    def test_head_with_co_head_always_starts_from_parent(self):
        root,parent,child=self.hierarchy()
        with self.sessions() as db:
            department=db.get(Department,child['id']);department.managers.append(DepartmentManager(user_id=self.ids['employee']))
            db.flush()
            self.assertEqual([p['id'] for p in flow.organization_reviewers(db,db.get(User,self.ids['employee']),child['id'])],[self.ids['head']])
            db.get(Department,parent['id']).parent_id=child['id']
            db.get(User,self.ids['head']).active=False
            db.get(User,self.ids['pm']).active=False
            # Legacy hierarchy cycles terminate and use the independent admin.
            self.assertEqual(flow.organization_reviewers(db,db.get(User,self.ids['employee']),child['id'])[0]['id'],self.ids['admin'])

    def test_admin_uses_own_membership_and_no_independent_person_leaves_no_objects(self):
        self.publish('change')
        result=self.request('POST','/changes',body=self.body('change',application_department_id=self.depts['平台']))
        self.assertEqual(result.status_code,409,result.text)
        self.assertIn('独立审批人',result.json()['detail'])
        forged=self.request('POST','/changes',body=self.body('change'))
        self.assertEqual(forged.status_code,422)
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(WorkflowInstance)))
            self.assertIsNone(db.scalar(select(main.ChangeRequest)))
            self.assertIsNone(db.scalar(select(Approval)))

    def test_enforced_first_review_cannot_be_removed_changed_or_replaced_by_self(self):
        self.appoint()
        definition=self.approval_definition()
        definition['steps'][0]={'id':'bypass','name':'申请人自审','type':'approval','mode':'quorum','required_count':99,
            'assignee':{'source':'fixed','user_ids':[self.ids['employee']]}}
        self.publish('change',definition)
        output=self.request('GET','/admin/flows/change').json()
        self.assertEqual(output['draft']['steps'][0],flow.department_first_definition(definition)['steps'][0])
        item=self.create()
        self.assertEqual(self.tasks_for('change',item)[0].assignee_id,self.ids['head'])
        definition['steps'].pop(0)
        self.publish('change',definition)
        second=self.create()
        self.assertEqual(self.tasks_for('change',second)[0].assignee_id,self.ids['head'])
        denied=self.request('POST',f'/change/{second}/action','employee',{'task_id':self.tasks_for('change',second)[0].id,'action':'approve'})
        self.assertEqual(denied.status_code,403)

    def test_migration_preserves_alias_permissions_projects_and_legacy_pending(self):
        with self.sessions() as db:
            person=db.get(User,self.ids['employee']);person.role_grants.append(UserRole(role='project_owner'))
            before={u.id:set(effective_role_ids(u)) for u in db.scalars(select(User))}
            members=[(m.project_id,m.user_id,m.role) for m in db.scalars(select(ProjectMember))]
            task=Approval(object_type='change',object_id=999,stage='test',assignee_id=person.id,
                assignee_snapshot=json.dumps({'required_role':'project_owner','roles':effective_role_ids(person)}))
            db.add(task);db.flush()
            org.migrate_business_grants_to_positions(db);db.commit();db.expire_all()
            for u in db.scalars(select(User)):
                self.assertTrue(before[u.id]<=set(effective_role_ids(u)))
                self.assertFalse(set(direct_role_ids(u))-SYSTEM_ROLES-RETIRED_HEAD_ROLES-{'department_head'})
            self.assertTrue(flow.eligible(db.get(User,self.ids['employee']),task))
            self.assertEqual(members,[(m.project_id,m.user_id,m.role) for m in db.scalars(select(ProjectMember))])
            counts=(db.scalar(select(func.count()).select_from(DepartmentPosition)),db.scalar(select(func.count()).select_from(DepartmentUserPosition)))
            org.migrate_business_grants_to_positions(db);db.commit()
            self.assertEqual(counts,(db.scalar(select(func.count()).select_from(DepartmentPosition)),db.scalar(select(func.count()).select_from(DepartmentUserPosition))))
            self.assertEqual(json.loads(db.get(SystemSetting,'organization_business_positions_v107').value)['unmapped_users'],[])

    def test_business_direct_grants_rejected_and_head_can_delegate_operator(self):
        self.appoint()
        for role in ['operator','ops_head','manager','sales_presales','project_owner']:
            result=self.request('PATCH',f'/users/{self.ids["employee"]}',body={'roles':['applicant',role]})
            self.assertEqual(result.status_code,422,result.text)
        position=self.position('运维执行',('operator',))
        assigned=self.assign([position['id']],who='head')
        self.assertEqual(assigned.status_code,200,assigned.text)
        self.assertNotIn('operator',assigned.json()['roles']);self.assertEqual(assigned.json()['positions'][0]['id'],position['id'])
        for role in ['admin','knowledge_admin','license_manager','department_head']:
            result=self.request('POST',f'/departments/{self.depts["甲部"]}/positions',body={'name':role,'roles':[role]})
            self.assertEqual(result.status_code,422,result.text)

    def test_pending_and_future_handlers_cannot_be_removed_before_transfer(self):
        self.appoint();self.publish('change');item=self.create()
        for endpoint,body in [(f'/users/{self.ids["head"]}',{'active':False}),
                (f'/users/{self.ids["operator"]}',{'active':False})]:
            changed=self.request('PATCH',endpoint,body=body)
            self.assertEqual(changed.status_code,409,changed.text)
        changed=self.request('PUT',f'/departments/{self.depts["甲部"]}/managers',body={
            'user_ids':[],'revision':self.department()['revision']})
        self.assertEqual(changed.status_code,409,changed.text)
        # Another appointed manager preserves the any-one threshold.
        self.assertEqual(self.appoint(('head','second')).status_code,200)
        task=self.tasks_for('change',item)[0]
        result=self.request('POST',f'/admin/tasks/{task.id}/reassign',body={'assignee_id':self.ids['second'],'reason':'负责人调整'})
        self.assertEqual(result.status_code,200,result.text)
        changed=self.request('PATCH',f'/users/{self.ids["head"]}',body={'active':False})
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(self.act('change',item,'second').status_code,200)

    def test_position_revocation_respects_future_execution_and_recovers_after_completion(self):
        with self.sessions() as db:
            org.migrate_business_grants_to_positions(db);db.commit()
            operator=db.get(User,self.ids['operator']);position=operator.position_grants[0].position
            pid,revision=position.id,position.revision
        self.appoint();self.publish('change');item=self.create()
        response=self.request('PATCH',f'/department-positions/{pid}',body={'name':'运维人员','roles':[], 'revision':revision})
        self.assertEqual(response.status_code,200,response.text)
        revision=response.json()['revision']
        self.assertEqual(self.act('change',item,'head').status_code,200)
        self.assertEqual(self.act('change',item,'operator',fulfillment='完成',implementation_plan='执行',rollback_plan='回滚').status_code,200)
        response=self.request('PATCH',f'/department-positions/{pid}',body={'name':'运维人员','roles':[], 'revision':revision})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('operator',self.member('operator')['roles'])

    def test_upgrade_publishes_once_without_changing_old_versions_or_instances(self):
        self.publish('change');item=self.create()
        with self.sessions() as db:
            before=[(v.id,v.definition) for v in db.scalars(select(WorkflowVersion))]
            instance=db.scalar(select(WorkflowInstance));old_snapshot=instance.snapshot
            flow.upgrade_department_first_approval(db);db.commit()
            count=db.scalar(select(func.count()).select_from(WorkflowVersion))
            flow.upgrade_department_first_approval(db);db.commit()
            self.assertEqual(count,db.scalar(select(func.count()).select_from(WorkflowVersion)))
            for uid,definition in before:self.assertEqual(db.get(WorkflowVersion,uid).definition,definition)
            self.assertEqual(db.get(WorkflowInstance,instance.id).snapshot,old_snapshot)

    def test_historical_application_department_prevents_deletion_and_is_audited(self):
        self.appoint();self.publish('change');item=self.create()
        with self.sessions() as db:
            department=db.get(Department,self.depts['甲部'])
            impact=org.deletion_impact(db,'department',department)
            self.assertFalse(impact['can_delete'])
            self.assertIn({'label':'申请部门历史引用','count':1},impact['blockers'])
            entry=main.operation_audit.snapshot(db,'change',db.get(main.ChangeRequest,item))
            self.assertIn('甲部',entry['application_department'])
