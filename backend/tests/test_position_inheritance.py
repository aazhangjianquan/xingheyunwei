"""Ancestor positions, independent department assignments and safe schema upgrades."""
import tempfile
import unittest
from pathlib import Path
from sqlalchemy import create_engine, event, inspect, select, text
from app import organization as org
from app.migrations import upgrade_position_assignments
from app.models import Department, DepartmentPosition, DepartmentPositionRole, DepartmentUserPosition, User
from app.roles import effective_role_ids
try:
    from . import test_organization as baseline
except ImportError:
    import test_organization as baseline


class PositionInheritanceTests(unittest.TestCase):
    setUp = baseline.OrganizationTests.setUp
    tearDown = baseline.OrganizationTests.tearDown
    request = baseline.OrganizationTests.request
    department = baseline.OrganizationTests.department
    appoint = baseline.OrganizationTests.appoint
    position = baseline.OrganizationTests.position
    member = baseline.OrganizationTests.member
    assign = baseline.OrganizationTests.assign

    def create_department(self, name, parent=None):
        response = self.request('POST', '/departments', body={'name': name, 'parent_id': parent})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create_position(self, department_id, name='统一销售', roles=('sales_presales',), **extra):
        response = self.request('POST', f'/departments/{department_id}/positions',
                                body={'name': name, 'roles': [], **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def move(self, department_id, parent_id):
        department = next(d for d in self.request('GET', '/departments').json() if d['id'] == department_id)
        return self.request('PATCH', f'/departments/{department_id}', body={
            'name': department['name'], 'parent_id': parent_id, 'revision': department['revision']})

    def tree(self):
        root = self.create_department('产研中心')
        self.assertEqual(self.move(self.depts['甲部'], root['id']).status_code, 200)
        self.assertEqual(self.move(self.depts['乙部'], root['id']).status_code, 200)
        child = self.create_department('后端组', self.depts['甲部'])
        return root, child

    def test_all_ancestors_are_visible_and_unrelated_positions_cannot_be_assigned(self):
        root, child = self.tree()
        top = self.create_position(root['id'])
        middle = self.position('开发')
        local = self.create_position(child['id'], '本地岗位')
        unrelated = self.create_position(self.depts['平台'], '平台岗位')
        for endpoint in ['/departments', '/organization/options']:
            departments = {d['id']: d for d in self.request('GET', endpoint).json()}
            positions = {p['id']: p for p in departments[child['id']]['positions']}
            self.assertEqual(set(positions), {top['id'], middle['id'], local['id']})
            self.assertEqual(positions[top['id']]['source_department_name'], '产研中心')
            self.assertTrue(positions[top['id']]['inherited'])
            self.assertFalse(positions[local['id']]['inherited'])
        self.assertEqual(self.assign([unrelated['id']], department=child['id']).status_code, 409)
        self.assertEqual(self.assign([top['id'], middle['id']], department=child['id'], set_primary=False).status_code, 200)
        person = self.member()
        self.assertNotIn(root['id'], person['department_ids'])
        self.assertEqual({p['department_id'] for p in person['positions']}, {child['id']})
        self.assertNotIn('sales_presales', person['roles']); self.assertTrue(person['positions'])
        self.assertEqual(self.request('GET', '/users?page=1&role=sales_presales').json()['total'], 0)
        response = self.request('GET', f'/departments/{child["id"]}/positions')
        self.assertEqual({p['id'] for p in response.json()}, set(positions))

    def test_same_position_in_multiple_departments_keeps_independent_grants_and_counts(self):
        root, _ = self.tree()
        position = self.create_position(root['id'])
        for dept in ['甲部', '乙部']:
            self.assertEqual(self.assign([position['id']], department=self.depts[dept], set_primary=False).status_code, 200)
        person = self.member()
        self.assertEqual(len(person['positions']), 2)
        departments = {d['id']: d for d in self.request('GET', '/departments').json()}
        self.assertEqual(departments[root['id']]['positions'][0]['member_count'], 1)
        self.assertEqual(departments[root['id']]['positions'][0]['referenced_department_count'], 2)
        for dept in ['甲部', '乙部']:
            self.assertEqual(departments[self.depts[dept]]['positions'][0]['member_count'], 1)
        before_projects = self.request('GET', f'/users/{person["id"]}/organization-impact').json()['project_assignments']
        result = self.request('DELETE', f'/users/{person["id"]}/departments/{self.depts["乙部"]}?revision={person["organization_revision"]}')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual({p['department_id'] for p in result.json()['positions']}, {self.depts['甲部']})
        self.assertNotIn('sales_presales', result.json()['roles']); self.assertTrue(result.json()['positions'])
        self.assertIn('tester', result.json()['roles'])
        self.assertEqual(self.request('GET', f'/users/{person["id"]}/organization-impact').json()['project_assignments'], before_projects)
        self.assign([], department=self.depts['甲部'])
        self.assertNotIn('sales_presales', self.member()['roles'])
        self.assertIn('tester', self.member()['roles'])

    def test_middle_department_counts_all_descendant_assignments_without_siblings_or_duplicates(self):
        root, child = self.tree()
        second_child = self.create_department('前端组', self.depts['甲部'])
        position = self.create_position(root['id'], '工程师', ())
        for target, department in [('employee', self.depts['甲部']), ('employee', child['id']),
                                   ('employee', second_child['id']), ('outsider', second_child['id']),
                                   ('outsider', self.depts['乙部']), ('pm', self.depts['乙部'])]:
            self.assertEqual(self.assign([position['id']], target=target, department=department,
                                         set_primary=False).status_code, 200)
        with self.sessions() as db:
            from app.models import Approval
            db.add(Approval(assignee_id=self.ids['employee'], action='pending', object_type='resource',
                            object_id=1, stage='department_head'))
            db.commit()
        directory = {d['id']: d for d in self.request('GET', '/departments').json()}
        expected = {root['id']: (0, 3, 4), self.depts['甲部']: (1, 2, 3),
                    self.depts['乙部']: (2, 2, 1), child['id']: (1, 1, 1), second_child['id']: (2, 2, 1)}
        for department_id, (direct, total, references) in expected.items():
            output = next(p for p in directory[department_id]['positions'] if p['id'] == position['id'])
            self.assertEqual((output['direct_member_count'], output['member_count'],
                              output['referenced_department_count']), (direct, total, references))
            self.assertEqual(output['member_count_scope'], 'department_subtree')
            api_output = self.request('GET', f'/departments/{department_id}/positions').json()
            self.assertEqual(next(p for p in api_output if p['id'] == position['id']), output)
        middle = next(p for p in directory[self.depts['甲部']]['positions'] if p['id'] == position['id'])
        self.assertEqual(middle['pending_tasks'], 1)
        self.assertEqual(middle['project_assignments'], 1)
        self.assertTrue(middle['has_descendants'])
        self.assertFalse(directory[child['id']]['positions'][0]['has_descendants'])
        # The unbatched response used by editing a department must agree with the directory.
        department = directory[self.depts['甲部']]
        updated = self.request('PATCH', f'/departments/{department["id"]}', body={
            'name': department['name'], 'parent_id': root['id'], 'revision': department['revision']})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(next(p for p in updated.json()['positions'] if p['id'] == position['id']), middle)
        self.assertEqual(set(self.member()['roles']), {'applicant', 'tester'})

    def test_subtree_statistics_do_not_expand_manager_member_visibility_or_assignment_scope(self):
        root, child = self.tree()
        position = self.create_position(root['id'])
        self.assign([position['id']], target='outsider', department=child['id'], set_primary=False)
        self.appoint()
        directory = self.request('GET', '/departments', 'head').json()
        output = next(p for p in directory[0]['positions'] if p['id'] == position['id'])
        self.assertEqual((output['direct_member_count'], output['member_count']), (0, 1))
        self.assertTrue(output['can_assign'])
        visible = self.request('GET', '/users?page=1&page_size=50', 'head').json()['items']
        self.assertNotIn(self.ids['outsider'], {person['id'] for person in visible})
        self.assertEqual(self.assign([], who='head', target='outsider', department=child['id'], set_primary=False).status_code, 403)
        self.assertEqual(self.request('GET', f'/departments/{child["id"]}/positions', 'head').status_code, 403)

    def test_manager_assigns_inherited_safe_positions_only_within_appointed_department(self):
        root, _ = self.tree()
        safe = self.create_position(root['id'])
        sensitive = self.create_position(root['id'], '统一运维', ('operator',), manager_assignable=False)
        self.appoint()
        self.assign([], department=self.depts['乙部'], set_primary=False)
        directory = self.request('GET', '/departments', 'head').json()
        self.assertEqual([d['id'] for d in directory], [self.depts['甲部']])
        self.assertTrue(next(p for p in directory[0]['positions'] if p['id'] == safe['id'])['can_assign'])
        self.assertFalse(next(p for p in directory[0]['positions'] if p['id'] == sensitive['id'])['can_assign'])
        self.assertEqual(self.assign([safe['id']], who='head', department=self.depts['甲部'], set_primary=False).status_code, 200)
        self.assertEqual(self.assign([safe['id']], who='head', department=self.depts['乙部'], set_primary=False).status_code, 403)
        self.assertEqual(self.assign([sensitive['id']], who='head', set_primary=False).status_code, 403)
        self.assertEqual(self.assign([safe['id']], who='employee').status_code, 403)
        self.assertEqual(self.request('GET', f'/departments/{root["id"]}/positions', 'head').status_code, 403)

    def test_duplicate_names_are_blocked_across_ancestors_in_both_directions(self):
        root, child = self.tree()
        top = self.create_position(root['id'], 'Sales')
        response = self.request('POST', f'/departments/{child["id"]}/positions', body={'name': 'ＳＡＬＥＳ'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('产研中心', response.text)
        other = self.create_position(child['id'], '后端开发')
        response = self.request('POST', f'/departments/{root["id"]}/positions', body={'name': '后端开发'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('后端组', response.text)
        response = self.request('PATCH', f'/department-positions/{other["id"]}', body={'name': top['name'], 'revision': other['revision']})
        self.assertEqual(response.status_code, 409)
        self.create_position(self.depts['乙部'], '后端开发')  # Siblings have independent definitions.
        disabled = self.request('PATCH', f'/department-positions/{top["id"]}', body={'name': top['name'], 'active': False, 'revision': top['revision']}).json()
        self.assertFalse(disabled['active'])
        self.assertEqual(self.request('POST', f'/departments/{child["id"]}/positions', body={'name': 'sales'}).status_code, 409)
        self.assertEqual(self.assign([top['id']], department=child['id']).status_code, 409)

    def test_existing_duplicates_are_preserved_without_automatic_role_changes(self):
        root, child = self.tree()
        source = self.create_position(root['id'])
        with self.sessions() as db:
            existing = DepartmentPosition(department_id=child['id'], name=source['name'], active=True, manager_assignable=True)
            existing.role_grants = [DepartmentPositionRole(role='product_developer')]
            db.add(existing); db.flush(); existing_id = existing.id
            db.commit()
        self.assertEqual(self.assign([existing_id, source['id']], department=child['id'], set_primary=False).status_code, 200)
        self.assertIn('product_developer', self.member()['roles']); self.assertNotIn('sales_presales', self.member()['roles']); self.assertEqual(len(self.member()['positions']),2)
        updated = self.request('PATCH', f'/department-positions/{source["id"]}', body={
            'name': source['name'], 'roles': source['roles'], 'revision': source['revision']})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(self.move(child['id'], self.depts['甲部']).status_code, 200)
        positions = self.request('GET', f'/departments/{child["id"]}/positions').json()
        self.assertEqual({p['id'] for p in positions}, {source['id'], existing_id})

    def test_reparent_checks_lost_references_new_collisions_and_atomic_failure(self):
        root, child = self.tree()
        source = self.create_position(root['id'])
        self.assign([source['id']], department=child['id'], set_primary=False)
        response = self.move(self.depts['甲部'], None)
        self.assertEqual(response.status_code, 409)
        self.assertIn('后端组', response.text)
        self.assertEqual(self.department()['parent_id'], root['id'])
        self.assertNotIn('sales_presales', self.member()['roles']); self.assertTrue(self.member()['positions'])
        self.assertEqual(self.move(child['id'], self.depts['乙部']).status_code, 200)
        self.assign([], department=child['id'])
        self.assertEqual(self.move(child['id'], None).status_code, 200)
        local = self.create_position(child['id'], source['name'])
        self.assertEqual(self.move(child['id'], root['id']).status_code, 409)
        with self.sessions() as db:
            self.assertIsNone(db.get(Department, child['id']).parent_id)
            self.assertIsNotNone(db.get(DepartmentPosition, local['id']))

    def test_source_definition_changes_update_all_references_and_delete_is_blocked(self):
        root, child = self.tree()
        position = self.create_position(root['id'])
        self.assign([position['id']], department=child['id'], set_primary=False)
        self.assign([position['id']], department=self.depts['乙部'], set_primary=False)
        impact = self.request('GET', f'/department-positions/{position["id"]}/deletion-impact').json()
        self.assertEqual(impact['blockers'], [{'label': '岗位成员（含停用成员）', 'count': 1}])
        self.assertEqual(self.request('DELETE', f'/department-positions/{position["id"]}?revision={position["revision"]}').status_code, 409)
        for roles, active, expected in [([], True, set()), ([], False, set()), ([], True, set())]:
            response = self.request('PATCH', f'/department-positions/{position["id"]}', body={
                'name': position['name'], 'roles': roles, 'active': active, 'revision': position['revision']})
            self.assertEqual(response.status_code, 200, response.text)
            position = response.json()
            actual = set(self.member()['roles']) & {'sales_presales', 'product_developer'}
            self.assertEqual(actual, expected)
            for role in ['sales_presales', 'product_developer']:
                self.assertEqual(self.request('GET', f'/users?page=1&role={role}').json()['total'], int(role in expected))
        self.assertEqual(len(self.member()['positions']), 2)

    def test_user_patch_keeps_inherited_positions_and_new_account_can_select_them(self):
        root, child = self.tree()
        source = self.create_position(root['id'])
        self.assign([source['id']], department=self.depts['甲部'])
        person = self.member()
        response = self.request('PATCH', f'/users/{person["id"]}', body={
            'department_id': self.depts['甲部'], 'organization_revision': person['organization_revision']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['positions'][0]['id'], source['id'])
        created = self.request('POST', '/users', body={'username': 'inherited-new', 'display_name': '新成员',
            'password': 'SecurePass123!', 'roles': ['applicant'], 'department_id': child['id'], 'position_ids': [source['id']]})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()['department_ids'], [child['id']])
        self.assertNotIn('sales_presales', created.json()['roles']); self.assertEqual(created.json()['positions'][0]['id'],source['id'])

    def test_invalid_membership_and_inactive_source_do_not_grant_roles(self):
        root, child = self.tree()
        source = self.create_position(root['id'])
        with self.sessions() as db:
            db.add(DepartmentUserPosition(user_id=self.ids['employee'], position_id=source['id'], department_id=child['id']))
            db.commit()
        self.assertNotIn('sales_presales', self.member()['roles'])
        self.assertEqual(self.request('GET', '/users?page=1&role=sales_presales').json()['total'], 0)
        self.assign([source['id']], department=child['id'], set_primary=False)
        with self.sessions() as db:
            db.get(Department, root['id']).active = False
            db.commit()
        self.assertNotIn('sales_presales', self.member()['roles'])
        self.assertEqual(self.request('GET', '/users?page=1&role=sales_presales').json()['total'], 0)

    def test_directory_queries_remain_batched_for_many_inherited_positions(self):
        root, _ = self.tree()
        self.appoint()
        self.create_position(root['id'])
        def count_queries():
            statements = []
            def capture(_conn, _cursor, statement, *_args): statements.append(statement)
            event.listen(self.engine, 'before_cursor_execute', capture)
            try:
                result = self.request('GET', '/departments', 'head')
                self.assertEqual(result.status_code, 200, result.text)
                return len(statements)
            finally: event.remove(self.engine, 'before_cursor_execute', capture)
        initial = count_queries()
        with self.sessions() as db:
            for index in range(30):
                db.add(DepartmentPosition(department_id=root['id'], name=f'继承岗位{index}',
                    role_grants=[DepartmentPositionRole(role='sales_presales')]))
            db.commit()
        self.assertLessEqual(count_queries(), initial + 2)
        self.assertLess(initial, 30)


class PositionAssignmentMigrationTests(unittest.TestCase):
    def test_sqlite_old_rows_are_backfilled_once_and_multiple_scopes_are_supported(self):
        with tempfile.TemporaryDirectory(prefix='position-upgrade-') as directory:
            engine = create_engine('sqlite:///' + str(Path(directory) / 'upgrade.db'))
            try:
                with engine.begin() as conn:
                    conn.execute(text('CREATE TABLE departments (id INTEGER PRIMARY KEY)'))
                    conn.execute(text('INSERT INTO departments VALUES (1), (2), (3)'))
                    conn.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
                    conn.execute(text('INSERT INTO users VALUES (10)'))
                    conn.execute(text('CREATE TABLE department_positions (id INTEGER PRIMARY KEY, department_id INTEGER NOT NULL)'))
                    conn.execute(text('INSERT INTO department_positions VALUES (100, 1)'))
                    conn.execute(text('CREATE TABLE department_user_positions (user_id INTEGER NOT NULL, position_id INTEGER NOT NULL, '
                                      'PRIMARY KEY(user_id, position_id), FOREIGN KEY(user_id) REFERENCES users(id), '
                                      'FOREIGN KEY(position_id) REFERENCES department_positions(id))'))
                    conn.execute(text('INSERT INTO department_user_positions VALUES (10,100)'))
                upgrade_position_assignments(engine)
                upgrade_position_assignments(engine)
                with engine.begin() as conn:
                    self.assertEqual(conn.execute(text('SELECT * FROM department_user_positions')).all(), [(10, 100, 1)])
                    conn.execute(text('INSERT INTO department_user_positions VALUES (10,100,2), (10,100,3)'))
                upgrade_position_assignments(engine)
                with engine.connect() as conn:
                    self.assertEqual(conn.scalar(text('SELECT COUNT(*) FROM department_user_positions')), 3)
                    self.assertEqual(conn.exec_driver_sql('PRAGMA foreign_key_check').all(), [])
                self.assertEqual(inspect(engine).get_pk_constraint('department_user_positions')['constrained_columns'],
                                 ['user_id', 'position_id', 'department_id'])
            finally: engine.dispose()
