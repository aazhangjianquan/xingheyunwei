"""HTTP pagination: SQL slicing, status totals, search, boundaries and permissions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, event
from sqlalchemy.orm import sessionmaker
from app.database import get_db
from app.main import app
from app.models import User, UserRole, Project, ProjectMember, Approval, ResourceRequest, ChangeRequest, DeliveryProject
from app.security import hash_password, make_token

from app.rate_limit import RateDecision


class PaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='yunwei-page-check-')
        cls.engine = create_engine('sqlite:///' + str(Path(cls.folder.name) / 'pagination.db'))
        cls.sessions = sessionmaker(cls.engine, expire_on_commit=False)
        cls.patches = [patch('app.main.engine', cls.engine), patch('app.main.SessionLocal', cls.sessions),
                       patch('app.main.check_request_rate', return_value=RateDecision())]
        for p in cls.patches: p.start()
        def db_dependency():
            with cls.sessions() as db: yield db
        app.dependency_overrides[get_db] = db_dependency
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.prefix = '分页验收'
        cls.ids = {}
        with cls.sessions() as db:
            for username in ['pageowner', 'pageother', 'pageviewer', 'pageoutsider']:
                user = User(username=username, display_name=username, department='分页部门', role='applicant',
                            password_hash=hash_password('Demo@2026'), role_grants=[UserRole(role='applicant')])
                db.add(user); db.flush(); cls.ids[username] = user.id
            cls.ids['admin'] = db.scalar(select(User.id).where(User.username == 'admin'))
            project = Project(name=cls.prefix, department='分页部门', created_by=cls.ids['admin'])
            db.add(project); db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=cls.ids['pageviewer'], role='applicant'))
            for kind, model in [('resources', ResourceRequest), ('changes', ChangeRequest), ('deliveries', DeliveryProject)]:
                cls.ids[kind] = []
                for i in range(55):
                    status = ['pending', 'in_progress', 'completed', 'rejected'][i % 4]
                    args = {'code': f'PAGE-{kind}-{i:03}', 'status': status, 'stage':'operator', 'project_id':project.id}
                    name = cls.prefix + (" 100%_字面" if i == 54 else f' {i:03}')
                    if kind == 'resources':
                        args.update(project_name=name, applicant_id=cls.ids['pageowner'], environment='测试', resource_type='ECS', specification='2核4GB', start_date='2026-01-01', end_date='2027-01-01', purpose='验证分页', capacity_basis='10人')
                    elif kind == 'changes':
                        args.update(name=name, owner_id=cls.ids['pageowner'], environment='生产', change_type='配置变更', window_start='2026-11-01T22:00', window_end='2026-11-01T23:00', plan='升级')
                    else:
                        args.update(name=name, owner_id=cls.ids['pageowner'], customer='分页客户', cloud_type='专有云', deployment_mode='云平台', environment='POC', compute_type='虚拟机')
                    item = model(**args)
                    db.add(item); db.flush(); cls.ids[kind].append(item.id)
                # An identically searchable record invisible to the applicant/project viewer.
                args['project_id'] = None
                args['code'] = 'PAGE-hidden-' + kind
                args['applicant_id' if kind == 'resources' else 'owner_id'] = cls.ids['pageother']
                db.add(model(**args))
            db.commit()
        cls.headers = {key:{'Authorization':'Bearer '+make_token(uid)} for key,uid in cls.ids.items() if type(uid) is int}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        app.dependency_overrides.pop(get_db, None)
        for p in reversed(cls.patches): p.stop()
        cls.engine.dispose()
        cls.folder.cleanup()

    def read(self, path='resources', who='pageowner', **params):
        params.setdefault('q', self.prefix)
        r = self.client.get('/api/'+path, params=params, headers=self.headers[who])
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_all_three_lists_default_and_page_sizes_have_no_missing_or_repeated_rows(self):
        for path in ['resources', 'changes', 'deliveries']:
            for size in [10, 20, 50]:
                with self.subTest(path=path, size=size):
                    first = self.read(path, page_size=size)
                    self.assertEqual(first['total'], 55)
                    self.assertEqual(first['counts'], {'all':55, 'pending':14, 'in_progress':14, 'completed':14, 'rejected':13})
                    self.assertEqual(len(first['items']), size)
                    all_ids = []
                    for page in range(1, first['total_pages'] + 1):
                        r = self.read(path, page=page, page_size=size)
                        self.assertEqual(r['page'], page)
                        self.assertEqual(r['counts'], first['counts'])
                        self.assertLessEqual(len(r['items']), size)
                        all_ids.extend(x['id'] for x in r['items'])
                    self.assertEqual(all_ids, list(reversed(self.ids[path])))
            self.assertEqual(self.read(path)['page_size'], 10)

    def test_status_totals_count_entire_filtered_set_and_not_current_slice(self):
        first = self.read(status='pending', page_size=10)
        second = self.read(status='pending', page_size=10, page=2)
        self.assertEqual(first['total'], 14)
        self.assertEqual(len(second['items']), 4)
        self.assertEqual(second['counts']['all'], 55)
        self.assertEqual(first['counts'], second['counts'])
        self.assertTrue(all(x['status']=='pending' for x in first['items'] + second['items']))
        for status, count in [('in_progress',14), ('completed',14), ('rejected',13)]:
            self.assertEqual(self.read(status=status)['total'], count)

    def test_search_runs_before_slicing_and_treats_wildcards_literally(self):
        for path in ['resources','changes','deliveries']:
            r = self.read(path, q='100%_字面')
            self.assertEqual(r['total'], 1)
            self.assertEqual(r['items'][0]['id'], self.ids[path][-1])
            self.assertEqual(self.read(path, q='PAGE-'+path+'-000')['total'], 1)
            self.assertEqual(self.read(path, q='pageowner')['total'], 55)
            self.assertEqual(self.read(path, q='  '+self.prefix+'  ')['total'], 55)

    def test_empty_result_and_out_of_range_page_clamping(self):
        empty = self.read(q='没有匹配的名称', page=99)
        self.assertEqual((empty['items'], empty['total'], empty['page'], empty['total_pages']), ([],0,1,1))
        self.assertTrue(all(n==0 for n in empty['counts'].values()))
        last = self.read(page=99, page_size=20)
        self.assertEqual((last['page'],last['total_pages'],len(last['items'])), (3,3,15))
        # Approval transitions may shrink a status's last page between requests.
        with self.sessions() as db:
            row = db.get(ResourceRequest, self.ids['resources'][0])
            row.status = 'completed'; db.commit()
        try:
            r = self.read(status='pending', page=99, page_size=10)
            self.assertEqual((r['page'],r['total'],len(r['items'])), (2,13,3))
        finally:
            with self.sessions() as db:
                db.get(ResourceRequest,self.ids['resources'][0]).status='pending'; db.commit()

    def test_counts_rows_and_search_all_enforce_visibility(self):
        for path in ['resources','changes','deliveries']:
            self.assertEqual(self.read(path, who='pageviewer')['total'], 55)
            self.assertEqual(self.read(path, who='pageoutsider')['total'], 0)
            self.assertEqual(self.read(path, who='pageother')['total'], 1)
            self.assertEqual(self.read(path, who='admin')['total'], 56)
        # Assigned tasks grant visibility, but duplicate task history cannot inflate totals.
        with self.sessions() as db:
            tasks=[Approval(object_type='resource',object_id=self.ids['resources'][0],stage='operator',assignee_id=self.ids['pageoutsider']) for _ in range(2)]
            db.add_all(tasks); db.commit(); task_ids=[t.id for t in tasks]
        try:
            r=self.read(who='pageoutsider')
            self.assertEqual((r['total'],r['counts']['all'],len(r['items'])),(1,1,1))
        finally:
            with self.sessions() as db:
                for task_id in task_ids: db.delete(db.get(Approval,task_id))
                db.commit()

    def test_invalid_paging_parameters_are_rejected(self):
        for path in ['resources','changes','deliveries']:
            for query in ['page=0','page=-1','page=abc','page_size=5','page_size=100','page_size=0','status=unknown','q='+'x'*101]:
                r=self.client.get('/api/'+path+'?'+query,headers=self.headers['pageowner'])
                self.assertEqual(r.status_code,422,(path,query,r.text))

    def test_database_applies_limit_and_offset(self):
        statements=[]
        def capture(conn,cursor,statement,parameters,context,executemany): statements.append(statement)
        event.listen(self.engine,'before_cursor_execute',capture)
        try: self.read(page=2, page_size=10)
        finally: event.remove(self.engine,'before_cursor_execute',capture)
        self.assertTrue(any('resource_requests.id DESC' in sql and 'LIMIT' in sql and 'OFFSET' in sql for sql in statements))


if __name__=='__main__': unittest.main(verbosity=2)
