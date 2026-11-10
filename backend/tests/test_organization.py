"""Directory integrity, delegated grants, live revocation and legacy compatibility."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func, event
from sqlalchemy.orm import sessionmaker
from app import main, organization as org, flow_engine as flow
from app.database import Base, get_db
from app.models import (User, UserRole, Department, DepartmentManager, DepartmentPosition,
                        Project, ProjectMember, Approval, DepartmentMembership, DepartmentAlias, DepartmentPositionRole,
                        DepartmentUserPosition, AuditLog, WorkflowTemplate, WorkflowVersion)
from app.roles import effective_role_ids
from app.rate_limit import RateDecision
from app.security import make_token


class OrganizationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='organization-test-')
        self.engine = create_engine('sqlite:///' + str(Path(self.folder.name) / 'test.db'))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [patch('app.main.SessionLocal', self.sessions),
                        patch('app.main.check_request_rate', return_value=RateDecision())]
        for item in self.patches: item.start()
        def dependency():
            with self.sessions() as db: yield db
        main.app.dependency_overrides[get_db] = dependency
        self.client = TestClient(main.app)
        self.ids = {}
        with self.sessions() as db:
            for name, roles, department in [
                ('admin',['admin'],'平台'), ('head',['applicant'],'甲部'),
                ('second',['applicant'],'甲部'), ('employee',['applicant','tester'],'甲部'),
                ('outsider',['applicant'],'乙部'), ('function',['commercial_head'],'甲部'),
                ('operator',['operator'],'平台'), ('pm',['project_manager'],'甲部')]:
                person = User(username=name,display_name=name,department=department,role=roles[0],
                              role_grants=[UserRole(role=r) for r in roles],password_hash='unused')
                db.add(person);db.flush();self.ids[name] = person.id
            org.seed_organization(db)
            self.depts = {d.name:d.id for d in db.scalars(select(Department))}
            project = Project(name='原项目',department='甲部',department_id=self.depts['甲部'],created_by=self.ids['admin'])
            db.add(project);db.flush();self.project_id=project.id
            db.add_all([ProjectMember(project_id=project.id,user_id=self.ids['employee'],role='applicant'),
                        ProjectMember(project_id=project.id,user_id=self.ids['pm'],role='project_manager')])
            db.commit()
        self.headers = {name:{'Authorization':'Bearer '+make_token(uid)} for name,uid in self.ids.items()}

    def tearDown(self):
        self.client.close();main.app.dependency_overrides.pop(get_db,None)
        for item in reversed(self.patches): item.stop()
        self.engine.dispose();self.folder.cleanup()

    def request(self, method, path, who='admin', body=None):
        return self.client.request(method,'/api'+path,headers=self.headers[who],**({'json':body} if body is not None else {}))

    def department(self, name='甲部'):
        return next(d for d in self.request('GET','/departments').json() if d['id']==self.depts[name])

    def appoint(self, names=('head',)):
        d=self.department()
        return self.request('PUT',f'/departments/{d["id"]}/managers',body={'user_ids':[self.ids[n] for n in names],'revision':d['revision']})

    def position(self, name='销售', roles=('sales_presales',), **extra):
        response=self.request('POST',f'/departments/{self.depts["甲部"]}/positions',body={'name':name,'roles':[],**extra})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def member(self, name='employee'):
        with self.sessions() as db: return main.user_out(db.get(User,self.ids[name]))

    def assign(self, positions, who='admin', target='employee', department=None, **extra):
        person=self.member(target)
        return self.request('PUT',f'/users/{person["id"]}/organization',who,{'department_id':department or person['department_id'],
            'position_ids':positions,'revision':person['organization_revision'],**extra})

    def test_empty_departments_and_id_only_user_and_project_writes(self):
        d=self.request('POST','/departments',body={'name':'业务一部'}).json()
        self.assertEqual((d['member_count'],d['heads'],d['positions']),(0,[],[]))
        body={'username':'newmember','display_name':'新成员','password':'SecurePass123!','roles':['applicant']}
        self.assertEqual(self.request('POST','/users',body={**body,'department':'自造部门'}).status_code,422)
        self.assertEqual(self.request('POST','/users',body={**body,'department_id':99999}).status_code,404)
        created=self.request('POST','/users',body={**body,'department_id':d['id']})
        self.assertEqual(created.status_code,200,created.text)
        self.assertEqual(created.json()['department'],'业务一部')
        project={'name':'新项目','department_id':d['id'],'members':[{'user_id':self.ids['pm'],'role':'project_manager'}]}
        self.assertEqual(self.request('POST','/projects',body=project).status_code,200)
        self.assertEqual(self.request('POST','/projects',body={**project,'department':'非法名称'}).status_code,422)

    def test_regular_members_only_read_public_options(self):
        options=self.request('GET','/organization/options','employee')
        self.assertEqual(options.status_code,200)
        self.assertTrue(all(set(d)<={'id','name','parent_id','path','positions'} for d in options.json()))
        self.assertEqual(self.request('GET','/departments','employee').status_code,403)
        self.assertEqual(self.request('POST','/departments','employee',{'name':'非法创建'}).status_code,403)
        self.assertEqual(self.request('PATCH','/me','employee',{'display_name':'本人','department_id':self.depts['乙部']}).status_code,422)

    def test_appointed_head_can_assign_only_own_ordinary_members_and_safe_positions(self):
        self.assertEqual(self.appoint(('head','second')).status_code,200)
        p=self.position()
        self.assertEqual(self.assign([p['id']],who='head').status_code,200)
        for target in ['head','second','admin','outsider']:
            response=self.assign([],who='head',target=target)
            self.assertEqual(response.status_code,403,(target,response.text))
        self.assertEqual(self.assign([],who='head',department=self.depts['乙部']).status_code,403)
        self.assertEqual(self.request('PATCH',f'/users/{self.ids["employee"]}','head',{'roles':['admin']}).status_code,403)
        listing=self.request('GET','/users?page=1','head').json()
        self.assertTrue(all(p['department_id']==self.depts['甲部'] for p in listing['items']))
        self.assertTrue(all('feishu_open_id' not in p for p in listing['items']))
        self.assertEqual([d['id'] for d in self.request('GET','/departments','head').json()],[self.depts['甲部']])

    def test_sources_merge_and_removing_one_source_preserves_other_grants(self):
        sales=self.position();presales=self.position('售前')
        self.assertEqual(self.assign([sales['id'],presales['id']]).status_code,200)
        self.assertNotIn('sales_presales',self.member()['roles']);self.assertTrue(self.member()['positions'])
        self.assertEqual(self.request('GET','/users?page=1&role=sales_presales').json()['total'],0)
        self.assertEqual(self.assign([presales['id']]).status_code,200)
        self.assertNotIn('sales_presales',self.member()['roles']);self.assertTrue(self.member()['positions'])
        self.assertEqual(self.assign([]).status_code,200)
        self.assertEqual(set(self.member()['roles']),{'applicant','tester'})
        rejected=self.request('PATCH',f'/users/{self.ids["employee"]}',body={'roles':['applicant','tester','sales_presales']})
        self.assertEqual(rejected.status_code,422)
        self.assign([sales['id']]);self.assign([])
        self.assertNotIn('sales_presales',self.member()['roles'])

    def test_non_delegatable_positions_are_enforced_on_server(self):
        self.appoint()
        p=self.position('运维',('operator',),manager_assignable=True)
        self.assertTrue(p['manager_assignable'])
        self.assertEqual(self.assign([p['id']],who='head').status_code,200)
        self.assertEqual(self.assign([p['id']]).status_code,200)
        self.assertEqual(self.request('POST',f'/departments/{self.depts["甲部"]}/positions',body={'name':'提权','roles':['admin']}).status_code,422)
        self.assertEqual(self.request('PATCH',f'/users/{self.ids["head"]}',body={'roles':['department_head']}).status_code,422)

    def test_old_functional_head_no_longer_manages_people_without_appointment(self):
        sales=self.position();custom=self.position('自定义',());other=self.position('开发',('product_developer',))
        self.assertEqual(self.assign([sales['id']],who='function').status_code,403)
        for p in (custom,other):
            self.assertEqual(self.assign([sales['id'],p['id']],who='function').status_code,403)

    def test_revocation_takes_effect_with_same_token_and_does_not_infer_appointments(self):
        self.appoint();p=self.position()
        self.assertTrue(self.request('GET','/me','head').json()['can_manage_people'])
        self.assertEqual(self.request('GET','/users?page=1&role=department_head').json()['total'],1)
        self.appoint(())
        self.assertFalse(self.request('GET','/me','head').json()['can_manage_people'])
        self.assertEqual(self.assign([p['id']],who='head').status_code,403)
        self.assertEqual(self.request('GET','/users?page=1&role=department_head').json()['total'],0)

    def test_rename_keeps_ids_legacy_tasks_and_department_conditions(self):
        self.appoint()
        d=self.department()
        renamed=self.request('PATCH',f'/departments/{d["id"]}',body={'name':'业务一部','revision':d['revision']})
        self.assertEqual(renamed.status_code,200,renamed.text)
        with self.sessions() as db:
            head=db.get(User,self.ids['head'])
            for spec in [{'required_role':'department_head','department':'甲部'},
                         {'required_role':'department_head','department':'甲部','department_id':d['id']}]:
                task=Approval(assignee_id=head.id,assignee_snapshot=json.dumps(spec))
                self.assertTrue(flow.eligible(head,task))
            self.assertEqual(db.get(Project,self.project_id).department,'业务一部')
            definition={'steps':[{'id':'branch','name':'部门条件','type':'branch','branches':[
                {'id':'yes','name':'命中','match':'all','conditions':[{'field':'department','op':'eq','value':'甲部'}],'steps':[]},
                {'id':'no','name':'默认','default':True,'steps':[]}]},
                {'id':'end','name':'执行','type':'execution','mode':'all','assignee':{'source':'fixed','user_ids':[self.ids['operator']]}}]}
            for value in ['甲部',f'department:{d["id"]}']:
                definition['steps'][0]['branches'][0]['conditions'][0]['value']=value
                resolved=flow.resolve(db,'resource',definition,{},db.get(User,self.ids['employee']),self.project_id)
                self.assertEqual(resolved['branches'][0]['matched'],'yes')
        self.assertEqual(self.request('POST','/departments',body={'name':'甲部'}).status_code,409)

    def test_revision_conflict_and_cross_department_positions_roll_back(self):
        p=self.position()
        before=self.member()
        self.assign([p['id']])
        stale=self.assign([],revision=before['organization_revision'])
        self.assertEqual(stale.status_code,409)
        self.assertEqual([p['id'] for p in self.member()['positions']],[p['id']])
        self.assertEqual(self.assign([p['id']],department=self.depts['乙部']).status_code,409)
        self.assertEqual(self.member()['department_id'],self.depts['甲部'])
        self.assertEqual(self.assign([99999]).status_code,404)

    def test_department_cycle_disabled_parent_and_usage_blocks(self):
        a=self.request('POST','/departments',body={'name':'父部'}).json()
        b=self.request('POST','/departments',body={'name':'子部','parent_id':a['id']}).json()
        self.assertEqual(self.request('PATCH',f'/departments/{a["id"]}',body={'name':a['name'],'parent_id':b['id'],'revision':a['revision']}).status_code,409)
        self.assertEqual(self.request('PATCH',f'/departments/{a["id"]}',body={'name':a['name'],'active':False,'revision':a['revision']}).status_code,409)
        d=self.department()
        result=self.request('PATCH',f'/departments/{d["id"]}',body={'name':d['name'],'active':False,'revision':d['revision']})
        self.assertEqual(result.status_code,409);self.assertIn('启用成员',result.text)
        inactive=self.request('POST','/departments',body={'name':'停用部','active':False}).json()
        self.assertEqual(self.request('POST','/departments',body={'name':'非法子部','parent_id':inactive['id']}).status_code,409)
        self.assertEqual(self.assign([],department=inactive['id']).status_code,409)

    def test_move_preserves_original_membership_positions_appointment_and_roles(self):
        self.appoint();p=self.position()
        self.assign([p['id']],target='head')
        moved=self.assign([],target='head',department=self.depts['乙部'])
        self.assertEqual(moved.status_code,200,moved.text)
        self.assertEqual([p['id'] for p in moved.json()['positions']],[p['id']])
        self.assertTrue(moved.json()['can_manage_people'])
        self.assertEqual(set(moved.json()['roles']),{'applicant','department_head'})
        self.assertEqual(self.department()['head_ids'],[self.ids['head']])
        self.assertEqual(set(moved.json()['department_ids']),{self.depts['甲部'],self.depts['乙部']})

    def test_position_disable_revokes_only_derived_role_and_keeps_assignment_history(self):
        p=self.position();self.assign([p['id']])
        result=self.request('PATCH',f'/department-positions/{p["id"]}',body={'name':p['name'],'roles':p['roles'],'active':False,'revision':p['revision']})
        self.assertEqual(result.status_code,200,result.text)
        person=self.member();self.assertNotIn('sales_presales',person['roles'])
        self.assertEqual(len(person['positions']),1);self.assertFalse(person['positions'][0]['active'])
        self.assertEqual(self.assign([p['id']],target='second').status_code,409)

    def test_migration_is_idempotent_and_never_creates_positions_or_managers(self):
        with self.sessions() as db:
            original={d.id:d.name for d in db.scalars(select(Department))}
            org.seed_organization(db);org.seed_organization(db);db.commit()
            self.assertEqual({d.id:d.name for d in db.scalars(select(Department))},original)
            self.assertEqual(db.scalar(select(func.count()).select_from(DepartmentManager)),0)
            self.assertEqual(db.scalar(select(func.count()).select_from(DepartmentPosition)),0)
            db.add_all([User(username='space1',display_name='1',department='新部',role='applicant',password_hash='x'),
                        User(username='space2',display_name='2',department=' 新部 ',role='applicant',password_hash='x')])
            db.flush()
            with self.assertRaisesRegex(RuntimeError,'部门名称冲突'):org.seed_organization(db)

    def test_read_only_rollback_mode_blocks_organization_changes(self):
        self.appoint();p=self.position()
        with patch.dict('os.environ',{'ORGANIZATION_WRITES_ENABLED':'false'}):
            self.assertFalse(self.request('GET','/me').json()['organization_writes_enabled'])
            self.assertEqual(self.request('POST','/departments',body={'name':'只读不能创建'}).status_code,409)
            self.assertEqual(self.assign([p['id']]).status_code,409)
            self.assertEqual(self.request('GET','/departments').status_code,200)
            self.assertEqual(self.request('PATCH',f'/users/{self.ids["employee"]}',body={'roles':['applicant','tester']}).status_code,200)

    def test_confirmed_managers_approval_uses_independent_fallback_and_never_self(self):
        definition = {'steps': [
            {'id': 'head', 'name': '部门审批', 'type': 'approval', 'mode': 'any',
             'assignee': {'source': 'department_role', 'role': 'department_head'}},
            {'id': 'execute', 'name': '执行', 'type': 'execution', 'mode': 'all',
             'assignee': {'source': 'fixed', 'user_ids': [self.ids['operator']]}}]}
        def resolve():
            with self.sessions() as db:
                return flow.resolve(db, 'resource', definition, {}, db.get(User, self.ids['employee']), self.project_id)
        self.assertEqual([p['id'] for p in resolve()['path'][0]['assignees']], [self.ids['admin']])
        self.appoint()
        self.assertEqual([p['id'] for p in resolve()['path'][0]['assignees']], [self.ids['head']])
        for names in [(), ('employee',)]:
            self.appoint(names)
            self.assertEqual([p['id'] for p in resolve()['path'][0]['assignees']], [self.ids['admin']])
        self.appoint(('head', 'second'))
        definition['steps'][0].update(mode='quorum', required_count=3)
        result=resolve()['path'][0]
        self.assertEqual(result['mode'],'any')
        self.assertEqual({p['id'] for p in result['assignees']},{self.ids['head'],self.ids['second']})

    def test_position_revision_conflict_after_another_admin_commits(self):
        position = self.position()
        original_lock = org.write_lock
        def concurrent_edit(db, actor, target_ids=()):
            with self.sessions() as other:
                current = other.get(DepartmentPosition, position['id'])
                current.name = '其他管理员已修改'
                current.revision += 1
                other.commit()
            return original_lock(db, actor, target_ids)
        with patch('app.organization.write_lock', side_effect=concurrent_edit):
            result = self.request('PATCH', f'/department-positions/{position["id"]}', body={
                'name': '旧表单覆盖', 'roles': position['roles'], 'revision': position['revision']})
        self.assertEqual(result.status_code, 409, result.text)
        with self.sessions() as db:
            self.assertEqual(db.get(DepartmentPosition, position['id']).name, '其他管理员已修改')

    def test_disabled_appointed_manager_is_removed_from_effective_role_filter(self):
        self.appoint()
        self.assertEqual(self.request('PATCH', f'/users/{self.ids["head"]}', body={'active': False}).status_code, 200)
        self.assertNotIn('department_head', self.member('head')['roles'])
        self.assertEqual(self.request('GET', '/users?page=1&status=disabled&role=department_head').json()['total'], 0)

    def hierarchy(self, appoint_parent=True):
        root=self.request('POST','/departments',body={'name':'集团'}).json()
        parent=self.department('乙部')
        parent=self.request('PATCH',f'/departments/{parent["id"]}',body={'name':parent['name'],'parent_id':root['id'],'revision':parent['revision']}).json()
        child=self.department()
        self.assertEqual(self.request('PATCH',f'/departments/{child["id"]}',body={'name':child['name'],'parent_id':parent['id'],'revision':child['revision']}).status_code,200)
        self.assign([],target='head',department=parent['id'])
        self.assign([],target='second',department=root['id'])
        for dept,names in [(root,['second']), (self.department(),['pm'])] + ([(parent,['head'])] if appoint_parent else []):
            result=self.request('PUT',f'/departments/{dept["id"]}/managers',body={'user_ids':[self.ids[n] for n in names],'revision':dept['revision']})
            self.assertEqual(result.status_code,200,result.text)
        return root,parent,child

    def approval_definition(self):
        return {'steps':[
            {'id':'first','name':'上级部门审核','type':'approval','mode':'any','assignee':{'source':'department_role','role':'department_head'}},
            {'id':'execute','name':'执行','type':'execution','mode':'all','assignee':{'source':'fixed','user_ids':[self.ids['operator']]}}]}

    def test_first_approval_uses_local_head_and_preserves_frozen_appointment(self):
        root,parent,child=self.hierarchy()
        with self.sessions() as db:
            result=flow.resolve(db,'resource',self.approval_definition(),{},db.get(User,self.ids['employee']),self.project_id)
            first=result['path'][0]['assignees']
            self.assertEqual([p['id'] for p in first],[self.ids['pm']])
            self.assertEqual(first[0]['department_id'],child['id'])
            self.assertTrue(first[0]['organization_manager'])
            self.assertEqual(result['context']['department_id'],child['id'])
            task=Approval(assignee_id=self.ids['pm'],assignee_snapshot=json.dumps(first[0]))
            self.assertTrue(flow.eligible(db.get(User,self.ids['pm']),task))
            db.get(Department,child['id']).parent_id=root['id']
            self.assertTrue(flow.eligible(db.get(User,self.ids['pm']),task))

    def test_missing_parent_manager_still_uses_available_local_manager(self):
        self.hierarchy(appoint_parent=False)
        with self.sessions() as db:
            result=flow.resolve(db,'resource',self.approval_definition(),{},db.get(User,self.ids['employee']),self.project_id)
            self.assertEqual(result['path'][0]['assignees'][0]['id'],self.ids['pm'])

    def test_admin_must_choose_own_department_instead_of_project_department(self):
        self.hierarchy()
        with self.sessions() as db:
            with self.assertRaises(HTTPException) as caught:
                flow.resolve(db,'resource',self.approval_definition(),{},db.get(User,self.ids['admin']),self.project_id)
            self.assertEqual(caught.exception.status_code,409)
            with self.assertRaises(HTTPException) as caught:
                flow.resolve(db,'resource',self.approval_definition(),{'application_department_id':self.depts['甲部']},db.get(User,self.ids['admin']),self.project_id)
            self.assertEqual(caught.exception.status_code,422)

    def test_top_level_approval_uses_own_appointment_and_current_scope_can_be_explicit(self):
        self.appoint()
        with self.sessions() as db:
            result=flow.resolve(db,'resource',self.approval_definition(),{},db.get(User,self.ids['employee']),self.project_id)
            self.assertEqual(result['path'][0]['assignees'][0]['id'],self.ids['head'])
        self.hierarchy()
        definition=self.approval_definition()
        definition['steps'][0]['assignee']['department_scope']='current'
        with self.sessions() as db:
            result=flow.resolve(db,'resource',definition,{},db.get(User,self.ids['employee']),self.project_id)
            self.assertEqual(result['path'][0]['assignees'][0]['id'],self.ids['pm'])

    def test_parent_counts_subtree_unique_people_and_list_matches_counts(self):
        root=self.request('POST','/departments',body={'name':'产研中心'}).json()
        for name in ['甲部','乙部']:
            d=self.department(name)
            self.assertEqual(self.request('PATCH',f'/departments/{d["id"]}',body={'name':d['name'],'parent_id':root['id'],'revision':d['revision']}).status_code,200)
        before={d['id']:d for d in self.request('GET','/departments').json()}
        # employee also joins the second child; grandparent must still count once.
        moved=self.assign([],department=self.depts['乙部'],set_primary=False)
        self.assertEqual(moved.status_code,200,moved.text)
        directory={d['id']:d for d in self.request('GET','/departments').json()}
        self.assertEqual(directory[root['id']]['member_count'],before[root['id']]['member_count'])
        self.assertEqual(directory[root['id']]['direct_member_count'],0)
        self.assertEqual(directory[self.depts['乙部']]['direct_member_count'],before[self.depts['乙部']]['direct_member_count']+1)
        listed=self.request('GET',f'/users?page=1&page_size=50&department_id={root["id"]}&include_descendants=true').json()
        self.assertEqual(listed['total'],directory[root['id']]['member_count'])
        self.assertEqual(len({p['id'] for p in listed['items']}),listed['total'])
        self.assertEqual(self.request('GET',f'/users?page=1&department_id={root["id"]}').json()['total'],0)

    def test_department_directory_query_count_stays_batched_as_pending_tasks_grow(self):
        with self.sessions() as db:
            employee=db.get(User,self.ids['employee'])
            department=db.scalar(select(Department).where(Department.name=='甲部'))
            positions=[]
            for name in ('批量岗位一','批量岗位二'):
                position=DepartmentPosition(department_id=department.id,name=name)
                db.add(position);db.flush()
                db.add(DepartmentPositionRole(position_id=position.id,role='sales_presales'))
                db.add(DepartmentUserPosition(user_id=employee.id,position_id=position.id,department_id=department.id))
                positions.append(position.id)
            for index in range(8):
                db.add(Department(name=f'批量部门{index}'))
            db.add_all([Approval(assignee_id=self.ids['employee'] if index < 3 else None,
                assignee_snapshot=json.dumps({'department':'甲部'}),object_type='resource',
                object_id=index+1,stage='department_head',action='pending') for index in range(40)])
            db.commit()

        statements=[]
        def capture(conn,cursor,statement,parameters,context,executemany):
            statements.append(statement)
        event.listen(self.engine,'before_cursor_execute',capture)
        try:
            response=self.request('GET','/departments')
        finally:
            event.remove(self.engine,'before_cursor_execute',capture)
        self.assertEqual(response.status_code,200,response.text)
        departments={department['name']:department for department in response.json()}
        self.assertEqual(departments['甲部']['pending_tasks'],40)
        self.assertEqual([position['pending_tasks'] for position in departments['甲部']['positions'][-2:]],[3,3])
        self.assertTrue(all(position['member_count']==1 and position['project_assignments']==1
                            for position in departments['甲部']['positions'][-2:]))
        self.assertLess(len(statements),80,f'department response used {len(statements)} SQL statements')

    def test_secondary_membership_roles_and_manager_permission_use_specific_department(self):
        self.appoint()
        old=self.position()
        self.assign([old['id']],target='head')
        self.assign([],target='head',department=self.depts['乙部'])
        # Manager's primary is now B but A appointment still permits local A duties.
        self.assertEqual([d['id'] for d in self.request('GET','/departments','head').json()],[self.depts['甲部']])
        self.assertEqual(self.assign([old['id']],who='head').status_code,200)
        other=self.request('POST',f'/departments/{self.depts["乙部"]}/positions',body={'name':'开发','roles':[]}).json()
        self.assertEqual(self.assign([other['id']],who='head',target='outsider').status_code,403)
        self.assertEqual(self.assign([other['id']],department=self.depts['乙部'],set_primary=False).status_code,200)
        person=self.member()
        self.assertNotIn('sales_presales',person['roles']);self.assertNotIn('product_developer',person['roles']);self.assertEqual(len(person['positions']),2)
        for role in ['sales_presales','product_developer']:
            self.assertEqual(self.request('GET','/users?page=1&role='+role).json()['total'],0)
        self.assertEqual(self.request('GET','/users?page=1&role=department_head').json()['total'],1)
        self.assertEqual(self.request('PATCH',f'/users/{person["id"]}','employee',{'roles':['admin']}).status_code,403)

    def test_approval_matches_secondary_manager_only_in_appointed_department(self):
        self.appoint()
        self.assign([],target='head',department=self.depts['乙部'])
        definition=self.approval_definition()
        definition['steps'][0]['assignee']['department_scope']='current'
        with self.sessions() as db:
            result=flow.resolve(db,'resource',definition,{},db.get(User,self.ids['employee']),self.project_id)
            snap=result['path'][0]['assignees'][0]
            self.assertEqual(snap['id'],self.ids['head'])
            self.assertTrue(flow.snapshot_role_matches(db.get(User,self.ids['head']),snap))
            wrong={**snap,'department_id':self.depts['乙部']}
            self.assertFalse(flow.snapshot_role_matches(db.get(User,self.ids['head']),wrong))
        # Secondary members may themselves be appointed there by the administrator.
        self.assign([],target='second',department=self.depts['乙部'],set_primary=False)
        d=self.department('乙部')
        appointed=self.request('PUT',f'/departments/{d["id"]}/managers',body={'revision':d['revision'],'user_ids':[self.ids['second']]})
        self.assertEqual(appointed.status_code,200,appointed.text)
        self.assertEqual(set(self.member('second')['managed_department_ids']),{d['id']})

    def test_admin_removes_any_department_and_last_membership_without_restoring_it(self):
        self.appoint();p=self.position();self.assign([p['id']],target='head')
        other=self.request('POST',f'/departments/{self.depts["乙部"]}/positions',body={'name':'开发','roles':[]}).json()
        self.assign([other['id']],target='head',department=self.depts['乙部'],set_primary=False)
        with self.sessions() as db:
            db.add(ProjectMember(project_id=self.project_id,user_id=self.ids['head'],role='project_manager'))
            db.commit()
        person=self.member('head')
        base=f'/users/{person["id"]}/departments/'
        self.assertEqual(self.request('DELETE',base+str(self.depts['甲部'])+f'?revision={person["organization_revision"]}','employee').status_code,403)
        self.assertEqual(self.request('DELETE',base+str(self.depts['甲部'])+f'?revision={person["organization_revision"]}','head').status_code,403)
        removed=self.request('DELETE',base+str(self.depts['甲部'])+f'?revision={person["organization_revision"]}')
        self.assertEqual(removed.status_code,200,removed.text)
        self.assertEqual(removed.json()['department_ids'],[self.depts['乙部']])
        self.assertEqual(removed.json()['department_id'],self.depts['乙部'])
        self.assertEqual([p['id'] for p in removed.json()['positions']],[other['id']])
        self.assertEqual(set(removed.json()['roles']),{'applicant'})
        self.assertEqual(removed.json()['direct_roles'],['applicant'])
        self.assertFalse(removed.json()['can_manage_people'])
        self.assertEqual(self.department()['head_ids'],[])
        self.assertEqual(self.request('DELETE',base+str(self.depts['甲部'])+f'?revision={person["organization_revision"]}').status_code,409)
        self.assertEqual(self.request('DELETE',base+str(self.depts['甲部'])+f'?revision={removed.json()["organization_revision"]}').status_code,404)
        final=self.request('DELETE',base+str(self.depts['乙部'])+f'?revision={removed.json()["organization_revision"]}')
        self.assertEqual(final.status_code,200,final.text)
        self.assertEqual(final.json()['department_ids'],[])
        self.assertIsNone(final.json()['department_id'])
        self.assertEqual(final.json()['department'],'')
        self.assertEqual(final.json()['positions'],[])
        self.assertEqual(final.json()['roles'],['applicant'])
        with self.sessions() as db:
            org.seed_organization(db);org.seed_organization(db);db.commit()
            person=db.get(User,self.ids['head'])
            self.assertEqual(org.membership_ids(person),set())
            self.assertEqual(db.scalar(select(func.count()).select_from(ProjectMember).where(ProjectMember.user_id==person.id)),1)
        # A member with no department can be assigned again through the same entrypoint.
        joined=self.assign([p['id']],target='head',department=self.depts['甲部'],set_primary=False)
        self.assertEqual(joined.status_code,200,joined.text)
        self.assertEqual(joined.json()['department_ids'],[self.depts['甲部']])

    def test_membership_backfill_idempotent_and_existing_secondary_not_reset(self):
        self.assign([],department=self.depts['乙部'],set_primary=False)
        with self.sessions() as db:
            count=db.scalar(select(func.count()).select_from(DepartmentMembership))
            org.seed_organization(db);org.seed_organization(db);db.commit()
            self.assertEqual(db.scalar(select(func.count()).select_from(DepartmentMembership)),count)
            self.assertEqual(db.get(User,self.ids['employee']).department_id,self.depts['甲部'])
            self.assertEqual(len(db.get(User,self.ids['employee']).department_memberships),2)

    def test_admin_deletes_empty_department_and_position_with_cleanup_and_audit(self):
        d=self.request('POST','/departments',body={'name':'可删除部门'}).json()
        p=self.request('POST',f'/departments/{d["id"]}/positions',body={'name':'可删除岗位','roles':[]}).json()
        impact=self.request('GET',f'/departments/{d["id"]}/deletion-impact').json()
        self.assertFalse(impact['can_delete'])
        self.assertEqual(impact['blockers'],[{'label':'部门岗位','count':1}])
        self.assertEqual(self.request('DELETE',f'/departments/{d["id"]}?revision={d["revision"]}').status_code,409)
        self.assertTrue(self.request('GET',f'/department-positions/{p["id"]}/deletion-impact').json()['can_delete'])
        self.assertEqual(self.request('DELETE',f'/department-positions/{p["id"]}?revision={p["revision"]}').status_code,200)
        self.assertEqual(self.request('DELETE',f'/departments/{d["id"]}?revision={d["revision"]}').status_code,200)
        with self.sessions() as db:
            self.assertIsNone(db.get(Department,d['id']))
            self.assertIsNone(db.get(DepartmentPosition,p['id']))
            self.assertIsNone(db.get(DepartmentAlias,d['name']))
            self.assertEqual(db.scalar(select(func.count()).select_from(DepartmentPositionRole)),0)
            self.assertIsNotNone(db.get(Department,self.depts['甲部']))
            logs=list(db.scalars(select(AuditLog).where(AuditLog.record_kind=='business',AuditLog.action=='delete')))
            self.assertEqual({log.target_type for log in logs},{'department','department_position'})
            self.assertTrue(all(log.actor_id==self.ids['admin'] for log in logs))

    def test_deletion_preview_and_write_require_system_admin(self):
        self.appoint();p=self.position()
        d=self.request('POST','/departments',body={'name':'空部门'}).json()
        for who in ['head','employee','outsider']:
            for path,revision in [(f'/departments/{d["id"]}',d['revision']), (f'/department-positions/{p["id"]}',p['revision'])]:
                self.assertEqual(self.request('GET',path+'/deletion-impact',who).status_code,403)
                self.assertEqual(self.request('DELETE',path+f'?revision={revision}',who).status_code,403)
        with self.sessions() as db:
            self.assertIsNotNone(db.get(Department,d['id']));self.assertIsNotNone(db.get(DepartmentPosition,p['id']))

    def test_delete_blocks_secondary_disabled_members_and_assigned_positions(self):
        d=self.request('POST','/departments',body={'name':'兼职部门'}).json()
        p=self.request('POST',f'/departments/{d["id"]}/positions',body={'name':'兼职岗位','roles':[]}).json()
        self.assign([p['id']],department=d['id'],set_primary=False)
        self.request('PATCH',f'/users/{self.ids["employee"]}',body={'active':False})
        di=self.request('GET',f'/departments/{d["id"]}/deletion-impact').json()
        self.assertIn({'label':'直属成员（含兼职及停用成员）','count':1},di['blockers'])
        pi=self.request('GET',f'/department-positions/{p["id"]}/deletion-impact').json()
        self.assertEqual(pi['blockers'],[{'label':'岗位成员（含停用成员）','count':1}])
        self.assertEqual(self.request('DELETE',f'/department-positions/{p["id"]}?revision={p["revision"]}').status_code,409)
        self.assertEqual(self.request('DELETE',f'/departments/{d["id"]}?revision={d["revision"]}').status_code,409)
        with self.sessions() as db:
            self.assertIsNotNone(db.get(DepartmentUserPosition,(self.ids['employee'],p['id'],d['id'])))
            self.assertFalse(db.get(User,self.ids['employee']).active)
            self.assertEqual(db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='delete',AuditLog.record_kind=='business')),0)

    def test_delete_blocks_inactive_child_project_appointments_and_pending_snapshot(self):
        d=self.request('POST','/departments',body={'name':'历史关联部'}).json()
        self.request('POST','/departments',body={'name':'停用子部','parent_id':d['id'],'active':False})
        with self.sessions() as db:
            db.add(Project(name='停用项目',department=d['name'],department_id=d['id'],active=False,created_by=self.ids['admin']))
            db.add(DepartmentManager(department_id=d['id'],user_id=self.ids['head']))
            db.add(Approval(object_type='resource',object_id=self.project_id,stage='head',assignee_id=self.ids['head'],
                           action='pending',assignee_snapshot=json.dumps({'department_id':d['id']})))
            db.commit()
        impact=self.request('GET',f'/departments/{d["id"]}/deletion-impact').json()
        labels={b['label'] for b in impact['blockers']}
        self.assertTrue({'子部门','关联项目','负责人任命','未完成部门审批'}<=labels)
        self.assertEqual(self.request('DELETE',f'/departments/{d["id"]}?revision={d["revision"]}').status_code,409)

    def test_delete_blocks_current_flow_references_but_preserves_old_versions(self):
        d=self.request('POST','/departments',body={'name':'原名称'}).json()
        renamed=self.request('PATCH',f'/departments/{d["id"]}',body={'name':'新名称','revision':d['revision']}).json()
        raw=json.dumps({'steps':[{'conditions':[{'field':'department','value':'原名称'}]}]})
        with self.sessions() as db:
            db.add(WorkflowTemplate(kind='resource',draft=raw))
            db.add(WorkflowVersion(kind='resource',version=1,definition=raw));db.commit()
        impact=self.request('GET',f'/departments/{d["id"]}/deletion-impact').json()
        self.assertEqual({b['label'] for b in impact['blockers']},{'审批流程草稿引用','现行已发布流程引用'})
        self.assertEqual(self.request('DELETE',f'/departments/{d["id"]}?revision={renamed["revision"]}').status_code,409)
        with self.sessions() as db:
            db.get(WorkflowTemplate,'resource').draft='{"steps":[]}'
            db.add(WorkflowVersion(kind='resource',version=2,definition='{"steps":[]}'));db.commit()
        self.assertEqual(self.request('DELETE',f'/departments/{d["id"]}?revision={renamed["revision"]}').status_code,200)
        with self.sessions() as db:
            original=db.scalar(select(WorkflowVersion).where(WorkflowVersion.version==1))
            self.assertEqual(original.definition,raw)

    def test_delete_rechecks_associations_after_preview_and_rejects_stale_revision(self):
        d=self.request('POST','/departments',body={'name':'预览后变更'}).json()
        self.assertTrue(self.request('GET',f'/departments/{d["id"]}/deletion-impact').json()['can_delete'])
        # Membership changes do not bump department revision: final writer must recheck.
        self.assign([],department=d['id'],set_primary=False)
        self.assertEqual(self.request('DELETE',f'/departments/{d["id"]}?revision={d["revision"]}').status_code,409)
        p=self.position()
        self.request('PATCH',f'/department-positions/{p["id"]}',body={'name':'新岗位名','roles':p['roles'],'revision':p['revision']})
        self.assertEqual(self.request('DELETE',f'/department-positions/{p["id"]}?revision={p["revision"]}').status_code,409)
        other=self.request('POST','/departments',body={'name':'空待删部'}).json()
        self.request('PATCH',f'/departments/{other["id"]}',body={'name':'改名后','revision':other['revision']})
        self.assertEqual(self.request('DELETE',f'/departments/{other["id"]}?revision={other["revision"]}').status_code,409)

    def test_delete_respects_maintenance_and_returns_404_for_missing_or_repeated_delete(self):
        d=self.request('POST','/departments',body={'name':'维护保护部'}).json();p=self.position()
        with patch.dict('os.environ',{'ORGANIZATION_WRITES_ENABLED':'false'}):
            self.assertEqual(self.request('DELETE',f'/departments/{d["id"]}?revision={d["revision"]}').status_code,409)
            self.assertEqual(self.request('DELETE',f'/department-positions/{p["id"]}?revision={p["revision"]}').status_code,409)
            self.assertEqual(self.request('GET',f'/departments/{d["id"]}/deletion-impact').status_code,200)
        self.assertEqual(self.request('DELETE',f'/departments/{d["id"]}?revision={d["revision"]}').status_code,200)
        self.assertEqual(self.request('DELETE',f'/departments/{d["id"]}?revision={d["revision"]}').status_code,404)
        self.assertEqual(self.request('DELETE','/department-positions/999999?revision=1').status_code,404)
