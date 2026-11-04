"""Exercise V3 through HTTP against the configured database.

Creates clearly named acceptance users/projects/records; saves and restores each
flow's original published definition and draft. Notification transport is mocked,
but actual notification, task, version and audit rows are committed and checked.
Run inside api: python scripts/verify_v3.py --report /app/reports/workflow-v3.json
"""
import argparse
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text, inspect
from app.main import app
from app.database import SessionLocal, engine
from app.models import (Approval, AuditLog, NotificationLog, WorkflowInstance,
                        WorkflowVersion, WorkflowNode, TaskTransfer, User, ResourceRequest, DeliveryCredential)

parser = argparse.ArgumentParser()
parser.add_argument('--allow-sqlite', action='store_true')
parser.add_argument('--report', default='/app/reports/workflow-v3.json')
args = parser.parse_args()
if engine.dialect.name != 'mysql' and not args.allow_sqlite:
    raise SystemExit('此验收要求真实 MySQL；隔离开发测试可加 --allow-sqlite')
run = datetime.now().strftime('%m%d%H%M%S%f')
report = {'executed_at': run, 'database_engine': engine.dialect.name, 'checks': [], 'cases': [], 'passed': False}
backups = {}

def check(condition, label):
    assert condition, label
    report['checks'].append(label)

