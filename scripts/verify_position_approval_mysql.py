"""MySQL 8 verification; creates/removes a disposable container only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
CONTAINER='position-approval-mysql-test'

def run():
    assert not subprocess.run(['docker','ps','-aq','--filter','name=^/'+CONTAINER+'$'],capture_output=True,text=True).stdout.strip(), 'Test container already exists; inspect it before retrying'
    subprocess.run(['docker','run','-d','--name',CONTAINER,'-p','127.0.0.1:13317:3306','-e','MYSQL_ROOT_PASSWORD=position_approval_test_only','-e','MYSQL_DATABASE=yunweishenji','mysql:8.4'],check=True,capture_output=True)
    try:
        with tempfile.TemporaryDirectory(prefix='approval-mysql-') as folder:
            os.environ.update(INSTANCE_DIR=folder,DATABASE_URL='mysql+pymysql://root:position_approval_test_only@127.0.0.1:13317/yunweishenji?charset=utf8mb4',DATABASE_CONFIG_MODE='environment',REQUIRE_SHARED_KEYS='false',SEED_DEMO_DATA='false',REDIS_URL='')
            sys.path.insert(0,str(ROOT/'backend'))
            from sqlalchemy import inspect,select,text,func
            from fastapi.testclient import TestClient
            from app import main,organization as org,flow_engine as flow
            from app.database import Base,engine,SessionLocal
            from app.models import User,UserRole,Department,DepartmentManager,DepartmentMembership,Project,ProjectMember,WorkflowTemplate,WorkflowVersion,DepartmentPosition,Approval,WorkflowInstance
            from app.roles import effective_role_ids,direct_role_ids
            from app.security import make_token
            from app.rate_limit import RateDecision
            for attempt in range(60):
                try:
                    with engine.connect() as conn:conn.execute(text('SELECT 1'))
                    break
                except Exception:
                    if attempt==59:raise
                    time.sleep(.5)
            Base.metadata.create_all(engine)
            ids={}
            with SessionLocal() as db:
                department=Department(name='研发');db.add(department);db.flush();did=department.id
                for name,roles in [('admin',['admin']),('owner',['applicant','project_owner']),('head',['applicant']),('ops',['operator']),('final',['applicant'])]:
                    u=User(username=name,display_name=name,password_hash='retained-hash',department='研发',department_id=did,role=roles[0],role_grants=[UserRole(role=r) for r in roles]);db.add(u);db.flush();ids[name]=u.id
                    db.add(DepartmentMembership(user_id=u.id,department_id=did))
                db.add(DepartmentManager(department_id=did,user_id=ids['head']))
                project=Project(name='保留项目',department='研发',department_id=did,created_by=ids['admin']);db.add(project);db.flush();pid=project.id
                db.add(ProjectMember(project_id=pid,user_id=ids['owner'],role='project_owner'))
                for kind in flow.KINDS:
                    final={'id':'execute','name':'执行','type':'execution','mode':'all','assignee':{'source':'fixed','user_ids':[ids['ops']]}}
                    if kind=='license':final={'id':'final','name':'最终审批','type':'license_approval','mode':'all','assignee':{'source':'fixed','user_ids':[ids['final']]}}
                    definition={'steps':[{'id':'oldhead','name':'旧首审','type':'approval','mode':'all','assignee':{'source':'department_role','role':'department_head','department_scope':'parent'}},final]}
                    db.add(WorkflowTemplate(kind=kind,draft=flow.dump(definition)));db.add(WorkflowVersion(kind=kind,version=1,definition=flow.dump(definition)))
                db.commit()
                before_roles={u.id:set(effective_role_ids(u)) for u in db.scalars(select(User))}
                before_passwords={u.id:u.password_hash for u in db.scalars(select(User))}
                before_versions=[(v.id,v.definition) for v in db.scalars(select(WorkflowVersion))]
            # Recreate the prior schema by removing only the new nullable columns.
            tables=['resource_requests','change_requests','delivery_projects','license_requests']
            with engine.begin() as conn:
                for table in tables:
                    inspector=inspect(conn)
                    for fk in inspector.get_foreign_keys(table):
                        if fk['constrained_columns']==['application_department_id']:
                            conn.execute(text(f'ALTER TABLE {table} DROP FOREIGN KEY `{fk["name"]}`'))
                    conn.execute(text(f'ALTER TABLE {table} DROP COLUMN application_department_id'))
            for _ in range(2):
                result=subprocess.run(['docker','exec','-i','-e','MYSQL_PWD=position_approval_test_only',CONTAINER,'mysql','-uroot'],input=(ROOT/'database/v1.0.7.sql').read_bytes(),capture_output=True)
                assert result.returncode==0,result.stderr.decode()
            from app.migrations import upgrade,finish_upgrade
            upgrade(engine);finish_upgrade(engine);upgrade(engine);finish_upgrade(engine)
            with SessionLocal() as db:
                org.migrate_business_grants_to_positions(db);flow.upgrade_department_first_approval(db);main.resource_catalog.seed(db);db.commit();db.expire_all()
                counts=(db.scalar(select(func.count()).select_from(DepartmentPosition)),db.scalar(select(func.count()).select_from(WorkflowVersion)))
                org.migrate_business_grants_to_positions(db);flow.upgrade_department_first_approval(db);db.commit()
                assert counts==(db.scalar(select(func.count()).select_from(DepartmentPosition)),db.scalar(select(func.count()).select_from(WorkflowVersion)))
                for u in db.scalars(select(User)):
                    assert before_roles[u.id]<=set(effective_role_ids(u));assert u.password_hash==before_passwords[u.id]
                for key,definition in before_versions:assert db.get(WorkflowVersion,key).definition==definition
                assert db.scalar(select(ProjectMember)).role=='project_owner'
            headers={name:{'Authorization':'Bearer '+make_token(uid)} for name,uid in ids.items()}
            client=TestClient(main.app)
            with patch('app.main.check_request_rate',return_value=RateDecision()),patch('app.main.send_notification'):
                body={'project_id':pid,'application_department_id':did,'environment':'生产','name':'并发撤权验证','change_type':'配置变更','window_start':'2027-01-01T10:00','window_end':'2027-01-01T11:00','problem_description':'组织调整'}
                result=client.post('/api/changes',json=body,headers=headers['owner']);assert result.status_code==200,result.text
                oid=result.json()['id']
                with SessionLocal() as db:
                    task=db.scalar(select(Approval).where(Approval.object_type=='change',Approval.object_id==oid,Approval.action=='pending'));tid=task.id
                def act():return client.post(f'/api/change/{oid}/action',json={'task_id':tid,'action':'approve'},headers=headers['head'])
                def revoke():return client.patch(f'/api/users/{ids["ops"]}',json={'active':False},headers=headers['admin'])
                with ThreadPoolExecutor(max_workers=2) as pool:
                    a=pool.submit(act);r=pool.submit(revoke);ar,rr=a.result(),r.result()
                assert ar.status_code==200,ar.text;assert rr.status_code==409,rr.text
                with SessionLocal() as db:
                    assert db.get(User,ids['ops']).active
                    task=db.scalar(select(Approval).where(Approval.object_type=='change',Approval.object_id==oid,Approval.action=='pending'))
                    assert task.assignee_id==ids['ops'];tid=task.id
                done=client.post(f'/api/change/{oid}/action',json={'task_id':tid,'action':'approve','fulfillment':'完成','implementation_plan':'实施','rollback_plan':'回退'},headers=headers['ops'])
                assert done.status_code==200,done.text;assert done.json()['status']=='completed'
            for table in tables:
                inspector=inspect(engine)
                assert 'application_department_id' in {c['name'] for c in inspector.get_columns(table)}
                assert any(fk['constrained_columns']==['application_department_id'] for fk in inspector.get_foreign_keys(table))
            client.close();engine.dispose()
            report={'checks':8,'result':'passed','mysql':'8.4','sql_repeat':'passed','startup_repeat':'passed','grant_migration':'preserved','history':'preserved','concurrent_revoke_approval':'passed','execution':'completed'}
            (ROOT/'reports/position-approval-mysql.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
    finally:subprocess.run(['docker','rm','-f',CONTAINER],capture_output=True)

if __name__=='__main__':run()
