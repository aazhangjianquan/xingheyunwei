"""Destructive verification against the dedicated disposable MySQL container only."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CONTAINER = 'position-inheritance-mysql-test'


def run():
    with tempfile.TemporaryDirectory(prefix='position-mysql-') as directory:
        os.environ.update(INSTANCE_DIR=directory, DATABASE_URL='mysql+pymysql://root:position_test_only@127.0.0.1:13316/yunweishenji?charset=utf8mb4',
            DATABASE_CONFIG_MODE='environment', REQUIRE_SHARED_KEYS='false', SEED_DEMO_DATA='false', REDIS_URL='')
        sys.path.insert(0, str(ROOT / 'backend'))
        from sqlalchemy import create_engine, inspect, select, text
        from sqlalchemy.orm import sessionmaker
        from fastapi.testclient import TestClient
        from app import main
        from app.database import Base, get_db
        from app.models import Department, DepartmentMembership, DepartmentPosition, DepartmentPositionRole, User, UserRole
        from app.migrations import upgrade_position_assignments
        from app.roles import effective_role_ids
        from app.security import make_token
        from app.rate_limit import RateDecision
        engine = create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True)
        for attempt in range(50):
            try:
                with engine.connect() as conn: conn.execute(text('SELECT 1'))
                break
            except Exception:
                if attempt == 49: raise
                time.sleep(1)
        sessions = sessionmaker(engine, expire_on_commit=False)
        Base.metadata.create_all(engine)
        with sessions() as db:
            root = Department(name='产研中心'); db.add(root); db.flush()
            child = Department(name='研发中心', parent_id=root.id); db.add(child); db.flush()
            admin = User(username='test-admin', display_name='管理员', department='产研中心', department_id=root.id,
                password_hash='unused', role='admin', role_grants=[UserRole(role='admin')])
            member = User(username='test-member', display_name='成员', department='产研中心', department_id=root.id,
                password_hash='unused', role='applicant', role_grants=[UserRole(role='applicant')])
            position = DepartmentPosition(name='统一岗位', department_id=root.id,
                role_grants=[DepartmentPositionRole(role='sales_presales')])
            db.add_all([admin, member, position]); db.flush()
            db.add(DepartmentMembership(user_id=member.id, department_id=root.id))
            db.commit()
            root_id, child_id, position_id, member_id, admin_id = root.id, child.id, position.id, member.id, admin.id
        legacy = ('CREATE TABLE department_user_positions (user_id INTEGER NOT NULL, position_id INTEGER NOT NULL, '
            'PRIMARY KEY (user_id,position_id), FOREIGN KEY (user_id) REFERENCES users(id), '
            'FOREIGN KEY (position_id) REFERENCES department_positions(id)) ENGINE=InnoDB')
        with engine.begin() as conn:
            for table in ('personal_reminder_recipients', 'personal_reminder_attachments', 'personal_reminders', 'department_user_positions'):
                conn.execute(text('DROP TABLE ' + table))
            conn.execute(text(legacy))
            conn.execute(text('INSERT INTO department_user_positions VALUES (:u,:p)'), {'u': member_id, 'p': position_id})
        def sql_file(path):
            result = subprocess.run(['docker', 'exec', '-i', '-e', 'MYSQL_PWD=position_test_only', CONTAINER,
                'mysql', '--default-character-set=utf8mb4', '-uroot'], input=Path(path).read_bytes(), capture_output=True)
            if result.returncode: raise AssertionError(result.stderr.decode())
        for _ in range(2):
            for version in ('1.0.4', '1.0.5', '1.0.6'):
                sql_file(ROOT / 'database' / f'v{version}.sql')
        with engine.connect() as conn:
            assert conn.execute(text('SELECT * FROM department_user_positions')).all() == [(member_id, position_id, root_id)]
        # Verify the startup migration independently of the SQL upgrade path.
        with engine.begin() as conn:
            conn.execute(text('DROP TABLE department_user_positions'))
            conn.execute(text(legacy))
            conn.execute(text('INSERT INTO department_user_positions VALUES (:u,:p)'), {'u': member_id, 'p': position_id})
        upgrade_position_assignments(engine); upgrade_position_assignments(engine)
        with engine.connect() as conn:
            assert conn.execute(text('SELECT * FROM department_user_positions')).all() == [(member_id, position_id, root_id)]
        assert inspect(engine).get_pk_constraint('department_user_positions')['constrained_columns'] == ['user_id', 'position_id', 'department_id']
        def dependency():
            with sessions() as db: yield db
        main.app.dependency_overrides[get_db] = dependency
        headers = {'Authorization': 'Bearer ' + make_token(admin_id)}
        with patch('app.main.SessionLocal', sessions), patch('app.main.check_request_rate', return_value=RateDecision()):
            client = TestClient(main.app)
            try:
                with sessions() as db: revision = db.get(User, member_id).organization_revision
                response = client.put(f'/api/users/{member_id}/organization', headers=headers, json={
                    'department_id': child_id, 'position_ids': [position_id], 'revision': revision, 'set_primary': False})
                assert response.status_code == 200, response.text
                assert len(response.json()['positions']) == 2
                listing = client.get('/api/users?page=1&role=sales_presales', headers=headers)
                assert listing.status_code == 200 and listing.json()['total'] == 1, listing.text
                with sessions() as db:
                    assert 'sales_presales' in effective_role_ids(db.get(User, member_id))
                def create(department_id):
                    response = client.post(f'/api/departments/{department_id}/positions', headers=headers,
                        json={'name': '并发同名岗位', 'roles': ['sales_presales']})
                    return response.status_code
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = sorted(pool.map(create, [root_id, child_id]))
                assert results == [200, 409], results
                with engine.connect() as conn:
                    assert conn.scalar(text("SELECT COUNT(*) FROM department_positions WHERE name='并发同名岗位'")) == 1
                print('MySQL 8.4: repeated SQL v1.0.4/v1.0.5/v1.0.6, independent startup upgrade, old grants, multi-department assignment, role SQL parity and concurrent duplicate rejection passed.')
                # Every install SQL must agree with the updated ORM assignment key.
                for path in ('database/install.sql', 'database/schema.sql', 'k8s-deploy/v1.0.0.sql', 'docker-deploy/database/v1.0.0.sql'):
                    with engine.begin() as conn:
                        conn.execute(text('DROP DATABASE yunweishenji'))
                        conn.execute(text('CREATE DATABASE yunweishenji CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'))
                    engine.dispose()
                    sql_file(ROOT / path); sql_file(ROOT / path)
                    inspector = inspect(engine)
                    assert len(inspector.get_table_names()) == len(Base.metadata.tables)
                    assert inspector.get_pk_constraint('department_user_positions')['constrained_columns'] == ['user_id', 'position_id', 'department_id']
                    print('Repeat install passed:', path)
            finally:
                client.close(); main.app.dependency_overrides.pop(get_db, None); engine.dispose()


if __name__ == '__main__':
    run()
