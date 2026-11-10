"""Disposable project appointment browser fixture; no external workers or messages."""
import os
import tempfile
from pathlib import Path

def run():
    with tempfile.TemporaryDirectory(prefix='project-positions-ui-') as folder:
        os.environ.update(INSTANCE_DIR=folder,DATABASE_URL='sqlite:///'+str(Path(folder)/'startup.sqlite'),DATABASE_CONFIG_MODE='environment',REQUIRE_SHARED_KEYS='false',SEED_DEMO_DATA='false',REDIS_URL='')
        from contextlib import asynccontextmanager
        from test_project_positions import ProjectPositionTests
        from app import main
        from app.database import Base,engine
        from app.models import User,ProjectMember,DepartmentPosition,DepartmentPositionRole,DepartmentUserPosition
        from app.project_position_upgrade import upgrade_project_positions
        from app.security import hash_password
        from sqlalchemy import select
        import uvicorn
        Base.metadata.create_all(engine)
        fixture=ProjectPositionTests();fixture.setUp()
        try:
            with fixture.sessions() as db:
                names={'admin':'系统管理员','head':'部门负责人','owner':'申请成员','pm':'项目经理成员','ops':'项目运维负责人','worker':'张建全（验证）','outside':'跨部门成员','sales':'销售售前成员'}
                for u in db.scalars(select(User)):
                    u.password_hash=hash_password('ProjectPositions123!');u.display_name=names[u.username]
                db.add(ProjectMember(project_id=fixture.pid,user_id=fixture.ids['sales'],role='sales_presales'))
                job=DepartmentPosition(name='运维实施工程师',department_id=fixture.did,role_grants=[DepartmentPositionRole(role='operator')]);db.add(job);db.flush()
                db.add(DepartmentUserPosition(user_id=fixture.ids['worker'],department_id=fixture.did,position_id=job.id))
                upgrade_project_positions(db,main.OBJECTS);main.resource_catalog.seed(db);db.commit()
            @asynccontextmanager
            async def lifespan(_):yield
            main.app.router.lifespan_context=lifespan
            uvicorn.run(main.app,host='127.0.0.1',port=18078)
        finally:fixture.tearDown()

if __name__=='__main__':run()
