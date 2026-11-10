"""Disposable UI environment; credentials are fixtures and notifications are disabled."""
import os
from pathlib import Path
import tempfile


def run():
    with tempfile.TemporaryDirectory(prefix='position-approval-ui-') as folder:
        os.environ.update(INSTANCE_DIR=folder, DATABASE_URL='sqlite:///'+str(Path(folder)/'db.sqlite'),
            DATABASE_CONFIG_MODE='environment', REQUIRE_SHARED_KEYS='false', SEED_DEMO_DATA='false', REDIS_URL='')
        from contextlib import asynccontextmanager
        from app import main, organization as org, flow_engine as flow
        from app.database import Base, engine, SessionLocal
        from app.models import (Department, DepartmentManager, DepartmentMembership, User, UserRole,
                                Project, ProjectMember, WorkflowTemplate, WorkflowVersion)
        from app.security import hash_password
        import uvicorn
        Base.metadata.create_all(engine)
        ids={}
        with SessionLocal() as db:
            root=Department(name='产研中心');db.add(root);db.flush()
            middle=Department(name='研发中心',parent_id=root.id);db.add(middle);db.flush()
            leaf=Department(name='后端组',parent_id=middle.id);other=Department(name='平台管理');db.add_all([leaf,other]);db.flush()
            for username,name,roles,department in [('pa-admin','系统管理员',['admin'],other),
                    ('pa-head','后端负责人',['applicant'],leaf),('pa-parent','研发负责人',['applicant'],middle),
                    ('pa-member','研发成员',['applicant','tester'],leaf),('pa-ops','运维成员',['operator'],root),
                    ('pa-final','授权审批人',['applicant'],root)]:
                person=User(username=username,display_name=name,department=department.name,department_id=department.id,
                    role=roles[0],role_grants=[UserRole(role=r) for r in roles],password_hash=hash_password('PositionApproval123!'))
                db.add(person);db.flush();ids[username]=person.id
                db.add(DepartmentMembership(user_id=person.id,department_id=department.id))
            db.add(DepartmentMembership(user_id=ids['pa-member'],department_id=middle.id))
            db.add_all([DepartmentManager(user_id=ids['pa-head'],department_id=leaf.id),
                        DepartmentManager(user_id=ids['pa-parent'],department_id=middle.id)])
            project=Project(name='岗位审批验证项目',department=leaf.name,department_id=leaf.id,created_by=ids['pa-admin']);db.add(project);db.flush()
            db.add_all([ProjectMember(project_id=project.id,user_id=ids[name],role='applicant') for name in ['pa-head','pa-parent','pa-member']])
            org.migrate_business_grants_to_positions(db)
            main.resource_catalog.seed(db)
            for kind in flow.KINDS:
                final={'id':'execute','name':'运维执行','type':'execution','mode':'all','assignee':{'source':'fixed','user_ids':[ids['pa-ops']]}}
                if kind=='license':final={'id':'final','name':'License 最终审批','type':'license_approval','mode':'all','assignee':{'source':'fixed','user_ids':[ids['pa-final']]}}
                definition=flow.department_first_definition({'steps':[final]})
                db.add(WorkflowTemplate(kind=kind,draft=flow.dump(definition)))
                db.add(WorkflowVersion(kind=kind,version=1,definition=flow.dump(definition)))
            db.commit()
        @asynccontextmanager
        async def lifespan(_):yield
        main.app.router.lifespan_context=lifespan
        main.send_notification=lambda *args,**kwargs:None
        try:uvicorn.run(main.app,host='127.0.0.1',port=18077)
        finally:engine.dispose()

if __name__=='__main__':run()
