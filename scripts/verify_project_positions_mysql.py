"""Disposable MySQL 8 upgrade, policy and rollback-safety checks."""
import os,json,subprocess,tempfile,time,sys
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
CONTAINER='project-positions-mysql-test'

def run():
    assert not subprocess.check_output(['docker','ps','-aq','--filter','name=^/'+CONTAINER+'$'],text=True).strip()
    subprocess.run(['docker','run','-d','--name',CONTAINER,'-p','127.0.0.1:13318:3306','-e','MYSQL_ROOT_PASSWORD=project_positions_test_only','-e','MYSQL_DATABASE=yunweishenji','mysql:8.4'],check=True,capture_output=True)
    try:
        with tempfile.TemporaryDirectory(prefix='project-positions-mysql-') as folder:
            os.environ.update(INSTANCE_DIR=folder,DATABASE_URL='mysql+pymysql://root:project_positions_test_only@127.0.0.1:13318/yunweishenji?charset=utf8mb4',DATABASE_CONFIG_MODE='environment',REQUIRE_SHARED_KEYS='false',SEED_DEMO_DATA='false',REDIS_URL='')
            sys.path.insert(0,str(ROOT/'backend'))
            from sqlalchemy import select,text,func
            from fastapi.testclient import TestClient
            from app import main,flow_engine as flow,organization as org
            from app.database import Base,engine,SessionLocal
            from app.models import (User,UserRole,Department,DepartmentManager,DepartmentMembership,Project,ProjectMember,DepartmentPosition,DepartmentPositionRole,DepartmentUserPosition,WorkflowTemplate,WorkflowVersion,WorkflowInstance,WorkflowNode,Approval,ChangeRequest,SystemSetting)
            from app.project_position_upgrade import upgrade_project_positions
            from app.roles import role_ids
            from app.security import make_token
            from app.rate_limit import RateDecision
            for attempt in range(90):
                try:
                    with engine.connect() as conn:conn.execute(text('SELECT 1'))
                    break
                except Exception:
                    if attempt==89:raise
                    time.sleep(.5)
            Base.metadata.create_all(engine)
            with engine.begin() as conn:conn.execute(text('ALTER TABLE workflow_nodes DROP COLUMN assignment_policy'))
            for _ in range(2):
                result=subprocess.run(['docker','exec','-i','-e','MYSQL_PWD=project_positions_test_only',CONTAINER,'mysql','-uroot'],input=(ROOT/'database/v1.0.8.sql').read_bytes(),capture_output=True)
                assert result.returncode==0,result.stderr.decode()
            ids={}
            with SessionLocal() as db:
                dept=Department(name='研发');db.add(dept);db.flush();did=dept.id
                for name,roles in [('admin',['admin']),('owner',['applicant']),('head',['applicant']),('pm',['applicant']),('ops',['ops_head']),('worker',['operator']),('sales',['sales_presales'])]:
                    user=User(username=name,display_name=name,department='研发',department_id=did,role=roles[0],role_grants=[UserRole(role=r) for r in roles],password_hash='retained-hash')
                    db.add(user);db.flush();ids[name]=user.id;db.add(DepartmentMembership(user_id=user.id,department_id=did))
                db.add(DepartmentManager(user_id=ids['head'],department_id=did))
                project=Project(name='MySQL 项目',department='研发',department_id=did,created_by=ids['admin']);db.add(project);db.flush();pid=project.id
                for who,role in [('owner','applicant'),('pm','project_manager'),('sales','sales_presales')]:db.add(ProjectMember(project_id=pid,user_id=ids[who],role=role))
                job=DepartmentPosition(name='运维实施工程师',department_id=did,role_grants=[DepartmentPositionRole(role='operator')]);db.add(job);db.flush();jid=job.id
                db.add(DepartmentUserPosition(user_id=ids['worker'],department_id=did,position_id=jid))
                definition=flow.department_first_definition({'steps':[{'id':'oldreview','name':'部门负责人','type':'approval','mode':'all','assignee':{'source':'organization_manager'}},{'id':'ops_head','name':'运维负责人','type':'approval','mode':'all','assignee':{'source':'fixed','user_ids':[ids['ops']]}},{'id':'operator','name':'运维回填','type':'execution','mode':'all','assignee':{'source':'fixed','user_ids':[ids['worker']]}}]})
                raw=flow.dump(definition);db.add(WorkflowTemplate(kind='change',draft=raw));version=WorkflowVersion(kind='change',version=1,definition=raw);db.add(version);db.flush();vid=version.id
                item=ChangeRequest(code='MYSQL-OLD',name='旧在途单',project_id=pid,owner_id=ids['owner'],application_department_id=did,change_type='配置变更',environment='生产',window_start='2027-01-01T10:00',window_end='2027-01-01T11:00',problem_description='原审批',status='in_progress',stage='operator');db.add(item);db.flush();oid=item.id
                instance=WorkflowInstance(object_type='change',object_id=oid,version_id=vid,round=1,snapshot=flow.dump({'original':True}),status='in_progress');db.add(instance);db.flush();iid=instance.id
                snap={'id':ids['worker'],'name':'worker','required_role':'operator','roles':['operator']}
                config=flow.dump({'id':'operator','assignee':{'source':'fixed','user_ids':[ids['worker']]},'assignees':[snap]})
                node=WorkflowNode(instance_id=iid,position=0,name='旧回填',node_type='execution',mode='all',config=config,status='pending');db.add(node);db.flush();nid=node.id
                task=Approval(object_type='change',object_id=oid,node_run_id=nid,stage='operator',assignee_id=ids['worker'],assignee_snapshot=flow.dump(snap));db.add(task);db.flush();tid=task.id;task_raw=task.assignee_snapshot
                db.commit()
                upgrade_project_positions(db,main.OBJECTS);db.commit()
                assert db.get(WorkflowInstance,iid).snapshot==flow.dump({'original':True})
                assert db.get(WorkflowNode,nid).config==config
                assert db.get(Approval,tid).assignee_snapshot==task_raw
                assert db.get(WorkflowVersion,vid).definition==raw
                assert db.get(DepartmentPosition,jid).role_grants==[]
                assert role_ids(db.get(User,ids['worker']))==['applicant']
                assert {m.role for m in db.scalars(select(ProjectMember).where(ProjectMember.user_id==ids['sales']))}=={'sales','presales'}
                assert flow.eligible(db.get(User,ids['worker']),db.get(Approval,tid))
                assert org.unavailable_workflow_nodes(db)==set()
                count=db.scalar(select(func.count()).select_from(SystemSetting));upgrade_project_positions(db,main.OBJECTS);db.commit();assert count==db.scalar(select(func.count()).select_from(SystemSetting))
            headers={name:{'Authorization':'Bearer '+make_token(uid)} for name,uid in ids.items()}
            client=TestClient(main.app)
            with patch('app.main.check_request_rate',return_value=RateDecision()),patch('app.main.send_notification'):
                result=client.post(f'/api/change/{oid}/action',headers=headers['worker'],json={'task_id':tid,'action':'approve','fulfillment':'完成','implementation_plan':'实施','rollback_plan':'恢复'})
                assert result.status_code==200,result.text
                assert result.json()['status']=='completed'
                result=client.post('/api/projects',headers=headers['admin'],json={'name':'无部门 MySQL 项目','active':True,'members':[{'user_id':ids['worker'],'role':'project_manager'}]})
                assert result.status_code==200,result.text
                assert result.json()['department_id'] is None
            client.close();engine.dispose()
            report={'result':'passed','checks':9,'cases':['SQL twice','sales split','business grant retirement','department job preservation','immutable history','inflight execution without global role','idempotent migration','valid remaining handlers','project create without department']}
            (ROOT/'reports/project-positions-mysql.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report))
    finally:subprocess.run(['docker','rm','-f',CONTAINER],check=False,capture_output=True)

if __name__=='__main__':run()
