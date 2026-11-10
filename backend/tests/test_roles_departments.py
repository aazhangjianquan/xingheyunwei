"""Unified organization management retires functional grants without rewriting history."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app import main, organization as org, flow_engine as flow
from app.database import Base, get_db
from app.models import User, UserRole, Project, ProjectMember, Approval, Department, WorkflowTemplate, WorkflowVersion
from app.roles import RETIRED_HEAD_ROLES, ROLE_ALIASES, has_role, direct_role_ids
from app.security import make_token


class DepartmentRoleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='unified-department-')
        self.engine = create_engine('sqlite:///' + str(Path(self.folder.name) / 'test.db'))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.patch = patch('app.main.SessionLocal', self.sessions)
        self.patch.start()
        def dependency():
            with self.sessions() as db: yield db
        main.app.dependency_overrides[get_db] = dependency
        self.client = TestClient(main.app)
        self.ids = {}
        with self.sessions() as db:
            accounts = [('admin',['admin'],'甲部'),('employee',['applicant','tester'],'甲部'),
                        ('head',['applicant'],'甲部'),('worker',['operator'],'乙部'),
                        ('pm',['project_manager'],'甲部'),('outside',['applicant'],'乙部')]
            accounts += [(r,[r],'甲部') for r in RETIRED_HEAD_ROLES]
            accounts += [('old_'+r,[r],'甲部') for r in ROLE_ALIASES]
            for name,roles,dept in accounts:
                p=User(username=name,display_name=name,department=dept,role=roles[0],
                    role_grants=[UserRole(role=r) for r in roles],password_hash='retained-hash')
                db.add(p);db.flush();self.ids[name]=p.id
            org.seed_organization(db)
            self.depts={d.name:d.id for d in db.scalars(select(Department))}
            project=Project(name='原项目',department='甲部',department_id=self.depts['甲部'],created_by=self.ids['admin'])
            db.add(project);db.flush();self.project_id=project.id
            db.add_all([ProjectMember(project_id=project.id,user_id=self.ids['employee'],role='applicant'),
                        ProjectMember(project_id=project.id,user_id=self.ids['pm'],role='project_manager')])
            db.commit()
        self.headers={name:{'Authorization':'Bearer '+make_token(uid)} for name,uid in self.ids.items()}

    def tearDown(self):
        self.client.close();main.app.dependency_overrides.pop(get_db,None)
        self.patch.stop();self.engine.dispose();self.folder.cleanup()

    def request(self,method,path,who='admin',body=None):
        return self.client.request(method,'/api'+path,headers=self.headers[who],**({'json':body} if body is not None else {}))

    def test_catalog_hides_retired_roles_and_old_aliases(self):
        roles=self.request('GET','/roles').json()
        self.assertFalse((RETIRED_HEAD_ROLES | set(ROLE_ALIASES)) & {r['id'] for r in roles})
        self.assertTrue(all(not r['managed_by'] for r in roles))
        self.assertEqual(next(r for r in roles if r['id']=='department_head')['assignment_source'],'department')
        with self.sessions() as db:
            for legacy,current in ROLE_ALIASES.items():
                self.assertEqual(has_role(db.get(User,self.ids['old_'+legacy]),current),current not in RETIRED_HEAD_ROLES)

    def test_retired_heads_have_no_current_people_or_project_management_authority(self):
        for role in RETIRED_HEAD_ROLES:
            with self.subTest(role=role):
                self.assertFalse(self.request('GET','/me',role).json()['can_manage_people'])
                self.assertEqual(self.request('GET','/departments',role).status_code,403)
                self.assertEqual(self.request('PATCH',f'/users/{self.ids["employee"]}',role,{'roles':['applicant','sales_presales']}).status_code,403)
                self.assertEqual(self.request('PUT',f'/projects/{self.project_id}/members',role,{'members':[]}).status_code,403)
                self.assertEqual(self.request('GET','/users?page=1&role='+role).json()['total'],0)

    def test_new_retired_grants_are_rejected_but_original_direct_grants_are_preserved(self):
        for role in RETIRED_HEAD_ROLES | {'sales_head','project_director','development_head'}:
            with self.subTest(role=role):
                self.assertEqual(self.request('PATCH',f'/users/{self.ids["employee"]}',body={'roles':['applicant',role]}).status_code,422)
                self.assertEqual(self.request('POST','/users',body={'username':'new-'+role,'display_name':'新成员',
                    'department_id':self.depts['甲部'],'roles':[role],'password':'SecurePass123!'}).status_code,422)
                self.assertEqual(self.request('POST',f'/departments/{self.depts["甲部"]}/positions',body={'name':'旧负责人','roles':[role]}).status_code,422)
        for role in RETIRED_HEAD_ROLES:
            response=self.request('PATCH',f'/users/{self.ids[role]}',body={'display_name':'改名'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['direct_roles'],[role])
            self.assertNotIn(role,response.json()['roles'])

    def test_cross_department_move_keeps_original_direct_grants_and_project_memberships(self):
        person=next(p for p in self.request('GET','/users').json() if p['id']==self.ids['employee'])
        moved=self.request('PUT',f'/users/{person["id"]}/organization',body={'department_id':self.depts['乙部'],
            'position_ids':[],'revision':person['organization_revision']})
        self.assertEqual(moved.status_code,200,moved.text)
        self.assertEqual(set(moved.json()['direct_roles']),{'applicant','tester'})
        with self.sessions() as db:
            self.assertIsNotNone(db.get(ProjectMember,(self.project_id,person['id'],'applicant')))
            self.assertEqual(db.get(User,person['id']).password_hash,'retained-hash')
        stale=self.request('PUT',f'/users/{person["id"]}/organization',body={'department_id':self.depts['甲部'],
            'position_ids':[],'revision':person['organization_revision']})
        self.assertEqual(stale.status_code,409)

    def test_historical_approval_grants_survive_only_for_existing_tasks(self):
        with self.sessions() as db:
            person=db.get(User,self.ids['technical_head'])
            fixed=Approval(assignee_id=person.id,assignee_snapshot=json.dumps({'roles':['technical_head']}))
            department=Approval(assignee_id=person.id,assignee_snapshot=json.dumps({'required_role':'department_head','department':'甲部'}))
            self.assertTrue(flow.eligible(person,fixed));self.assertTrue(flow.eligible(person,department))
            current=Approval(assignee_id=person.id,assignee_snapshot=json.dumps({'required_role':'department_head',
                'department_id':self.depts['甲部'],'organization_manager':True}))
            self.assertFalse(flow.eligible(person,current))
            person.role_grants=[UserRole(role='applicant')]
            self.assertFalse(flow.eligible(person,fixed));self.assertFalse(flow.eligible(person,department))

    def test_current_organization_appointment_is_the_only_people_management_source(self):
        department=next(d for d in self.request('GET','/departments').json() if d['id']==self.depts['甲部'])
        self.assertEqual(self.request('PUT',f'/departments/{department["id"]}/managers',body={'user_ids':[self.ids['head']], 'revision':department['revision']}).status_code,200)
        self.assertTrue(self.request('GET','/me','head').json()['can_manage_people'])
        self.assertEqual([d['id'] for d in self.request('GET','/departments','head').json()],[department['id']])
        self.assertEqual(self.request('PATCH',f'/users/{self.ids["employee"]}','head',{'roles':['applicant','tester']}).status_code,403)
        listing=self.request('GET','/users?page=1&page_size=50','head').json()['items']
        self.assertTrue(all(p['department_id']==department['id'] for p in listing))

    def test_default_workflow_upgrade_adds_parent_step_and_preserves_published_history(self):
        original={'steps':[{'id':'ops','name':'运维审核','type':'approval','mode':'any','assignee':{'source':'fixed','user_ids':[self.ids['worker']]}},
            {'id':'end','name':'执行','type':'execution','mode':'all','assignee':{'source':'fixed','user_ids':[self.ids['worker']]}}]}
        raw=json.dumps(original,ensure_ascii=False)
        with self.sessions() as db:
            db.add(WorkflowTemplate(kind='change',draft=raw))
            db.add(WorkflowVersion(kind='change',version=1,definition=raw));db.commit()
            flow.upgrade_organization_approval_defaults(db);db.commit()
            versions=list(db.scalars(select(WorkflowVersion).order_by(WorkflowVersion.version)))
            self.assertEqual(len(versions),2);self.assertEqual(versions[0].definition,raw)
            new=json.loads(versions[1].definition)
            self.assertEqual(new['steps'][0]['assignee'],{'source':'department_role','role':'department_head','department_scope':'parent'})
            self.assertEqual(new['steps'][1:],original['steps'])
            flow.upgrade_organization_approval_defaults(db);db.commit()
            self.assertEqual(len(list(db.scalars(select(WorkflowVersion)))),2)

    def test_default_upgrade_flushes_new_versions_and_preserves_edited_or_unpublished_drafts(self):
        original={'steps':[{'id':'review','name':'部门审核','type':'approval','mode':'any',
            'assignee':{'source':'department_role','role':'department_head'}}]}
        edited={'steps':[]}
        with self.sessions() as db:
            db.add(WorkflowTemplate(kind='resource',draft=json.dumps(edited),revision=8))
            db.add(WorkflowVersion(kind='resource',version=1,definition=json.dumps(original)))
            db.add(WorkflowTemplate(kind='delivery',draft=json.dumps(edited),revision=3))
            # Deliberately unflushed, matching first boot; delivery has no publication.
            flow.upgrade_organization_approval_defaults(db);db.commit()
            template=db.get(WorkflowTemplate,'resource')
            self.assertEqual((json.loads(template.draft),template.revision),(edited,8))
            self.assertEqual(flow.latest(db,'resource').version,2)
            self.assertEqual(json.loads(flow.latest(db,'resource').definition)['steps'][0]['assignee']['department_scope'],'parent')
            self.assertEqual(db.get(WorkflowTemplate,'delivery').revision,3)
            self.assertIsNone(db.scalar(select(WorkflowVersion).where(WorkflowVersion.kind=='delivery')))