with patch('app.main.send_notification'), patch('app.admin.send_notification'), TestClient(app) as c:
    def request(method, path, data=None, who='admin', expected=200):
        response = c.request(method, '/api'+path, json=data, headers=headers.get(who, {}))
        assert response.status_code == expected, f'{method} {path} expected {expected}: {response.status_code} {response.text[:900]}'
        return response.json()
    def login(name, password='Demo@2026'):
        r = c.post('/api/login',json={'username':name,'password':password})
        assert r.status_code == 200, r.text
        return {'Authorization':'Bearer '+r.json()['token']}
    headers = {'admin': login('admin')}
    users = {}
    specs = {
        'owner': (['applicant','project_owner'], '验收研发部'),
        'owner2': (['applicant'], '验收市场部'),
        'pm': (['project_owner','project_manager'], '验收研发部'),
        'pm2': (['project_manager'], '验收市场部'),
        'dev1': (['development_head'], '验收研发部'),
        'dev2': (['development_head'], '验收研发部'),
        'dept1': (['department_head'], '验收研发部'),
        'dept2': (['department_head'], '验收市场部'),
        'ops1': (['operator'], '验收运维部'),
        'ops2': (['operator'], '验收运维部'),
        'outsider': (['applicant'], '验收其他部'),
    }
    # Isolate department-role resolution from earlier acceptance runs. Existing
    # project membership and historic test records remain untouched.
    specs = {key: (grants, department + '-' + run) for key, (grants, department) in specs.items()}
    def node(key, source='fixed', ids=None, role=None, mode='all', execution=False):
        assignee = {'source':source, 'user_ids':ids} if source == 'fixed' else {'source':source, 'role':role}
        return {'id':key,'name':key,'type':'execution' if execution else 'approval','mode':mode,'assignee':assignee}
    def final(): return node('运维执行',ids=[users['ops1']['id']],execution=True)
    def publish(kind, definition):
        t = request('GET',f'/admin/flows/{kind}')
        r = request('PUT',f'/admin/flows/{kind}/draft',{'definition':definition,'revision':t['revision']})
        return request('POST',f'/admin/flows/{kind}/publish',{'revision':r['revision']})
    def payload(kind, pid):
        base = {'project_id':pid,'environment':'生产'}
        if kind=='resource': return {**base,'resource_type':'ECS','specification':'2核4G','start_date':'2026-10-26','end_date':'2027-10-26','purpose':'流程V3验收','capacity_basis':'100用户','ports':'443'}
        if kind=='change': return {**base,'name':'V3验收变更-'+run,'change_type':'配置变更','window_start':'2026-11-01T22:00','window_end':'2026-11-01T23:00','plan':'备份后升级','rollback_plan':'回滚镜像'}
        return {**base,'environment':'正式','name':'V3验收交付-'+run,'customer':'验收客户','cloud_type':'专有云','deployment_mode':'云平台','compute_type':'GPU','accelerator':'NVIDIA'}
    paths={'resource':'resources','change':'changes','delivery':'deliveries'}
    def create(kind='resource', who='owner', pid=None, **changes):
        body={**payload(kind,pid or projects[0]['id']),**changes}
        item=request('POST','/'+paths[kind],body,who)
        return item,body
    def detail(kind,item,who='admin'): return request('GET',f'/{paths[kind]}/{item["id"]}',who=who)
    def pending(d, uid=None): return [t for t in d['approvals'] if t['action']=='pending' and (uid is None or t['assignee_id']==uid)]
    def action(kind,item,who,action='approve',task=None,expected=200,fulfillment='验收执行结果'):
        task=task or pending(detail(kind,item),users[who]['id'])[0]
        return request('POST',f'/{kind}/{item["id"]}/action',{'task_id':task['id'],'action':action,'comment':'V3验收','fulfillment':fulfillment},who,expected)
    def reassign(item, to, reason='验收转交'):
        task=pending(detail('resource',item))[0]
        return request('POST',f'/admin/tasks/{task["id"]}/reassign',{'assignee_id':users[to]['id'],'reason':reason})
    try:
        for key,(roles,dept) in specs.items():
            name='v3_'+key+'_'+run
            users[key]=request('POST','/users',{'username':name,'display_name':'验收'+key,'department':dept,'roles':roles,'password':'Workflow@2026!'})
            headers[key]=login(name,'Workflow@2026!')
        catalog=request('GET','/roles')
        check(len(catalog)==13 and len(users['pm']['roles'])==2,'13 种角色及一人多角色保存')
        projects=[]
        for index in [0,1]:
            members=[{'user_id':users['pm']['id'],'role':'project_owner'}, {'user_id':users['pm' if index==0 else 'pm2']['id'],'role':'project_manager'}, {'user_id':users['owner' if index==0 else 'owner2']['id'],'role':'applicant'}, {'user_id':users['dev1' if index==0 else 'dev2']['id'],'role':'development_head'}]
            if index==0: members += [{'user_id':users['dev2']['id'],'role':'development_head'}, {'user_id':users['owner']['id'],'role':'project_owner'}]
            projects.append(request('POST','/projects',{'name':f'V3验收项目{index}-{run}','department':specs['owner' if index==0 else 'owner2'][1],'members':members}))
        p1,p2=projects
        request('PUT',f'/projects/{p1["id"]}/members',{'members':p1['members']},'pm')
        request('PUT',f'/projects/{p1["id"]}/members',{'members':p1['members']+[{'user_id':users['outsider']['id'],'role':'sales_head'}]},'pm',400)
        request('POST','/projects',{'name':'unauthorized','department':'x','members':p1['members']},'pm',403)
        check(True,'项目负责人维护岗位；不能给无角色人员分配岗位或创建项目')
        backups={k:request('GET',f'/admin/flows/{k}') for k in paths}
        for kind in paths:
            f={'steps':[
                node('项目负责人',source='project_role',role='project_owner'),
                node('研发会签',ids=[users['dev1']['id'],users['dev2']['id']],mode='all'),
                {'id':'环境分支','name':'环境分支','type':'branch','branches':[
                    {'id':'prod','name':'生产路径','match':'all','conditions':[{'field':'environment','op':'in','value':['生产','正式']}], 'steps':[node('研发或签',ids=[users['dev1']['id'],users['dev2']['id']],mode='any')]},
                    {'id':'fallback','name':'默认路径','default':True,'steps':[]}]},final()]}
            publish(kind,f)
            with SessionLocal() as db:
                before_tasks=db.scalar(select(func.count()).select_from(Approval)); before_notify=db.scalar(select(func.count()).select_from(NotificationLog))
            preview=request('POST',f'/admin/flows/{kind}/preview',{'definition':f,'project_id':p1['id'],'applicant_id':users['owner']['id'],'data':payload(kind,p1['id'])})
            with SessionLocal() as db:
                check(db.scalar(select(func.count()).select_from(Approval))==before_tasks and db.scalar(select(func.count()).select_from(NotificationLog))==before_notify,kind+' 预览不创建待办或通知')
            check(preview['path'][0]['assignees']==[next(a for a in preview['path'][0]['assignees'] if a['id']==users['pm']['id'])],kind+' 排除申请人自审')
            item,_=create(kind)
            first=pending(detail(kind,item))[0]
            action(kind,item,'owner',task=first,expected=403)
            action(kind,item,'outsider',task=first,expected=403)
            d=action(kind,item,'pm')
            check(len(pending(d))==2,kind+' 顺序审批进入双人会签')
            d=action(kind,item,'dev1')
            check(len(pending(d))==1 and d['rounds'][-1]['nodes'][1]['status']=='pending',kind+' 会签未齐不流转')
            d=action(kind,item,'dev2')
            stale=pending(d,users['dev2']['id'])[0]
            d=action(kind,item,'dev1')
            action(kind,item,'dev2',task=stale,expected=409)
            check(len(pending(d))==1 and pending(d)[0]['assignee_id']==users['ops1']['id'],kind+' 或签关闭其余任务且仅流转一次')
            action(kind,item,'ops1',fulfillment='',expected=400)
            if kind == 'delivery':
                for db_type in ['MySQL', 'Redis', 'MongoDB', 'PostgreSQL']:
                    secret = 'V3-only-test-password-' + run
                    cred=request('POST',f'/deliveries/{item["id"]}/credentials',{'db_type':db_type,'host':'acceptance.internal','username':'acceptance','password':secret,'privilege':'普通账号'},'ops1')
                    check('password_ciphertext' not in cred and 'password' not in cred,db_type+' 凭据接口不返回密文或明文')
                    with SessionLocal() as db:
                        cipher=db.get(DeliveryCredential,cred['id']).password_ciphertext
                        from app.security import decrypt_secret
                        check(cipher != secret and decrypt_secret(cipher)==secret,db_type+' 凭据加密持久化并可解密')
                    request('GET',f'/deliveries/{item["id"]}/credentials/{cred["id"]}/reveal',who='pm',expected=403)
                    check(request('GET',f'/deliveries/{item["id"]}/credentials/{cred["id"]}/reveal',who='owner')['password']==secret,db_type+' 凭据授权查看')
            d=action(kind,item,'ops1')
            check(d['status']=='completed' and not pending(d),kind+' 强制执行回填并完成')
            report['cases'].append({'kind':kind,'id':item['id'],'round':d['rounds'][-1]['round'],'version_id':d['rounds'][-1]['version_id']})
        # Rejection closes all sibling/future tasks, only applicant can resubmit.
        f={'steps':[node('会签',ids=[users['dev1']['id'],users['dev2']['id']]),final()]}
        publish('resource',f)
        item,body=create()
        stale=pending(detail('resource',item),users['dev2']['id'])[0]
        d=action('resource',item,'dev1','reject')
        check(d['status']=='rejected' and not pending(d),'驳回关闭所有待办')
        action('resource',item,'dev2',task=stale,expected=409)
        request('POST',f'/resource/{item["id"]}/resubmit',body,'pm',403)
        changed={'steps':[node('新版本项目经理',source='project_role',role='project_manager'),final()]}
        v=publish('resource',changed)
        d=request('POST',f'/resource/{item["id"]}/resubmit',{**body,'purpose':'补充容量后重提'},'owner')
        check(len(d['rounds'])==2 and d['rounds'][0]['status']=='rejected' and d['rounds'][1]['version_id']==v['id'],'重提保留历史且使用最新版本从头开始')
        other,_=create(who='owner2',pid=p2['id'])
        check(pending(detail('resource',item))[0]['assignee_id']==users['pm']['id'] and pending(detail('resource',other))[0]['assignee_id']==users['pm2']['id'],'不同项目匹配各自岗位处理人')
        # Published flow and membership changes cannot affect an old snapshot.
        old,_=create()
        old_task=pending(detail('resource',old))[0]
        request('PUT',f'/projects/{p1["id"]}/members',{'members':[m for m in p1['members'] if m['role']!='project_manager']+[{'user_id':users['pm2']['id'],'role':'project_manager'}]})
        publish('resource',{'steps':[node('改为研发',ids=[users['dev1']['id']]),final()]})
        check(pending(detail('resource',old))[0]['assignee_id']==old_task['assignee_id'],'新版发布及项目成员变化不改变在途处理人快照')
        action('resource',old,'pm')
        # Role revoked and disabled accounts require explicit reassignment.
        publish('resource',{'steps':[node('运维预审',source='department_role',role='department_head'),final()]})
        a,_=create();b,_=create(who='owner2',pid=p2['id'])
        check(pending(detail('resource',a))[0]['assignee_id']==users['dept1']['id'] and pending(detail('resource',b))[0]['assignee_id']==users['dept2']['id'],'不同申请部门匹配不同负责人')
        request('PATCH',f'/users/{users["dept1"]["id"]}',{'roles':['applicant']})
        action('resource',a,'dept1',task=pending(detail('resource',a))[0],expected=403)
        request('PATCH',f'/users/{users["dept1"]["id"]}',{'roles':['department_head']})
        request('PATCH',f'/users/{users["dept1"]["id"]}',{'active':False})
        action('resource',a,'dept1',task=pending(detail('resource',a))[0],expected=401)
        check(len(pending(detail('resource',a)))==1,'撤销角色及停用不自动跳过任务')
        # Fixed-role task transfer uses a second eligible operator.
        publish('resource',{'steps':[final()]})
        item,_=create()
        request('PATCH',f'/users/{users["ops1"]["id"]}',{'active':False})
        reassign(item,'ops2')
        d=action('resource',item,'ops2')
        check(d['status']=='completed' and len(d['transfers'])==1,'停用处理人后逐个转交且保留转交原因与历史')
        request('PATCH',f'/users/{users["ops1"]["id"]}',{'active':True})
        # Project-specific mapping and first matching branch/default semantics.
        publish('resource',{'steps':[node('项目经理',source='project_role',role='project_manager'),final()]})
        x,_=create();y,_=create(who='owner2',pid=p2['id'])
        check(pending(detail('resource',x))[0]['assignee_id']==users['pm2']['id'] and pending(detail('resource',y))[0]['assignee_id']==users['pm2']['id'],'项目成员变更只影响新申请')
        request('POST','/resources',payload('resource',p2['id']),'owner',403)
        request('GET',f'/resources/{x["id"]}',who='outsider',expected=404)
        request('GET','/admin/settings',who='pm',expected=403)
        request('GET','/audit',who='pm',expected=403)
        request('GET','/deliveries/1/credentials/1/reveal',who='pm',expected=403)
        check(True,'跨项目申请、非项目查看和新增业务角色的配置/审计/凭据权限隔离')
        # Missing role and self-only resolution fail atomically.
        publish('resource',{'steps':[node('无成员岗位',source='project_role',role='sales_head'),final()]})
        with SessionLocal() as db: before=db.scalar(select(func.count()).select_from(ResourceRequest))
        request('POST','/resources',payload('resource',p1['id']),'owner',409)
        with SessionLocal() as db: check(db.scalar(select(func.count()).select_from(ResourceRequest))==before,'未匹配合格成员阻止提交且不产生半张单据')
        publish('resource',{'steps':[node('自审',ids=[users['owner']['id']]),final()]})
        request('POST','/resources',payload('resource',p1['id']),'owner',409)
        check(True,'只有申请人作为审批人时阻止提交')
        # Multiple branch priorities, any conditions and fallback.
        branch={'id':'branch','name':'优先级分支','type':'branch','branches':[
            {'id':'first','name':'优先路径','match':'any','conditions':[{'field':'environment','op':'eq','value':'生产'},{'field':'resource_type','op':'not_in','value':['ECS']}],'steps':[node('firstnode',ids=[users['dev1']['id']])]},
            {'id':'second','name':'次级路径','match':'all','conditions':[{'field':'environment','op':'eq','value':'生产'}],'steps':[node('secondnode',ids=[users['dev2']['id']])]},
            {'id':'default','name':'默认路径','default':True,'steps':[]}]}
        f={'steps':[branch,final()]};publish('resource',f)
        for env,expected in [('生产','first'),('测试','default')]:
            p=request('POST','/admin/flows/resource/preview',{'definition':f,'project_id':p1['id'],'applicant_id':users['owner']['id'],'data':{'environment':env,'resource_type':'ECS'}})
            check(p['branches'][0]['matched']==expected,'条件首次匹配/默认路径 '+env)
        request('PUT','/admin/workflows',{},expected=422)
        request('PUT','/admin/workflows',{'object_type':'resource','stage':'operator','assignee_id':users['ops1']['id']},expected=410)
        check(True,'旧审批人写接口停止使用')
        # Real MySQL race: two any-sign actions share the same business-row lock.
        publish('resource',{'steps':[node('并发或签',ids=[users['dev1']['id'],users['dev2']['id']],mode='any'),final()]})
        concurrent,_=create()
        current=pending(detail('resource',concurrent))
        if engine.dialect.name=='mysql':
            def vote(pair):
                who,task=pair
                return c.post(f'/api/resource/{concurrent["id"]}/action',headers=headers[who],json={'task_id':task['id'],'action':'approve'}).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses=list(pool.map(vote,[(who,next(t for t in current if t['assignee_id']==users[who]['id'])) for who in ['dev1','dev2']]))
            check(sorted(statuses)==[200,409],'真实 MySQL 并发或签只推进一次')
            d=detail('resource',concurrent)
            check(len(pending(d))==1 and pending(d)[0]['assignee_id']==users['ops1']['id'],'并发后只产生一个末节点任务')
        else:
            action('resource',concurrent,'dev1')
            action('resource',concurrent,'dev2',task=current[1],expected=409)
            report['mysql_concurrency']='未执行；SQLite 不支持 FOR UPDATE'
        # Legacy startup migration is idempotent and preserves original task IDs.
        from app.flow_engine import seed_v3
        from app.main import OBJECTS
        with SessionLocal() as db:
            old_history=[(a.id,a.action,a.actor_id) for a in db.scalars(select(Approval).where(Approval.object_type=='resource',Approval.object_id==1)).all()]
            inst_count=db.scalar(select(func.count()).select_from(WorkflowInstance))
            seed_v3(db,OBJECTS)
            check(old_history==[(a.id,a.action,a.actor_id) for a in db.scalars(select(Approval).where(Approval.object_type=='resource',Approval.object_id==1)).all()] and inst_count==db.scalar(select(func.count()).select_from(WorkflowInstance)),'旧库迁移重复运行保留审批记录及在途进度')
            report['counts']={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [WorkflowVersion,WorkflowInstance,WorkflowNode,Approval,NotificationLog,AuditLog,TaskTransfer]}
            if engine.dialect.name=='mysql':
                report['mysql_version'],report['database_name']=db.execute(text('SELECT VERSION(), DATABASE()')).one()
            report['tables']=inspect(engine).get_table_names()
        check(report['counts']['audit_logs']>0 and report['counts']['notification_logs']>0,'审批、通知和审计记录实际持久化')
        report['passed']=True
    finally:
        for kind,original in backups.items():
            try:
                publish(kind,original['versions'][0]['definition'])
                t=request('GET',f'/admin/flows/{kind}')
                request('PUT',f'/admin/flows/{kind}/draft',{'definition':original['draft'],'revision':t['revision']})
            except Exception as exc:
                report['passed']=False
                report.setdefault('restore_errors',[]).append(str(exc))
        target=Path(args.report);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False,indent=2))
