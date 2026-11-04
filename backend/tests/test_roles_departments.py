"""Role changes must preserve project and workflow access without cross-department grants."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import main, flow_engine as flow
from app.database import Base, get_db
from app.models import User, UserRole, Project, ProjectMember, Approval, AuditLog, WorkflowTemplate, WorkflowVersion
from app.roles import MANAGER_ROLE_SCOPE, ROLE_ALIASES, has_role, effective_role_ids
from app.rate_limit import RateDecision
from app.security import make_token


class DepartmentRoleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='role-department-')
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
            accounts = [('admin', ['admin']), ('applicant', ['applicant']),
                        ('worker', ['operator']), ('pm', ['project_manager']),
                        ('employee', ['applicant','tester']), ('outsider', ['applicant']),
                        ('outside_pm', ['project_manager'])]
            accounts += [(role, [role]) for role in MANAGER_ROLE_SCOPE]
            accounts += [('old_'+role, [role]) for role in ROLE_ALIASES]
            for name, roles in accounts:
                person = User(username=name, display_name=name, department='乙部' if name in {'outsider','outside_pm'} else '甲部',
                              role=roles[0], role_grants=[UserRole(role=r) for r in roles], password_hash='unused')
                db.add(person); db.flush(); self.ids[name] = person.id
            self.project_ids = []
            for dept, name in [('甲部','甲项目'),('乙部','乙项目')]:
                project = Project(name=name, department=dept, created_by=self.ids['admin'])
                db.add(project); db.flush(); self.project_ids.append(project.id)
                db.add(ProjectMember(project_id=project.id, user_id=self.ids['pm' if dept=='甲部' else 'outside_pm'], role='project_manager'))
            db.add(ProjectMember(project_id=self.project_ids[0], user_id=self.ids['applicant'], role='applicant'))
            db.commit()
        self.headers = {name:{'Authorization':'Bearer '+make_token(uid)} for name,uid in self.ids.items()}

    def tearDown(self):
        self.client.close(); main.app.dependency_overrides.pop(get_db,None)
        for item in reversed(self.patches): item.stop()
        self.engine.dispose(); self.folder.cleanup()

    def update(self, who, target='employee', **body):
        return self.client.patch('/api/users/'+str(self.ids[target]), json=body, headers=self.headers[who])

    def test_catalog_groups_and_new_roles_are_exact_and_legacy_names_are_hidden(self):
        response = self.client.get('/api/roles', headers=self.headers['admin'])
        self.assertEqual(response.status_code,200,response.text)
        groups = {}
        for role in response.json(): groups.setdefault(role['group'],set()).add(role['id'])
        self.assertEqual(groups['基础与系统'], {'admin','applicant','license_manager','knowledge_admin'})
        self.assertEqual(groups['项目与业务'], {'sales_presales','project_manager','agent_developer','product_developer'})
        self.assertTrue({'tester','commercial_head','manager','project_manager_head','technical_head','agent_development_head'} <= groups['审批与执行'])
        self.assertFalse(set(ROLE_ALIASES) & {r['id'] for r in response.json()})
        with self.sessions() as db:
            for legacy,new in ROLE_ALIASES.items():
                self.assertTrue(has_role(db.get(User,self.ids['old_'+legacy]),new))

    def test_all_four_managers_can_only_change_their_own_function_and_department(self):
        for manager, allowed in MANAGER_ROLE_SCOPE.items():
            with self.subTest(manager=manager):
                grant=next(iter(allowed))
                result=self.update(manager,roles=['applicant','tester',grant])
                self.assertEqual(result.status_code,200,result.text)
                self.assertEqual(set(result.json()['canonical_roles']), {'applicant','tester',grant})
                for body in [dict(roles=['applicant','tester',grant,'admin']), dict(roles=[grant]),
                             dict(roles=['applicant','tester',grant],department='乙部'),
                             dict(active=False),dict(password='StrongPass123!'),dict(display_name='改名')]:
                    self.assertEqual(self.update(manager,**body).status_code,403,body)
                self.assertEqual(self.update(manager,'outsider',roles=['applicant',grant]).status_code,403)
                self.assertEqual(self.update(manager,manager,roles=[manager,grant]).status_code,403)
                self.assertEqual(self.update(manager,'admin',roles=['admin',grant]).status_code,403)
                self.assertEqual(self.update(manager,roles=['applicant','tester']).status_code,200)
        with self.sessions() as db:
            self.assertEqual(set(effective_role_ids(db.get(User,self.ids['employee']))), {'applicant','tester'})
            operations=db.scalars(select(AuditLog).where(AuditLog.record_kind=='business',AuditLog.target_id==str(self.ids['employee']))).all()
            self.assertEqual(len(operations),8)

    def test_multiple_responsibilities_combine_without_global_admin_access(self):
        self.assertEqual(self.update('admin','technical_head',roles=['technical_head','agent_development_head']).status_code,200)
        result=self.update('technical_head',roles=['applicant','tester','product_developer','agent_developer'])
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.update('technical_head',roles=['applicant','tester','product_developer','agent_developer','sales_presales']).status_code,403)
        self.assertEqual(self.client.get('/api/admin/settings',headers=self.headers['technical_head']).status_code,403)
        self.assertEqual(self.client.post('/api/users',json={'username':'forbidden','display_name':'非法创建','roles':['applicant'],'password':'StrongPass123!'},headers=self.headers['technical_head']).status_code,403)

    def test_department_list_member_count_search_and_filters_are_scoped(self):
        who='commercial_head'
        listing=self.client.get('/api/users?page=1&page_size=50',headers=self.headers[who]).json()
        self.assertEqual(listing['total'],len(self.ids)-2)
        self.assertTrue(all(u['department']=='甲部' for u in listing['items']))
        self.assertTrue(all('feishu_open_id' not in u and 'feishu_open_id_set' not in u for u in listing['items']))
        filtered=self.client.get('/api/users?page=1&page_size=10&q=outsider',headers=self.headers[who]).json()
        self.assertEqual(filtered['total'],0)
        directory=self.client.get('/api/departments',headers=self.headers[who]).json()
        self.assertEqual([d['name'] for d in directory],['甲部'])
        self.assertTrue(all(role['id'] in set(self.ids.values()) for role in directory[0]['heads']))
        self.assertEqual(self.client.get('/api/departments',headers=self.headers['employee']).status_code,403)
        # Unpaged selectors retain project members in other departments, while
        # still withholding account and notification identity details.
        selector=self.client.get('/api/users',headers=self.headers[who]).json()
        self.assertIn(self.ids['outsider'],[u['id'] for u in selector])
        self.assertTrue(all('username' not in u for u in selector))
        legacy=self.client.get('/api/users?page=1&role=technical_head',headers=self.headers['admin']).json()
        self.assertEqual(legacy['total'],2)

    def test_department_changes_and_lead_revocation_take_effect_on_next_request(self):
        self.assertEqual(self.update('admin','employee',department='乙部').status_code,200)
        self.assertEqual(self.update('technical_head',roles=['applicant','tester','product_developer']).status_code,403)
        self.assertEqual(self.update('admin','technical_head',department='乙部').status_code,200)
        self.assertEqual(self.update('technical_head',roles=['applicant','tester','product_developer']).status_code,200)
        self.assertEqual(self.update('admin','technical_head',roles=['applicant']).status_code,200)
        self.assertEqual(self.update('technical_head',roles=['applicant','tester']).status_code,403)

    def test_lead_grants_require_department_and_admin_can_update_department_and_roles_together(self):
        result=self.client.post('/api/users',json={'username':'head_no_dept','display_name':'负责人','roles':['technical_head'],'password':'StrongPass123!'},headers=self.headers['admin'])
        self.assertEqual(result.status_code,400,result.text)
        self.assertEqual(self.update('admin','technical_head',department='   ').status_code,400)
        self.assertEqual(self.update('admin','technical_head',department='  新部门  ',roles=['commercial_head','tester']).status_code,200)
        person=self.client.get('/api/me',headers=self.headers['technical_head']).json()
        self.assertEqual(person['department'],'新部门')
        self.assertEqual(set(person['roles']), {'commercial_head','tester'})

    def definition(self, role='department_head'):
        return {'steps':[
            {'id':'head','name':'部门审核','type':'approval','mode':'all','assignee':{'source':'department_role','role':role}},
            {'id':'end','name':'运维执行','type':'execution','mode':'all','assignee':{'source':'fixed','user_ids':[self.ids['worker']]}}]}

    def test_department_approval_resolves_functional_heads_and_respects_live_roles_and_department(self):
        with self.sessions() as db:
            owner=db.get(User,self.ids['applicant'])
            result=flow.resolve(db,'resource',self.definition(),{},owner,self.project_ids[0])
            ids={p['id'] for p in result['path'][0]['assignees']}
            self.assertTrue({self.ids[r] for r in MANAGER_ROLE_SCOPE} <= ids)
            self.assertNotIn(owner.id,ids)
            spec=next(p for p in result['path'][0]['assignees'] if p['id']==self.ids['technical_head'])
            task=Approval(assignee_id=self.ids['technical_head'],assignee_snapshot=flow.dump(spec))
            head=db.get(User,self.ids['technical_head'])
            self.assertTrue(flow.eligible(head,task))
            head.department='乙部'
            self.assertFalse(flow.eligible(head,task))
            head.department='甲部'; head.role_grants=[UserRole(role='applicant')]
            self.assertFalse(flow.eligible(head,task))
            specific=flow.resolve(db,'resource',self.definition('commercial_head'),{},owner,self.project_ids[0])
            self.assertEqual({p['id'] for p in specific['path'][0]['assignees']}, {self.ids['commercial_head'],self.ids['old_sales_head'],self.ids['old_sales_director']})

    def test_department_project_management_does_not_allow_submission_or_other_positions(self):
        project_id=self.project_ids[0]
        listing=self.client.get('/api/projects',headers=self.headers['project_manager_head']).json()
        self.assertEqual([p['id'] for p in listing],[project_id])
        self.assertTrue(listing[0]['can_manage']); self.assertFalse(listing[0]['can_apply'])
        self.assertEqual(listing[0]['manageable_roles'],['project_manager'])
        with self.sessions() as db:
            self.assertEqual(db.scalars(flow.project_scope(select(Project),db.get(User,self.ids['project_manager_head']))).all(),[])
        members=listing[0]['members']
        base=[{'user_id':m['user_id'],'role':m['role']} for m in members]
        def save(who,values,pid=project_id):
            return self.client.put(f'/api/projects/{pid}/members',json={'members':values},headers=self.headers[who])
        invalid=base+[{'user_id':self.ids['employee'],'role':'project_manager'}]
        self.assertEqual(save('project_manager_head',invalid).status_code,400)
        self.assertEqual(self.update('admin',roles=['applicant','tester','project_manager']).status_code,200)
        self.assertEqual(save('project_manager_head',invalid).status_code,200)
        self.assertEqual(save('project_manager_head',invalid+[{'user_id':self.ids['worker'],'role':'operator'}]).status_code,403)
        self.assertEqual(save('project_manager_head',base,self.project_ids[1]).status_code,403)
        self.assertEqual(save('project_manager_head',base+[{'user_id':self.ids['outside_pm'],'role':'project_manager'}]).status_code,403)

    def test_legacy_roles_preserve_fixed_tasks_and_drafts_without_mutating_history(self):
        with self.sessions() as db:
            person=db.get(User,self.ids['old_leader'])
            task=Approval(assignee_id=person.id,assignee_snapshot=flow.dump({'roles':['leader']}))
            self.assertTrue(flow.eligible(person,task))
            person.role_grants=[UserRole(role='manager')]
            self.assertTrue(flow.eligible(person,task))
            person.role_grants=[UserRole(role='tester')]
            self.assertFalse(flow.eligible(person,task))
            raw=flow.dump({'steps':[{'id':'legacy','name':'领导审批','type':'approval','mode':'all','assignee':{'source':'project_role','role':'project_owner'}}, self.definition()['steps'][-1]]})
            db.add(WorkflowTemplate(kind='change',draft=raw)); db.add(WorkflowVersion(kind='change',version=1,definition=raw)); db.commit()
        result=self.client.get('/api/admin/flows/change',headers=self.headers['admin']).json()
        self.assertEqual(result['draft']['steps'][0]['name'],'管理者审批')
        self.assertEqual(result['draft']['steps'][0]['assignee']['role'],'project_manager')
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(WorkflowVersion)).definition,raw)
