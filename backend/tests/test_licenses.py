"""Exercise ownership boundaries and the supplied Java signer's integration."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.license_tool import generate_key
from app.models import AuditLog, LicenseRequest, User, UserRole, WorkflowInstance, Project, ProjectMember
from app.security import decrypt_secret, encrypt_secret, make_token

from app.rate_limit import RateDecision


class LicenseTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='yunwei-license-')
        self.engine = create_engine('sqlite:///' + str(Path(self.folder.name) / 'licenses.db'))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.audit_patch = patch('app.main.SessionLocal', self.sessions)
        self.audit_patch.start()
        self.limit_patch = patch('app.main.check_request_rate', return_value=RateDecision())
        self.limit_patch.start()
        self.notify_patch = patch('app.main.send_notification')
        self.notify_patch.start()
        def dependency():
            with self.sessions() as db: yield db
        app.dependency_overrides[get_db] = dependency
        self.client = TestClient(app)
        self.ids = {}
        with self.sessions() as db:
            for username, roles in [('admin', ['admin']), ('owner', ['applicant']), ('other', ['applicant']),
                                    ('manager', ['license_manager']), ('reviewer', ['department_head']),
                                    ('final', ['applicant']), ('replacement', ['applicant']), ('ops', ['operator'])]:
                user = User(username=username, display_name=username, department='同一部门', role=roles[0],
                            password_hash='unused', role_grants=[UserRole(role=r) for r in roles])
                db.add(user); db.flush(); self.ids[username] = user.id
            project = Project(name='授权项目100%_', department='同一部门', created_by=self.ids['admin'])
            other_project = Project(name='其他人的秘密100%_', department='其他部门', created_by=self.ids['admin'])
            db.add_all([project, other_project]); db.flush()
            self.project_id, self.other_project_id = project.id, other_project.id
            for name, pid in [('owner',project.id),('other',other_project.id),('final',project.id)]:
                db.add(ProjectMember(project_id=pid,user_id=self.ids[name],role='applicant'))
            from app.licenses import seed_license_workflow
            seed_license_workflow(db)
        self.headers = {name: {'Authorization': 'Bearer ' + make_token(uid)} for name, uid in self.ids.items()}
        self.body = dict(project_id=self.project_id, project_name='授权项目100%_', customer='客户', product='产品', tenant_id=1,
                         expire_time=int(time.time() * 1000) + 86400000, account_number=-1, agent_number=-1, purpose='项目授权')
        self.key = json.dumps({'license': {'body': 'signed-body', 'key': 'public-key'}})
        self.definition = {'steps': [
            {'id':'review','name':'部门审核','type':'approval','mode':'all','assignee':{'source':'fixed','user_ids':[self.ids['reviewer']]}},
            {'id':'final','name':'最终审批','type':'license_approval','mode':'all','assignee':{'source':'fixed','user_ids':[self.ids['final']]}},
        ]}
        self.publish(self.definition)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        self.audit_patch.stop()
        self.limit_patch.stop()
        self.notify_patch.stop()
        self.engine.dispose()
        self.folder.cleanup()

    def publish(self, definition):
        current = self.client.get('/api/admin/flows/license', headers=self.headers['admin']).json()
        saved = self.client.put('/api/admin/flows/license/draft', json={'definition':definition,'revision':current['revision']},headers=self.headers['admin'])
        self.assertEqual(saved.status_code,200,saved.text)
        result = self.client.post('/api/admin/flows/license/publish',json={'revision':saved.json()['revision']},headers=self.headers['admin'])
        self.assertEqual(result.status_code,200,result.text)

    def create(self, who='owner', **changes):
        response=self.client.post('/api/licenses',json={**self.body,**changes},headers=self.headers[who])
        self.assertEqual(response.status_code,201,response.text)
        return response.json()['id']

    def read(self, item_id, who='owner'):
        r=self.client.get(f'/api/licenses/{item_id}',headers=self.headers[who])
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def task(self, item_id):
        return next(t for t in self.read(item_id,'admin')['approvals'] if t['action']=='pending')

    def act(self,item_id,who='reviewer',action='approve',task_id=None):
        return self.client.post(f'/api/license/{item_id}/action',json={'task_id':task_id or self.task(item_id)['id'],'action':action,'comment':'审核意见'},headers=self.headers[who])

    def issue(self,item_id,who='final'):
        with patch('app.licenses.generate_key',return_value=self.key) as signer:
            response=self.act(item_id,who)
        return response,signer

    def complete(self,item_id):
        self.assertEqual(self.act(item_id).status_code,200)
        result,signer=self.issue(item_id)
        self.assertEqual(result.status_code,200,result.text)
        return signer

    def department_definition(self):
        definition=json.loads(json.dumps(self.definition))
        definition['steps'][0]['assignee']={'source':'department_role','role':'department_head'}
        return definition

    def test_admin_can_apply_without_membership_using_project_department_and_read_own_key(self):
        with self.sessions() as db:
            db.get(User,self.ids['admin']).department='平台管理'
            db.get(Project,self.other_project_id).active=False
            db.commit()
            self.assertIsNone(db.scalar(select(ProjectMember).where(ProjectMember.user_id==self.ids['admin'])))
        self.publish(self.department_definition())
        options=self.client.get('/api/projects',headers=self.headers['admin'])
        self.assertEqual(options.status_code,200,options.text)
        self.assertIn(self.project_id,[p['id'] for p in options.json() if p['active']])
        item_id=self.create('admin')
        data=self.read(item_id,'admin')
        self.assertEqual((data['applicant_id'],data['owner_department']),(self.ids['admin'],'平台管理'))
        self.assertEqual((data['approval_department'],data['approval_department_source']),('同一部门','project'))
        self.assertEqual(self.task(item_id)['assignee_id'],self.ids['reviewer'])
        self.assertEqual(self.act(item_id,'admin').status_code,403)
        # Project changes after submission must not rewrite the frozen route.
        with self.sessions() as db:
            db.get(Project,self.project_id).department='变更后的项目部门'
            db.commit()
        self.assertEqual(self.read(item_id,'admin')['approval_department'],'同一部门')
        self.complete(item_id)
        for who in ['admin','final']:
            self.assertEqual(self.read(item_id,who)['license_key'],self.key)
        for who in ['manager','reviewer']:
            self.assertIsNone(self.read(item_id,who)['license_key'])
        with self.sessions() as db:
            self.assertEqual(db.get(User,self.ids['admin']).department,'平台管理')
        disabled=self.client.post('/api/licenses',json={**self.body,'project_id':self.other_project_id},headers=self.headers['admin'])
        self.assertEqual(disabled.status_code,409,disabled.text)

    def test_admin_department_branch_preview_matches_submission_and_ignores_client_department(self):
        with self.sessions() as db:
            db.get(User,self.ids['admin']).department='平台管理'
            db.commit()
        definition=self.department_definition()
        review=definition['steps'][0]
        fallback={**review,'id':'fallback','name':'其他部门审核','assignee':{'source':'fixed','user_ids':[self.ids['replacement']]}}
        definition['steps'][0]={'id':'department','name':'按审批归属部门分支','type':'branch','branches':[
            {'id':'project-department','name':'项目部门','match':'all','conditions':[{'field':'department','op':'eq','value':'同一部门'}],'steps':[review]},
            {'id':'default','name':'其他部门','default':True,'steps':[fallback]},
        ]}
        self.publish(definition)
        preview=self.client.post('/api/admin/flows/license/preview',headers=self.headers['admin'],json={
            'definition':definition,'applicant_id':self.ids['admin'],'project_id':self.project_id,
            'data':{**self.body,'department':'客户端伪造部门'}})
        self.assertEqual(preview.status_code,200,preview.text)
        forged=self.client.post('/api/licenses',headers=self.headers['admin'],json={**self.body,'department':'客户端伪造部门'})
        self.assertEqual(forged.status_code,422,forged.text)
        data=self.read(self.create('admin'),'admin')
        snapshot=data['rounds'][0]['snapshot']
        self.assertEqual(snapshot,preview.json())
        self.assertEqual(snapshot['context']['department'],'同一部门')
        self.assertEqual(snapshot['branches'][0]['matched'],'project-department')
        self.assertEqual(snapshot['path'][0]['assignees'][0]['department'],'同一部门')

    def test_admin_legacy_submission_uses_actual_applicants_department_rule(self):
        with self.sessions() as db:
            db.get(User,self.ids['admin']).department='平台管理'
            db.get(User,self.ids['owner']).department='申请人部门'
            replacement=db.get(User,self.ids['replacement'])
            replacement.department='申请人部门'
            replacement.role_grants.append(UserRole(role='department_head'))
            data={k:v for k,v in self.body.items() if k!='project_id'}
            items={}
            for who in ['admin','owner']:
                item=LicenseRequest(**data,code='LEGACY-'+who,applicant_id=self.ids[who])
                db.add(item);db.flush();items[who]=item.id
            db.commit()
            from app.licenses import seed_license_workflow
            seed_license_workflow(db)
        self.publish(self.department_definition())
        for who,department,source,reviewer in [('admin','同一部门','project','reviewer'),('owner','申请人部门','applicant','replacement')]:
            with self.subTest(applicant=who):
                options=self.client.get(f'/api/licenses/{items[who]}/projects',headers=self.headers['admin'])
                self.assertEqual(options.status_code,200,options.text)
                self.assertIn(self.project_id,[p['id'] for p in options.json()])
                response=self.client.post(f'/api/licenses/{items[who]}/submit-review',headers=self.headers['admin'],json={'project_id':self.project_id})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['applicant_id'],self.ids[who])
                self.assertEqual((response.json()['approval_department'],response.json()['approval_department_source']),(department,source))
                self.assertEqual(self.task(items[who])['assignee_id'],self.ids[reviewer])

    def test_ordinary_applicant_keeps_personal_department_when_project_department_differs(self):
        with self.sessions() as db:
            db.get(Project,self.project_id).department='项目其他部门'
            db.commit()
        self.publish(self.department_definition())
        item_id=self.create()
        data=self.read(item_id)
        self.assertEqual((data['approval_department'],data['approval_department_source']),('同一部门','applicant'))
        self.assertEqual(self.task(item_id)['assignee_id'],self.ids['reviewer'])
        denied=self.client.post('/api/licenses',json={**self.body,'project_id':self.other_project_id},headers=self.headers['owner'])
        self.assertEqual(denied.status_code,403,denied.text)

    def test_admin_missing_qualified_project_department_reviewer_is_explicit_and_atomic(self):
        self.publish(self.department_definition())
        for scenario in ['missing_department','no_reviewer','disabled_reviewer','revoked_role','applicant_only']:
            with self.subTest(scenario=scenario):
                with self.sessions() as db:
                    admin=db.get(User,self.ids['admin']);admin.department='平台管理';admin.role_grants=[UserRole(role='admin')]
                    reviewer=db.get(User,self.ids['reviewer']);reviewer.active=True;reviewer.role_grants=[UserRole(role='department_head')]
                    project=db.get(Project,self.project_id);project.department='同一部门'
                    db.flush()
                    if scenario=='missing_department': project.department=''
                    elif scenario=='no_reviewer': project.department='未配置负责人部门'
                    elif scenario=='disabled_reviewer': reviewer.active=False
                    elif scenario=='revoked_role': reviewer.role_grants=[UserRole(role='applicant')]
                    else:
                        project.department=admin.department
                        admin.role_grants.append(UserRole(role='department_head'))
                    db.commit()
                response=self.client.post('/api/licenses',json=self.body,headers=self.headers['admin'])
                self.assertEqual(response.status_code,409,response.text)
                self.assertIn('部门审核',response.json()['detail'])
                self.assertIn('项目所属部门' if scenario=='missing_department' else f'项目「{self.body["project_name"]}」所属部门',response.json()['detail'])
                with self.sessions() as db:
                    self.assertIsNone(db.scalar(select(LicenseRequest)))
                    self.assertIsNone(db.scalar(select(WorkflowInstance)))

    def test_admin_cannot_be_own_final_approver(self):
        definition=self.department_definition()
        definition['steps'][-1]['assignee']['user_ids']=[self.ids['admin']]
        self.publish(definition)
        response=self.client.post('/api/licenses',json=self.body,headers=self.headers['admin'])
        self.assertEqual(response.status_code,409,response.text)
        self.assertIn('最终审批人不能是申请人本人',response.json()['detail'])
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(LicenseRequest)))

    def test_admin_resubmit_recomputes_department_and_keeps_old_round(self):
        self.publish(self.department_definition())
        item_id=self.create('admin')
        self.assertEqual(self.act(item_id,action='reject').status_code,200)
        with self.sessions() as db:
            db.get(Project,self.project_id).department='新项目部门'
            person=db.get(User,self.ids['replacement']);person.department='新项目部门'
            person.role_grants.append(UserRole(role='department_head'))
            db.commit()
        response=self.client.post(f'/api/licenses/{item_id}/resubmit',json=self.body,headers=self.headers['admin'])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['approval_department'],'新项目部门')
        self.assertEqual(len(response.json()['rounds']),2)
        self.assertEqual(response.json()['rounds'][0]['snapshot']['context']['department'],'同一部门')
        self.assertEqual(self.task(item_id)['assignee_id'],self.ids['replacement'])

    def test_only_owner_and_actual_final_approver_can_read_key_even_admin_is_denied(self):
        item_id=self.create()
        self.assertIsNone(self.read(item_id)['license_key'])
        with patch('app.licenses.generate_key') as signer:
            r=self.act(item_id)
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['status'],'in_progress')
            signer.assert_not_called()
        self.assertIsNone(self.read(item_id,'final')['license_key'])
        result,signer=self.issue(item_id)
        self.assertEqual(result.status_code,200,result.text)
        signer.assert_called_once_with(1,self.body['expire_time'],-1,-1)
        for who in ['owner','final']:
            data=self.read(item_id,who)
            self.assertEqual(data['license_key'],self.key)
            self.assertEqual(data['final_approver_id'],self.ids['final'])
            self.assertTrue(data['can_view_key'])
        for who in ['admin','manager','reviewer']:
            data=self.read(item_id,who)
            self.assertIsNone(data['license_key'])
            self.assertFalse(data['can_view_key'])
            self.assertNotIn('signed-body',json.dumps(data))
        self.assertEqual(self.client.get(f'/api/licenses/{item_id}',headers=self.headers['other']).status_code,404)
        for who in self.ids:
            data=self.client.get('/api/licenses',headers=self.headers[who]).json()
            self.assertNotIn('signed-body',json.dumps(data))
            self.assertTrue(all('license_key' not in row for row in data['items']))
        with self.sessions() as db:
            saved=db.get(LicenseRequest,item_id)
            self.assertNotIn('signed-body',saved.license_key_ciphertext)
            self.assertEqual(decrypt_secret(saved.license_key_ciphertext),self.key)

    def test_direct_generation_and_unauthorized_approval_cannot_bypass_sequence(self):
        item_id=self.create()
        for who in self.ids:
            self.assertEqual(self.client.post(f'/api/licenses/{item_id}/generate',json={},headers=self.headers[who]).status_code,410)
        for who in ['admin','manager','owner','final','other']:
            self.assertEqual(self.act(item_id,who).status_code,403)
        self.assertEqual(self.read(item_id)['status'],'pending')
        self.assertEqual(self.client.get('/api/licenses').status_code,401)

    def test_lists_search_and_counts_keep_applicants_isolated_but_reviewers_see_assigned_requests(self):
        item_id=self.create()
        self.create('other',project_id=self.other_project_id,project_name='其他人的秘密100%_')
        for who,expected in [('owner',1),('other',1),('manager',2),('admin',2),('reviewer',2),('ops',0),('final',0)]:
            data=self.client.get('/api/licenses',headers=self.headers[who]).json()
            self.assertEqual(data['total'],expected,who)
        self.assertEqual(self.client.get('/api/licenses?q=秘密',headers=self.headers['owner']).json()['total'],0)
        self.assertEqual(self.client.get('/api/licenses?q=100%25_',headers=self.headers['manager']).json()['total'],2)
        self.assertEqual(self.act(item_id).status_code,200)
        final_tasks=self.client.get('/api/tasks',headers=self.headers['final']).json()['items']
        self.assertEqual(final_tasks[0]['kind'],'license')
        self.assertEqual(final_tasks[0]['object_id'],item_id)

    def test_rejection_and_resubmission_keep_history_and_use_latest_version(self):
        item_id=self.create()
        self.assertEqual(self.act(item_id,action='reject').status_code,200)
        self.assertIsNone(self.read(item_id)['license_key'])
        for who in ['admin','other','manager']:
            r=self.client.post(f'/api/licenses/{item_id}/resubmit',json=self.body,headers=self.headers[who])
            self.assertEqual(r.status_code,403)
        definition=json.loads(json.dumps(self.definition));definition['steps'][-1]['assignee']['user_ids']=[self.ids['replacement']]
        self.publish(definition)
        r=self.client.post(f'/api/licenses/{item_id}/resubmit',json={**self.body,'tenant_id':8},headers=self.headers['owner'])
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(len(r.json()['rounds']),2)
        self.assertEqual(self.act(item_id).status_code,200)
        result,signer=self.issue(item_id,'replacement')
        self.assertEqual(result.status_code,200,result.text)
        signer.assert_called_once_with(8,self.body['expire_time'],-1,-1)
        self.assertEqual(result.json()['final_approver_id'],self.ids['replacement'])

    def test_inflight_approval_freezes_final_reviewer_and_transfers_change_actual_key_recipient(self):
        item_id=self.create()
        definition=json.loads(json.dumps(self.definition));definition['steps'][-1]['assignee']['user_ids']=[self.ids['replacement']]
        self.publish(definition)
        self.assertEqual(self.act(item_id).status_code,200)
        task=self.task(item_id)
        self.assertEqual(task['assignee_id'],self.ids['final'])
        r=self.client.post(f'/api/admin/tasks/{task["id"]}/reassign',json={'assignee_id':self.ids['replacement'],'reason':'代理审核'},headers=self.headers['admin'])
        self.assertEqual(r.status_code,200,r.text)
        result,_=self.issue(item_id,'replacement')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.client.get(f'/api/licenses/{item_id}',headers=self.headers['final']).status_code,404)
        self.assertEqual(self.read(item_id,'replacement')['license_key'],self.key)
        self.assertEqual(self.read(item_id)['final_approver_id'],self.ids['replacement'])

    def test_signer_failure_rolls_back_final_approval_and_allows_retry_without_repeating_prior_review(self):
        item_id=self.create();self.act(item_id)
        with patch('app.licenses.generate_key',side_effect=HTTPException(502,'工具失败')):
            r=self.act(item_id,'final')
        self.assertEqual(r.status_code,502)
        data=self.read(item_id)
        self.assertEqual(data['status'],'in_progress')
        self.assertIsNone(data['license_key'])
        self.assertEqual([a['action'] for a in data['approvals']],['approve','pending'])
        self.assertEqual(self.issue(item_id)[0].status_code,200)

    def test_duplicate_approval_cannot_generate_again_or_replace_the_key(self):
        item_id=self.create();self.act(item_id)
        task_id=self.task(item_id)['id']
        self.assertEqual(self.issue(item_id)[0].status_code,200)
        with patch('app.licenses.generate_key') as signer:
            self.assertEqual(self.act(item_id,'final',task_id=task_id).status_code,409)
            signer.assert_not_called()
        self.assertEqual(self.read(item_id)['license_key'],self.key)

    def test_countersign_and_or_sign_do_not_advance_until_node_requirements_are_met(self):
        definition=json.loads(json.dumps(self.definition))
        definition['steps'][0]['assignee']['user_ids']=[self.ids['reviewer'],self.ids['manager']]
        for mode in ['all','any']:
            definition['steps'][0]['mode']=mode;self.publish(definition)
            item_id=self.create()
            self.assertEqual(self.act(item_id).status_code,200)
            data=self.read(item_id)
            self.assertEqual(data['current_node']['node_type'],'approval' if mode=='all' else 'license_approval')
            if mode=='all': self.assertEqual(self.act(item_id,'manager').status_code,200)
            self.key=json.dumps({'license':{'body':'signed-body-'+mode,'key':'public-key'}})
            self.assertEqual(self.issue(item_id)[0].status_code,200)
            self.assertIsNone(self.read(item_id,'manager')['license_key'])

    def test_final_review_requires_fixed_members_and_preview_has_no_side_effects(self):
        for source in [{'source':'fixed','user_ids':[]},{'source':'department_role','role':'department_head'}]:
            definition=json.loads(json.dumps(self.definition));definition['steps'][-1]['assignee']=source
            r=self.client.post('/api/admin/flows/license/validate',json={'definition':definition},headers=self.headers['admin'])
            self.assertEqual(r.status_code,400)
        # Final License approval supports multiple assignees and a configurable
        # all/any/quorum threshold, like other approval nodes.
        definition=json.loads(json.dumps(self.definition))
        definition['steps'][-1]['assignee']['user_ids']=[self.ids['final'],self.ids['replacement']]
        for mode in ['all','any','quorum']:
            definition['steps'][-1].update(mode=mode, required_count=2)
            response=self.client.post('/api/admin/flows/license/validate',json={'definition':definition},headers=self.headers['admin'])
            self.assertEqual(response.status_code,200,response.text)
        r=self.client.post('/api/admin/flows/license/preview',json={'definition':self.definition,'applicant_id':self.ids['owner'],'project_id':self.project_id,'data':{}},headers=self.headers['admin'])
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['path'][-1]['assignees'][0]['id'],self.ids['final'])
        with self.sessions() as db:self.assertEqual(len(db.scalars(select(LicenseRequest)).all()),0)
        self.assertEqual(self.client.post('/api/licenses',json=self.body,headers=self.headers['final']).status_code,409)

    def test_unpublished_workflow_or_invalid_input_leaves_no_partial_application(self):
        for values in [{'tenant_id':0},{'account_number':-2},{'expire_time':1},{'purpose':'   '},{'applicant_id':self.ids['other']},{'final_approver_id':self.ids['final']},{'license_key':'fake'}]:
            self.assertIn(self.client.post('/api/licenses',json={**self.body,**values},headers=self.headers['owner']).status_code,[400,422])
        with self.sessions() as db:
            from app.models import WorkflowVersion
            for version in db.scalars(select(WorkflowVersion).where(WorkflowVersion.kind=='license')):db.delete(version)
            db.commit()
        self.assertEqual(self.client.post('/api/licenses',json=self.body,headers=self.headers['owner']).status_code,409)
        with self.sessions() as db:self.assertEqual(len(db.scalars(select(LicenseRequest)).all()),0)

    def test_role_revocation_blocks_pending_final_task_until_admin_transfers_it(self):
        item_id=self.create();self.act(item_id)
        task_id=self.task(item_id)['id']
        changed=self.client.patch(f'/api/users/{self.ids["final"]}',json={'roles':['operator']},headers=self.headers['admin'])
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(self.act(item_id,'final').status_code,403)
        self.assertIsNone(self.read(item_id)['license_key'])
        transferred=self.client.post(f'/api/admin/tasks/{task_id}/reassign',json={'assignee_id':self.ids['replacement'],'reason':'原审批人岗位调整'},headers=self.headers['admin'])
        self.assertEqual(transferred.status_code,200,transferred.text)
        self.assertEqual(self.issue(item_id,'replacement')[0].status_code,200)
        self.assertEqual(self.read(item_id)['final_approver_id'],self.ids['replacement'])

    def test_conditional_route_records_match_and_final_approval_remains_mandatory(self):
        definition=json.loads(json.dumps(self.definition))
        review=definition['steps'][0]
        definition['steps'][0]={'id':'route','name':'产品分流','type':'branch','branches':[
            {'id':'special','name':'指定产品','match':'all','conditions':[{'field':'product','op':'eq','value':'重点产品'}],'steps':[review]},
            {'id':'default','name':'其他产品','default':True,'steps':[]},
        ]}
        self.publish(definition)
        matched=self.create(product='重点产品')
        self.assertEqual(self.read(matched)['current_node']['node_type'],'approval')
        other=self.create(product='普通产品')
        data=self.read(other)
        self.assertEqual(data['current_node']['node_type'],'license_approval')
        self.assertIsNone(data['license_key'])
        self.assertEqual(data['rounds'][0]['snapshot']['branches'][0]['matched'],'default')
        self.assertEqual(self.issue(other)[0].status_code,200)

    def test_legacy_keys_are_sealed_until_review_without_copying_secrets_to_snapshots(self):
        with self.sessions() as db:
            item=LicenseRequest(**self.body,code='LEGACY',applicant_id=self.ids['owner'],status='generated',license_key_ciphertext=encrypt_secret(self.key),generated_by=self.ids['manager'])
            db.add(item);db.commit();item_id=item.id
            from app.licenses import seed_license_workflow
            seed_license_workflow(db);seed_license_workflow(db)
        for who in ['owner','manager','admin']:
            data=self.read(item_id,who)
            self.assertIsNone(data['license_key']);self.assertEqual(data['stage'],'awaiting_workflow')
        r=self.client.post(f'/api/licenses/{item_id}/submit-review',json={},headers=self.headers['owner'])
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.act(item_id).status_code,200)
        r,signer=self.issue(item_id);self.assertEqual(r.status_code,200,r.text);signer.assert_not_called()
        self.assertEqual(self.read(item_id)['license_key'],self.key)
        self.assertIsNone(self.read(item_id,'manager')['license_key'])
        with self.sessions() as db:
            snapshots=[i.snapshot for i in db.scalars(select(WorkflowInstance))]
            self.assertTrue(all('license_key_ciphertext' not in snap and 'signed-body' not in snap for snap in snapshots))

    def test_project_is_required_authorized_active_and_named_by_the_server(self):
        missing={k:v for k,v in self.body.items() if k!='project_id'}
        self.assertEqual(self.client.post('/api/licenses',json=missing,headers=self.headers['owner']).status_code,422)
        for who,project_id,status in [('owner',self.other_project_id,403),('manager',self.project_id,403),('owner',99999,404)]:
            result=self.client.post('/api/licenses',json={**self.body,'project_id':project_id},headers=self.headers[who])
            self.assertEqual(result.status_code,status,result.text)
        with self.sessions() as db:
            db.get(Project,self.project_id).active=False;db.commit()
        self.assertEqual(self.client.post('/api/licenses',json=self.body,headers=self.headers['owner']).status_code,409)
        with self.sessions() as db:
            self.assertEqual(len(db.scalars(select(LicenseRequest)).all()),0)
            db.get(Project,self.project_id).active=True;db.commit()
        item_id=self.create(project_name='伪造项目名称')
        data=self.read(item_id)
        self.assertEqual((data['project_id'],data['project_name']),(self.project_id,'授权项目100%_'))
        self.assertEqual(data['rounds'][0]['snapshot']['project'],{'id':self.project_id,'name':'授权项目100%_'})
        with self.sessions() as db:
            owner=db.get(User,self.ids['owner']);owner.role_grants=[UserRole(role='operator')];db.commit()
        self.assertEqual(self.client.get('/api/projects',headers=self.headers['owner']).json(),[])
        self.assertEqual(self.client.post('/api/licenses',json=self.body,headers=self.headers['owner']).status_code,403)

    def test_project_role_approval_uses_each_project_and_preserves_key_restrictions(self):
        with self.sessions() as db:
            for name,pid in [('reviewer',self.project_id),('replacement',self.other_project_id)]:
                person=db.get(User,self.ids[name]);person.role_grants.append(UserRole(role='project_manager'))
                db.add(ProjectMember(project_id=pid,user_id=person.id,role='project_manager'))
            db.commit()
        definition=json.loads(json.dumps(self.definition))
        definition['steps'][0]['assignee']={'source':'project_role','role':'project_manager'}
        self.publish(definition)
        first=self.create()
        second=self.create('other',project_id=self.other_project_id)
        self.assertEqual(self.task(first)['assignee_id'],self.ids['reviewer'])
        self.assertEqual(self.task(second)['assignee_id'],self.ids['replacement'])
        # A project change cannot rewrite an in-flight route or key readership.
        with self.sessions() as db:
            db.get(Project,self.project_id).active=False
            db.get(Project,self.project_id).name='项目改名'
            db.commit()
        self.complete(first)
        self.assertEqual(self.read(first)['project_name'],'授权项目100%_')
        self.assertIsNone(self.read(first,'reviewer')['license_key'])
        self.assertEqual(self.read(first,'final')['license_key'],self.key)

    def test_resubmit_revalidates_the_project_and_refreshes_its_name(self):
        item_id=self.create();self.assertEqual(self.act(item_id,action='reject').status_code,200)
        result=self.client.post(f'/api/licenses/{item_id}/resubmit',json={**self.body,'project_id':self.other_project_id},headers=self.headers['owner'])
        self.assertEqual(result.status_code,403)
        self.assertEqual(len(self.read(item_id)['rounds']),1)
        with self.sessions() as db:
            db.get(Project,self.project_id).active=False;db.commit()
        self.assertEqual(self.client.post(f'/api/licenses/{item_id}/resubmit',json=self.body,headers=self.headers['owner']).status_code,409)
        with self.sessions() as db:
            project=db.get(Project,self.project_id);project.active=True;project.name='更新后的项目名';db.commit()
        result=self.client.post(f'/api/licenses/{item_id}/resubmit',json=self.body,headers=self.headers['owner'])
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['project_name'],'更新后的项目名')

    def test_legacy_review_selects_from_applicants_projects_even_when_manager_submits(self):
        with self.sessions() as db:
            data={k:v for k,v in self.body.items() if k!='project_id'}
            item=LicenseRequest(**data,code='LEGACY-PROJECT',applicant_id=self.ids['owner'])
            db.add(item);db.commit();item_id=item.id
            from app.licenses import seed_license_workflow
            seed_license_workflow(db)
        options=self.client.get(f'/api/licenses/{item_id}/projects',headers=self.headers['manager'])
        self.assertEqual(options.status_code,200)
        self.assertEqual([p['id'] for p in options.json()],[self.project_id])
        self.assertEqual(self.client.get(f'/api/licenses/{item_id}/projects',headers=self.headers['other']).status_code,404)
        missing=self.client.post(f'/api/licenses/{item_id}/submit-review',json={},headers=self.headers['manager'])
        self.assertEqual(missing.status_code,400)
        denied=self.client.post(f'/api/licenses/{item_id}/submit-review',json={'project_id':self.other_project_id},headers=self.headers['manager'])
        self.assertEqual(denied.status_code,403)
        self.assertEqual(self.read(item_id)['rounds'],[])
        result=self.client.post(f'/api/licenses/{item_id}/submit-review',json={'project_id':self.project_id},headers=self.headers['manager'])
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['project_id'],self.project_id)
        self.assertIsNone(result.json()['license_key'])

    def test_old_inflight_license_can_finish_without_rewriting_frozen_project_or_approval(self):
        item_id=self.create()
        with self.sessions() as db:
            db.get(LicenseRequest,item_id).project_id=None
            from app.licenses import seed_license_workflow
            seed_license_workflow(db)
        self.complete(item_id)
        data=self.read(item_id)
        self.assertIsNone(data['project_id'])
        self.assertEqual(data['license_key'],self.key)
        with self.sessions() as db:
            from app.licenses import seed_license_workflow
            seed_license_workflow(db)
        self.assertEqual(self.read(item_id)['status'],'generated')

    def test_new_and_reset_passwords_enforce_length_and_three_ascii_categories(self):
        weak=['Aa1!short','abcdefghijkl','ABCDEFGHIJKL','123456789012','Abcdefghijkl','中文中文中文中文1234','abcdefghijkl ']
        for password in weak:
            r=self.client.post('/api/users',json={'username':'newuser','display_name':'新成员','roles':['applicant','license_manager'],'password':password},headers=self.headers['admin'])
            self.assertEqual(r.status_code,422,password)
            r=self.client.patch(f'/api/users/{self.ids["other"]}',json={'password':password},headers=self.headers['admin'])
            self.assertEqual(r.status_code,422,password)
        for index,password in enumerate(['Abcdefghi123','Abcdefghij!@','abcdefghi12!','ABCDEFGHI12!']):
            r=self.client.post('/api/users',json={'username':f'newuser{index}','display_name':'新成员','roles':['applicant','license_manager'],'password':password},headers=self.headers['admin'])
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(set(r.json()['roles']),{'applicant','license_manager'})
            self.assertEqual(self.client.post('/api/login',json={'username':f'newuser{index}','password':password}).status_code,200)
        r=self.client.patch(f'/api/users/{self.ids["other"]}',json={'password':'NewSecure123!'},headers=self.headers['admin'])
        self.assertEqual(r.status_code,200,r.text)

    @unittest.skipUnless(os.getenv('LICENSE_REAL_JAVA_CHECK')=='1','Enable explicitly with supplied JAR')
    def test_actual_java_final_approval_persists_key_for_only_owner_and_final_reviewer(self):
        item_id=self.create();self.act(item_id)
        result=self.act(item_id,'final');self.assertEqual(result.status_code,200,result.text)
        key=result.json()['license_key'];self.assertTrue(json.loads(key)['license']['body'])
        self.assertEqual(self.read(item_id)['license_key'],key)
        self.assertIsNone(self.read(item_id,'admin')['license_key'])


class JavaToolTests(unittest.TestCase):
    def test_command_passes_validated_values_without_shell_and_keeps_entire_json(self):
        payload = {'license': {'body': 'signed', 'key': 'key'}}
        with patch('app.license_tool.subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), '')) as run:
            value = generate_key(9, 1893427199999, 100, -1)
        self.assertEqual(json.loads(value), payload)
        self.assertEqual(run.call_args.args[0][-8:], ['-tenantId', '9', '-expireTime', '1893427199999', '-accountNumber', '100', '-agentNumber', '-1'])
        self.assertFalse(run.call_args.kwargs.get('shell', False))
        self.assertEqual(run.call_args.kwargs['timeout'], 30)

    def test_signer_rejects_errors_and_malformed_output_without_returning_tool_output(self):
        for result in [subprocess.CompletedProcess([], 1, 'sensitive', 'sensitive'),
                       subprocess.CompletedProcess([], 0, 'usage: sensitive', ''),
                       subprocess.CompletedProcess([], 0, '{"license":{"key":"sensitive"}}', '')]:
            with patch('app.license_tool.subprocess.run', return_value=result):
                with self.assertRaises(HTTPException) as error: generate_key(1, 1893427199999)
                self.assertEqual(error.exception.status_code, 502)
                self.assertNotIn('sensitive', error.exception.detail)
        for raised, status in [(FileNotFoundError(), 503), (subprocess.TimeoutExpired('java', 30), 504)]:
            with patch('app.license_tool.subprocess.run', side_effect=raised):
                with self.assertRaises(HTTPException) as error: generate_key(1, 1893427199999)
                self.assertEqual(error.exception.status_code, status)

    @unittest.skipUnless(os.getenv('LICENSE_REAL_JAVA_CHECK') == '1', 'Enable explicitly when Java and the supplied JAR are available')
    def test_supplied_jar_generates_a_real_license(self):
        result = json.loads(generate_key(1, 1893427199999, -1, -1))
        self.assertGreater(len(result['license']['body']), 100)
        self.assertGreater(len(result['license']['key']), 100)


if __name__ == '__main__':
    unittest.main(verbosity=2)
