"""Dedicated local organization UI data; no Worker or external notification sends."""
import os
from pathlib import Path
import tempfile


def run():
    with tempfile.TemporaryDirectory(prefix='position-inheritance-ui-') as directory:
        root = Path(directory)
        os.environ.update(INSTANCE_DIR=str(root / 'instance'), DATABASE_URL='sqlite:///' + str(root / 'db.sqlite'),
            DATABASE_CONFIG_MODE='environment', REQUIRE_SHARED_KEYS='false', ATTACHMENT_STORAGE='local',
            SEED_DEMO_DATA='false', REDIS_URL='')
        from contextlib import asynccontextmanager
        from app import main, organization as org
        from app.database import Base, engine, SessionLocal
        from app.models import Department, DepartmentMembership, DepartmentManager, DepartmentPosition, DepartmentPositionRole, User, UserRole
        from app.security import hash_password
        import uvicorn
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            parent = Department(name='产研中心'); db.add(parent); db.flush()
            middle = Department(name='研发中心', parent_id=parent.id); db.add(middle); db.flush()
            child = Department(name='后端组', parent_id=middle.id)
            sibling = Department(name='测试组', parent_id=middle.id)
            external = Department(name='其他部门')
            db.add_all([child, sibling, external]); db.flush()
            for username, name, roles, department in [
                ('pi-admin', '系统管理员', ['admin'], parent), ('pi-head', '后端负责人', ['applicant'], child),
                ('pi-member', '后端成员', ['applicant', 'tester'], child), ('pi-other', '跨部门成员', ['applicant', 'tester'], external)]:
                person = User(username=username, display_name=name, role=roles[0], role_grants=[UserRole(role=role) for role in roles],
                    password_hash=hash_password('PositionInheritance123!'), department=department.name, department_id=department.id)
                db.add(person); db.flush()
                db.add(DepartmentMembership(user_id=person.id, department_id=department.id))
                if username == 'pi-head': db.add(DepartmentManager(department_id=child.id, user_id=person.id))
            for name, roles, department, assignable in [('后端开发', ['product_developer'], parent, True),
                ('统一运维', ['operator'], parent, False), ('研发协作', ['sales_presales'], middle, True)]:
                db.add(DepartmentPosition(name=name, department_id=department.id, manager_assignable=assignable,
                    role_grants=[DepartmentPositionRole(role=role) for role in roles]))
            db.commit()
        @asynccontextmanager
        async def lifespan(_): yield
        main.app.router.lifespan_context = lifespan
        try: uvicorn.run(main.app, host='127.0.0.1', port=18076)
        finally: engine.dispose()


if __name__ == '__main__': run()
