"""Space creation, ownership, document association and upgrade regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, MetaData, Table
from sqlalchemy.orm import Session, sessionmaker

from app import main
from app.database import Base, get_db
from app.migrations import upgrade, seed_knowledge
from app.models import AuditLog, User, UserRole, WikiArticle, WikiArticleVersion, WikiSpace, WikiSpaceMember, WikiSpaceDepartment, Department, DepartmentMembership
from app.rate_limit import RateDecision
from app.security import make_token


class WikiSpaceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='wiki-space-test-')
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
            for username, role in [('admin', 'admin'), ('curator', 'knowledge_admin'),
                                   ('owner', 'applicant'), ('other', 'applicant'), ('disabled', 'applicant')]:
                user = User(username=username, display_name=username + '姓名', department='研发部', role=role,
                            active=username != 'disabled', password_hash='unused', role_grants=[UserRole(role=role)])
                db.add(user); db.flush(); self.ids[username] = user.id
            db.commit()
        self.headers = {name: {'Authorization': 'Bearer ' + make_token(uid)} for name, uid in self.ids.items()}

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.pop(get_db, None)
        for item in reversed(self.patches): item.stop()
        self.engine.dispose(); self.folder.cleanup()

    def create(self, who='admin', **fields):
        response = self.client.post('/api/wiki/spaces', headers=self.headers[who],
            json={'key': ' sre ', 'name': ' 团队知识库 ', 'description': ' 运维经验 ', **fields})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def logs(self):
        with self.sessions() as db:
            return db.scalars(select(AuditLog).where(AuditLog.record_kind == 'business',
                             AuditLog.target_type == 'wiki_space').order_by(AuditLog.id)).all()

    def request(self, method, path, who='owner', **kwargs):
        return getattr(self.client, method)('/api' + path, headers=self.headers[who], **kwargs)

    def add(self, space, who='other', role='viewer', owner='owner'):
        response = self.request('post', f'/wiki/spaces/{space["id"]}/members', owner,
                                json={'user_id': self.ids[who], 'role': role})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def article(self, who='owner', space=None, **fields):
        payload = {'title': '私密文档-' + who, 'category': '规范', 'content': 'PRIVATE_BODY_' + who,
                   **({'space_id': space['id']} if space else {}), **fields}
        response = self.request('post', '/wiki', who, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def setup_departments(self):
        with self.sessions() as db:
            root = Department(name='产品中心'); db.add(root); db.flush()
            child = Department(name='产品研发', parent_id=root.id)
            outside = Department(name='商务中心'); db.add_all([child, outside]); db.flush()
            db.get(User, self.ids['other']).department_id = child.id
            db.get(User, self.ids['curator']).department_id = outside.id
            db.commit()
            return root.id, child.id, outside.id

    def assert_read(self, space, article, who, allowed):
        expected = 200 if allowed else 404
        for path in [f'/wiki/{article["id"]}', f'/wiki/{article["id"]}/versions', f'/wiki?space_id={space["id"]}']:
            result = self.request('get', path, who)
            self.assertEqual(result.status_code, expected, (path, result.text))
        listed = self.request('get', '/wiki/spaces', who).json()
        self.assertEqual(space['id'] in [row['id'] for row in listed], allowed)
        found = self.request('get', '/wiki?scope=shared&q=PRIVATE_BODY_', who).json()
        self.assertEqual(article['id'] in [row['id'] for row in found], allowed)

    def test_all_scope_is_authenticated_readonly_and_shrinking_revokes_every_read(self):
        space = self.create('owner', access_scope='all')
        item = self.article(space=space)
        for who in ['other', 'admin', 'curator']:
            self.assert_read(space, item, who, True)
            self.assertEqual(self.request('post', '/wiki', who, json={**item, 'title':'不能写'}).status_code, 403)
            self.assertEqual(self.request('put', f'/wiki/{item["id"]}', who, json=item).status_code, 403)
            self.assertEqual(self.request('put', f'/wiki/spaces/{space["id"]}', who, json=space).status_code, 403)
        self.assertEqual(self.client.get(f'/api/wiki/{item["id"]}').status_code, 401)
        self.assertEqual(self.request('get', f'/wiki/{item["id"]}', 'disabled').status_code, 401)
        with self.sessions() as db:
            from app.knowledge import accessible_spaces
            self.assertEqual(db.scalars(select(WikiSpace.id).where(accessible_spaces(self.ids['disabled']))).all(), [])
        updated = self.request('put', f'/wiki/spaces/{space["id"]}', json={**space, 'access_scope':'members'})
        self.assertEqual(updated.status_code, 200, updated.text)
        for who in ['other', 'admin', 'curator']: self.assert_read(space, item, who, False)

    def test_department_scope_hierarchy_multimembership_and_live_revocation(self):
        root, child, outside = self.setup_departments()
        space = self.create('owner', access_scope='departments', department_ids=[root])
        item = self.article(space=space)
        self.assert_read(space, item, 'other', True)
        self.assert_read(space, item, 'curator', False)
        space = self.request('put', f'/wiki/spaces/{space["id"]}', json={**space, 'include_subdepartments':False}).json()
        self.assert_read(space, item, 'other', False)
        with self.sessions() as db:
            db.add(DepartmentMembership(user_id=self.ids['curator'], department_id=root)); db.commit()
        self.assert_read(space, item, 'curator', True)
        with self.sessions() as db:
            db.delete(db.get(DepartmentMembership, (self.ids['curator'], root))); db.commit()
        self.assert_read(space, item, 'curator', False)
        self.request('put', f'/wiki/spaces/{space["id"]}', json={**space, 'include_subdepartments':True})
        with self.sessions() as db:
            db.get(User, self.ids['other']).department_id = outside; db.commit()
        self.assert_read(space, item, 'other', False)
        with self.sessions() as db:
            db.get(User, self.ids['other']).department_id = child
            db.get(Department, root).active = False; db.commit()
        self.assert_read(space, item, 'other', False)

    def test_selected_people_are_atomic_and_editor_grants_survive_range_changes(self):
        space = self.create('owner', member_ids=[self.ids['other'], self.ids['other']])
        item = self.article(space=space)
        self.assert_read(space, item, 'other', True)
        self.assert_read(space, item, 'admin', False)
        member_path=f'/wiki/spaces/{space["id"]}/members/{self.ids["other"]}'
        self.request('patch', member_path, json={'role':'editor'})
        for scope in ['all', 'members']:
            response = self.request('put', f'/wiki/spaces/{space["id"]}', json={**space, 'access_scope':scope, 'member_ids':[self.ids['other']]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.request('get', f'/wiki/spaces/{space["id"]}/members').json()[0]['role'], 'editor')
        self.request('put', f'/wiki/spaces/{space["id"]}', json={**space,'access_scope':'all'})
        removed = self.request('delete', member_path)
        self.assertTrue(removed.json()['still_has_access'])
        self.assert_read(space, item, 'other', True)
        self.assertEqual(self.request('post','/wiki','other',json={**item,'title':'只读'}).status_code,403)
        self.request('put',f'/wiki/spaces/{space["id"]}',json={**space,'access_scope':'members','member_ids':[]})
        self.assert_read(space, item, 'other', False)

    def test_invalid_access_update_rolls_back_and_personal_space_cannot_open(self):
        root, child, outside = self.setup_departments()
        space = self.create('owner', member_ids=[self.ids['other']])
        count = len(self.logs())
        for patch_data in [{'access_scope':'departments','department_ids':[]},
                           {'access_scope':'departments','department_ids':[root,999999]},
                           {'access_scope':'all','member_ids':[self.ids['disabled']]},
                           {'access_scope':'all','member_ids':[999999]}, {'department_ids':[-1]}]:
            result = self.request('put',f'/wiki/spaces/{space["id"]}',json={**space,'name':'不应保存',**patch_data})
            self.assertIn(result.status_code,[400,422],result.text)
        with self.sessions() as db:
            row=db.get(WikiSpace,space['id'])
            self.assertEqual((row.name,row.access_scope),(space['name'],'members'))
            self.assertIsNotNone(db.get(WikiSpaceMember,(space['id'],self.ids['other'])))
            self.assertEqual(db.scalars(select(WikiSpaceDepartment)).all(),[])
        self.assertEqual(len(self.logs()),count)
        personal = next(row for row in self.request('get','/wiki/spaces').json() if row['kind']=='personal')
        for values in [{'access_scope':'all'},{'department_ids':[root]},{'member_ids':[self.ids['other']]}]:
            self.assertEqual(self.request('put',f'/wiki/spaces/{personal["id"]}',json={**personal,**values}).status_code,400)

    def test_department_reference_blocks_deletion_until_owner_removes_it(self):
        department=self.request('post','/departments','admin',json={'name':'知识库授权部门'}).json()
        space=self.create('owner',access_scope='departments',department_ids=[department['id']])
        impact=self.request('get',f'/departments/{department["id"]}/deletion-impact','admin').json()
        self.assertEqual(impact['blockers'],[{'label':'知识库空间授权引用','count':1}])
        self.assertEqual(self.request('delete',f'/departments/{department["id"]}?revision={department["revision"]}','admin').status_code,409)
        changed=self.request('put',f'/wiki/spaces/{space["id"]}',json={**space,'access_scope':'members','department_ids':[]})
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(self.request('delete',f'/departments/{department["id"]}?revision={department["revision"]}','admin').status_code,200)

    def test_old_clients_preserve_scope_and_department_cycles_do_not_break_reads(self):
        root,child,outside=self.setup_departments()
        space=self.create('owner',access_scope='departments',department_ids=[root])
        item=self.article(space=space)
        legacy={k:space[k] for k in ['key','name','description','category','kind','owner_id']}
        updated=self.request('put',f'/wiki/spaces/{space["id"]}',json={**legacy,'name':'兼容旧客户端'})
        self.assertEqual(updated.status_code,200,updated.text)
        self.assertEqual(updated.json()['department_ids'],[root])
        self.assertEqual(updated.json()['access_scope'],'departments')
        with self.sessions() as db:
            db.get(Department,root).parent_id=child;db.commit()
        self.assert_read(space,item,'other',True)

    def test_access_choices_exclude_disabled_and_no_private_account_fields(self):
        root, child, outside = self.setup_departments()
        result = self.request('get','/wiki/access-options?q=other').json()
        self.assertEqual([p['id'] for p in result['people']],[self.ids['other']])
        self.assertEqual(set(result['people'][0]),{'id','display_name','username','active','department'})
        self.assertEqual(next(d['path'] for d in result['departments'] if d['id']==child),'产品中心 / 产品研发')
        self.assertEqual(self.client.get('/api/wiki/access-options').status_code,401)

    def test_scope_changes_are_audited_and_audit_visibility_follows_department_scope(self):
        root, child, outside = self.setup_departments()
        space = self.create('owner', access_scope='departments', department_ids=[root])
        result=self.request('get','/audit?target_type=wiki_space','admin').json()
        self.assertNotIn(str(space['id']),[str(row['target_id']) for row in result['items']])
        self.request('put',f'/wiki/spaces/{space["id"]}',json={**space,'access_scope':'all','include_subdepartments':False})
        logs=self.logs()
        changes=json.loads(logs[-1].changes)
        self.assertIn('开放范围',str(changes))
        result=self.request('get','/audit?target_type=wiki_space','admin').json()
        self.assertIn(str(space['id']),[str(row['target_id']) for row in result['items']])
        self.client.request('DELETE',f'/api/wiki/spaces/{space["id"]}',headers=self.headers['owner'],json={'confirm_name':space['name']})
        result=self.request('get','/audit?target_type=wiki_space','admin').json()
        self.assertNotIn(str(space['id']),[str(row['target_id']) for row in result['items']])
        restored=self.request('post',f'/wiki/spaces/{space["id"]}/restore')
        self.assertEqual(restored.json()['access_scope'],'all')

    def test_personal_library_is_automatic_unique_and_hidden_even_from_administrators(self):
        article = self.article()
        for who in ['admin', 'curator', 'other']:
            spaces = self.request('get', '/wiki/spaces', who).json()
            self.assertTrue(all(space['owner_id'] == self.ids[who] for space in spaces))
            self.assertEqual(len([space for space in spaces if space['kind'] == 'personal']), 1)
            for path in [f'/wiki/{article["id"]}', f'/wiki/{article["id"]}/versions', f'/wiki?space_id={article["space_id"]}']:
                response = self.request('get', path, who)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json()['detail'], '内容不存在或无权访问')
            self.assertEqual(self.request('get', '/wiki?q=私密文档', who).json(), [])
        spaces1 = self.request('get', '/wiki/spaces').json()
        spaces2 = self.request('get', '/wiki/spaces').json()
        self.assertEqual(spaces1, spaces2)
        self.assertEqual([item['id'] for item in self.request('get', '/wiki').json()], [article['id']])
        self.assertEqual(len(self.request('get', f'/wiki/{article["id"]}/versions').json()), 1)
        personal = spaces1[0]
        self.assertEqual(self.request('post', f'/wiki/spaces/{personal["id"]}/members', json={'user_id': self.ids['other']}).status_code, 400)
        self.assertEqual(self.request('put', f'/wiki/spaces/{personal["id"]}', json={**personal, 'owner_id': self.ids['other']}).status_code, 400)
        self.assertEqual(self.request('put', f'/wiki/spaces/{personal["id"]}', json={**personal, 'name': '我的笔记'}).status_code, 200)

    def test_any_role_can_create_a_shared_space_and_creator_is_owner(self):
        for who in ['owner', 'admin', 'curator']:
            space = self.create(who, key=who)
            self.assertEqual(space['kind'], 'shared')
            self.assertEqual(space['owner_id'], self.ids[who])
            self.assertEqual(space['owner_name'], who + '姓名')
            self.assertTrue(space['can_manage']); self.assertTrue(space['can_write'])
            self.assertEqual(space['article_count'], 0)
        self.assertEqual(len(self.logs()), 3)
        self.assertIn('创建了知识空间', self.logs()[0].summary)

    def test_invalid_creation_and_duplicate_do_not_claim_success(self):
        self.create()
        cases = [({'key': 'SrE', 'name': '重复'}, 409), ({'key': 'BAD KEY', 'name': '无效'}, 422),
                 ({'key': 'NEW', 'name': '   '}, 422),
                 ({'key': 'NEW', 'name': '指定他人', 'owner_id': self.ids['owner']}, 400),
                 ({'key': 'PERSONAL_RESERVED', 'name': '保留'}, 400)]
        for payload, status in cases:
            response = self.request('post', '/wiki/spaces', 'admin', json=payload)
            self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(len(self.logs()), 1)

    def test_viewer_editor_owner_permissions_and_immediate_revocation(self):
        space = self.create('owner')
        item = self.article(space=space)
        self.assertEqual(self.request('get', f'/wiki/{item["id"]}', 'admin').status_code, 404)
        self.add(space)
        spaces = self.request('get', '/wiki/spaces', 'other').json()
        shared = next(row for row in spaces if row['id'] == space['id'])
        self.assertFalse(shared['can_write']); self.assertFalse(shared['can_manage'])
        self.assertEqual(shared['article_count'], 1)
        self.assertEqual(self.request('get', f'/wiki/{item["id"]}/versions', 'other').status_code, 200)
        self.assertEqual(self.request('post', '/wiki', 'other', json={**item, 'title': '不能创建'}).status_code, 403)
        self.assertEqual(self.request('put', f'/wiki/{item["id"]}', 'other', json=item).status_code, 403)
        member_path = f'/wiki/spaces/{space["id"]}/members/{self.ids["other"]}'
        self.assertEqual(self.request('patch', member_path, json={'role': 'editor'}).status_code, 200)
        own = self.article('other', space)
        response = self.request('put', f'/wiki/{own["id"]}', 'other', json={**own, 'content': '修改'})
        self.assertEqual(response.status_code, 200, response.text)
        own = response.json()
        self.assertEqual(self.request('put', f'/wiki/{item["id"]}', 'other', json=item).status_code, 403)
        self.assertEqual(self.request('put', f'/wiki/{own["id"]}', json={**own, 'content': '负责人修改'}).status_code, 200)
        self.request('patch', member_path, json={'role': 'viewer'})
        self.assertEqual(self.request('put', f'/wiki/{own["id"]}', 'other', json=own).status_code, 403)
        self.assertEqual(self.request('delete', member_path).status_code, 200)
        for path in [f'/wiki/{own["id"]}', f'/wiki/{own["id"]}/versions', f'/wiki?space_id={space["id"]}']:
            self.assertEqual(self.request('get', path, 'other').status_code, 404)
        self.assertEqual(self.request('get', '/wiki?scope=shared', 'other').json(), [])
        self.assertNotIn(space['id'], [row['id'] for row in self.request('get', '/wiki/spaces', 'other').json()])

    def test_owner_only_member_management_default_viewer_and_transfer(self):
        space = self.create('owner'); path = f'/wiki/spaces/{space["id"]}'
        member = self.request('post', path + '/members', json={'user_id': self.ids['other']})
        self.assertEqual(member.status_code, 200, member.text); self.assertEqual(member.json()['role'], 'viewer')
        self.assertEqual(self.request('post', path + '/members', json={'user_id': self.ids['other']}).status_code, 409)
        self.assertEqual(self.request('post', path + '/members', json={'user_id': self.ids['disabled']}).status_code, 400)
        for who in ['other', 'admin', 'curator']:
            expected = 403 if who == 'other' else 404
            self.assertEqual(self.request('get', path + '/members', who).status_code, expected)
            self.assertEqual(self.request('get', path + '/candidates', who).status_code, expected)
            self.assertEqual(self.request('post', path + '/members', who, json={'user_id': self.ids['admin']}).status_code, expected)
        candidate_rows = self.request('get', path + '/candidates?q=admin').json()
        self.assertEqual([row['id'] for row in candidate_rows], [self.ids['admin']])
        self.assertTrue(all(set(row) == {'id','display_name','username','department'} for row in candidate_rows))
        self.assertEqual(self.request('put', path, json={**space, 'owner_id': self.ids['admin']}).status_code, 400)
        response = self.request('put', path, json={**space, 'owner_id': self.ids['other']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['can_manage']); self.assertTrue(response.json()['can_write'])
        self.assertEqual(self.request('get', path + '/members').status_code, 403)
        self.assertEqual(self.request('get', path + '/members', 'other').json()[0]['role'], 'editor')
        self.assertEqual(self.request('put', path, 'other', json={**space, 'owner_id': self.ids['other'], 'key': 'RENAME'}).status_code, 400)
        self.assertTrue(any(row.action == 'members' for row in self.logs()))

    def test_document_scope_and_parent_cannot_cross_libraries_and_all_states_are_scoped(self):
        personal = self.article()
        space = self.create('owner')
        self.add(space, role='editor')
        item = self.article(space=space, status='draft')
        self.assertEqual(self.request('put', f'/wiki/{item["id"]}', json={**item,'space_id':personal['space_id']}).status_code, 400)
        self.assertEqual(self.request('post', '/wiki', json={**item,'parent_id':personal['id']}).status_code, 400)
        self.assertEqual(self.request('put', f'/wiki/{item["id"]}', json={**item,'parent_id':item['id']}).status_code, 400)
        self.assertEqual([row['id'] for row in self.request('get', '/wiki').json()], [personal['id']])
        self.assertEqual([row['id'] for row in self.request('get', '/wiki?scope=shared&status=draft','other').json()], [item['id']])
        result = self.request('put', f'/wiki/{item["id"]}', json={**item,'status':'archived'})
        self.assertEqual(result.status_code, 200)
        rows = self.request('get', f'/wiki?space_id={space["id"]}&status=archived', 'other').json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.request('get', '/wiki?scope=shared&q=PRIVATE_BODY', 'admin').json(), [])

    def test_autosave_updates_revision_and_manual_click_creates_one_version(self):
        body = '    code_indented()\n\n正文  \n'
        item = self.article(content=body)
        self.assertEqual(item['content'], body)
        payload = {**item, 'title': ' 自动保存标题 ', 'content': body + '\n后续内容\n', 'save_mode': 'auto'}
        saved = self.request('put', f'/wiki/{item["id"]}', json=payload)
        self.assertEqual(saved.status_code, 200, saved.text)
        current = saved.json()
        self.assertEqual(current['version'], item['version'])
        self.assertEqual(current['revision'], item['revision'] + 1)
        self.assertEqual(current['title'], '自动保存标题')
        self.assertEqual(current['content'], payload['content'])
        retry = self.request('put', f'/wiki/{item["id"]}', json=payload)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()['version'], current['version'])
        self.assertEqual(retry.json()['revision'], current['revision'])
        self.assertEqual(len(self.request('get', f'/wiki/{item["id"]}/versions').json()), 1)
        # A second window cannot overwrite an autosave of the same visible version.
        stale = self.request('put', f'/wiki/{item["id"]}', json={**item, 'content': '旧窗口覆盖'})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(self.request('get', f'/wiki/{item["id"]}').json()['content'], current['content'])
        for suffix in ['第二段', '第三段', '第四段']:
            result = self.request('put', f'/wiki/{item["id"]}', json={**current, 'content': suffix, 'save_mode': 'auto'})
            self.assertEqual(result.status_code, 200, result.text)
            current = result.json()
            self.assertEqual(current['version'], item['version'])
        manual_payload = {**current, 'save_mode': 'manual'}
        saved = self.request('put', f'/wiki/{item["id"]}', json=manual_payload)
        self.assertEqual(saved.status_code, 200, saved.text)
        current = saved.json()
        self.assertEqual(current['version'], item['version'] + 1)
        # An ambiguous response/retry must not count a second time.
        repeated = self.request('put', f'/wiki/{item["id"]}', json=manual_payload)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()['version'], current['version'])
        self.assertEqual(repeated.json()['revision'], current['revision'])
        versions = self.request('get', f'/wiki/{item["id"]}/versions').json()
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]['content'], current['content'])
        self.assertEqual(versions[1]['content'], body)
        # A fresh click is one new snapshot even without changing the content.
        next_save = self.request('put', f'/wiki/{item["id"]}', json=current)
        self.assertEqual(next_save.status_code, 200, next_save.text)
        self.assertEqual(next_save.json()['version'], current['version'] + 1)
        self.assertEqual(self.request('put', f'/wiki/{item["id"]}', 'admin', json=current).status_code, 404)
        for field in ['title', 'category', 'content']:
            response = self.request('put', f'/wiki/{item["id"]}', json={**current, field: '  \n '})
            self.assertEqual(response.status_code, 422, response.text)

    def test_save_requires_revision_and_rejects_stale_manual_after_later_edits(self):
        item = self.article()
        missing = {key: value for key, value in item.items() if key != 'revision'}
        response = self.request('put', f'/wiki/{item["id"]}', json={**missing, 'content': '旧客户端'})
        self.assertEqual(response.status_code, 409, response.text)
        first = self.request('put', f'/wiki/{item["id"]}', json=item).json()
        current = self.request('put', f'/wiki/{item["id"]}', json={**first, 'content': '其他窗口修改', 'save_mode': 'auto'}).json()
        response = self.request('put', f'/wiki/{item["id"]}', json=item)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.request('get', f'/wiki/{item["id"]}').json()['content'], current['content'])
        self.assertEqual(len(self.request('get', f'/wiki/{item["id"]}/versions').json()), 2)

    def test_autosave_cannot_bypass_revoked_edit_permission(self):
        space = self.create('owner')
        self.add(space, role='editor')
        item = self.article('other', space)
        self.request('patch', f'/wiki/spaces/{space["id"]}/members/{self.ids["other"]}', json={'role': 'viewer'})
        response = self.request('put', f'/wiki/{item["id"]}', 'other', json={**item, 'content': '权限撤销后的自动保存'})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.request('get', f'/wiki/{item["id"]}').json()['version'], item['version'])

    def test_document_field_limits_apply_to_creation_and_edits_without_truncating_history(self):
        boundary = {'title': '项' * 180, 'category': '类' * 60, 'content': '正文保留'}
        result = self.request('post', '/wiki', json=boundary)
        self.assertEqual(result.status_code, 200, result.text)
        item = result.json()
        self.assertEqual(item['title'], boundary['title'])
        self.assertEqual(item['category'], boundary['category'])
        for field, value in [('title', 'a' * 181), ('title', '项' * 181), ('category', 'a' * 61)]:
            payload = {**item, field: value}
            for method, path in [('post', '/wiki'), ('put', f'/wiki/{item["id"]}')]:
                response = self.request(method, path, json=payload)
                self.assertEqual(response.status_code, 422, response.text)
        stored = self.request('get', f'/wiki/{item["id"]}').json()
        self.assertEqual(stored['title'], boundary['title'])
        self.assertEqual(stored['version'], 1)
        self.assertEqual(len(self.request('get', f'/wiki/{item["id"]}/versions').json()), 1)

    def test_audit_filters_old_and_new_private_records_before_search_counts_or_details(self):
        item = self.article()
        with self.sessions() as db:
            entry = db.scalar(select(AuditLog).where(AuditLog.record_kind=='business',AuditLog.target_type=='wiki'))
            self.assertNotIn('PRIVATE_BODY_', entry.changes)
            entry.changes = json.dumps([{'field':'content','before':None,'after':'LEGACY_PRIVATE_BODY'}])
            entry.summary = '原始私密文档标题'
            audit_id = entry.id; db.commit()
        response = self.request('get', '/audit?q=原始私密文档标题', 'admin')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total'], 0)
        self.assertEqual(self.request('get', f'/audit/{audit_id}', 'admin').status_code, 404)
        shared = self.create('owner'); self.add(shared, who='admin')
        public_item = self.article(space=shared)
        rows = self.request('get', '/audit?target_type=wiki', 'admin').json()['items']
        self.assertEqual([row['target_id'] for row in rows], [str(public_item['id'])])
        self.request('delete', f'/wiki/spaces/{shared["id"]}/members/{self.ids["admin"]}')
        self.assertEqual(self.request('get', '/audit?target_type=wiki', 'admin').json()['total'], 0)

    def test_new_users_get_personal_space_in_same_transaction(self):
        department=self.request('post','/departments','admin',json={'name':'账号部门'}).json()
        response = self.request('post','/users','admin',json={'username':'newuser','display_name':'新成员','department_id':department['id'],'password':'StrongPass123!','roles':['applicant']})
        self.assertEqual(response.status_code,200,response.text)
        with self.sessions() as db:
            space=db.scalar(select(WikiSpace).where(WikiSpace.personal_user_id==response.json()['id']))
            self.assertIsNotNone(space); self.assertEqual(space.kind,'personal')


class WikiMigrationTests(unittest.TestCase):
    def test_old_article_and_space_schema_upgrade_is_idempotent_and_preserves_history(self):
        engine = create_engine('sqlite:///:memory:')
        metadata = MetaData()
        for name in ['users', 'wiki_articles', 'wiki_spaces']:
            table = Table(name, metadata)
            omitted = {'wiki_articles': {'space_id', 'parent_id', 'status', 'version', 'revision'}, 'wiki_spaces': {'owner_id','kind','personal_user_id','members_initialized','access_scope','include_subdepartments'}}.get(name, set())
            for col in Base.metadata.tables[name].columns:
                if col.name not in omitted: table.append_column(col._copy())
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users(id,username,display_name,department,role,password_hash,active,created_at) VALUES (1,'admin','原管理员','研发部','admin','unused',1,CURRENT_TIMESTAMP)"))
            conn.execute(text("INSERT INTO users(id,username,display_name,department,role,password_hash,active,created_at) VALUES (2,'author','原作者','研发部','applicant','unused',1,CURRENT_TIMESTAMP)"))
            conn.execute(text("INSERT INTO wiki_spaces(id,key,name,description,created_by,created_at) VALUES (10,'OLD','原有空间','说明',1,CURRENT_TIMESTAMP)"))
            conn.execute(text("INSERT INTO wiki_articles(id,title,category,content,author_id,updated_at) VALUES (20,'原始文档','规范','保留原文',2,'2026-01-02 03:04:05')"))
        upgrade(engine); upgrade(engine); Base.metadata.create_all(engine)
        with Session(engine) as db:
            seed_knowledge(db); db.commit()
            article = db.get(WikiArticle, 20)
            default = db.scalar(select(WikiSpace).where(WikiSpace.key == 'OPS'))
            self.assertEqual((article.space_id, article.version, article.status), (default.id, 1, 'published'))
            self.assertEqual(article.revision, 1)
            self.assertEqual(db.get(WikiSpace, 10).owner_id, 1)
            self.assertEqual(db.get(WikiSpace, 10).access_scope, 'members')
            self.assertTrue(db.get(WikiSpace, 10).include_subdepartments)
            original = db.scalar(select(WikiArticleVersion))
            self.assertEqual((original.content, original.author_id, original.created_at.isoformat()),
                             ('保留原文', 2, '2026-01-02T03:04:05'))
            member=db.get(WikiSpaceMember,(default.id,2))
            self.assertIsNotNone(member);self.assertEqual(member.role,'editor')
            db.delete(member);db.commit()
            seed_knowledge(db); db.commit()
            self.assertIsNone(db.get(WikiSpaceMember,(default.id,2)))
            self.assertEqual(len(db.scalars(select(WikiSpace)).all()), 4)
            self.assertEqual(len(db.scalars(select(WikiArticleVersion)).all()), 1)
        engine.dispose()

    def test_fresh_application_startup_has_default_space_and_seed_document_versions(self):
        with tempfile.TemporaryDirectory(prefix='wiki-startup-test-') as folder:
            engine = create_engine('sqlite:///' + str(Path(folder) / 'fresh.db'))
            sessions = sessionmaker(engine, expire_on_commit=False)
            with patch('app.main.engine', engine), patch('app.main.SessionLocal', sessions):
                with TestClient(main.app):
                    with sessions() as db:
                        space = db.scalar(select(WikiSpace).where(WikiSpace.key == 'OPS'))
                        self.assertEqual(space.key, 'OPS')
                        self.assertIsNotNone(space.owner_id)
                        articles = db.scalars(select(WikiArticle)).all()
                        self.assertGreater(len(articles), 0)
                        self.assertTrue(all(row.space_id == space.id for row in articles))
                        self.assertEqual(len(db.scalars(select(WikiArticleVersion)).all()), len(articles))
            engine.dispose()


if __name__ == '__main__': unittest.main()
