"""Read-only deployed HTTP timing; only the platform's ordinary audit rows are written."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
from statistics import median
import sys
from time import perf_counter
from urllib.request import Request, urlopen
from sqlalchemy import func, select, text
from app.database import SessionLocal, engine
from app.models import AuditLog, Project, ProjectMember, ResourceRequest, ChangeRequest, DeliveryProject, WorkflowVersion, User
from app.roles import has_role
from app.security import make_token
from app.settings import public_settings

parser = argparse.ArgumentParser()
parser.add_argument('--phase', choices=['before','after'], required=True)
parser.add_argument('--base-url', default='http://127.0.0.1:8000')
parser.add_argument('--report', required=True)
parser.add_argument('--baseline')
parser.add_argument('--token-stdin', action='store_true')
parser.add_argument('--samples', type=int, choices=range(1,11), default=3)
args = parser.parse_args()
report = {'passed':False, 'phase':args.phase, 'checks':[], 'timing_scope':
          'HTTP 请求链实测；不含 JS/CSS 下载、外部字体、浏览器执行及绘制，不代表完整刷新耗时'}
request_count = 0


def read(path, protected=True):
    headers = {'Authorization':'Bearer ' + token} if protected else {}
    started = perf_counter()
    with urlopen(Request(args.base_url.rstrip('/') + path, headers=headers), timeout=30) as response:
        body = response.read().decode()
        return {'path':path,'ms':(perf_counter()-started)*1000, 'body':body,
                'server_timing':response.headers.get('Server-Timing','')}


def record_audit_count():
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(AuditLog))


try:
    assert engine.dialect.name == 'mysql', '需要在真实 MySQL 容器中运行'
    with SessionLocal() as db:
        options = public_settings(db)
        report['site_name'] = options['site_name']
        report['database_name'], report['mysql_version'] = db.execute(text('SELECT DATABASE(), VERSION()')).one()
        admin = next((p for p in db.scalars(select(User).where(User.active == True)) if has_role(p,'admin')), None)
        assert admin, '没有启用的管理员'
        token = sys.stdin.read().strip() if args.token_stdin else make_token(admin.id)
        assert token, '缺少会话'
        admin_id = admin.id
        report['data_counts'] = {model.__tablename__:db.scalar(select(func.count()).select_from(model))
            for model in [User,Project,ProjectMember,ResourceRequest,ChangeRequest,DeliveryProject,WorkflowVersion]}
    html = read('/projects',False)['body']
    asset = re.search(r'src="(/assets/[^\"]+\.js)"',html)
    assert asset, '找不到编译后的 Vue 页面'
    js = read(asset.group(1),False)['body']
    report['frontend_sha256'] = hashlib.sha256(js.encode()).hexdigest()
    report['remote_fonts'] = bool(re.search(r'fonts\.(?:googleapis|gstatic)\.com',html))
    optimized = not report['remote_fonts'] and '正在加载项目数据…' in js
    report['startup_mode'] = 'identity_first' if optimized else 'legacy_serial'
    if args.phase == 'after':
        assert optimized, '当前容器没有提供优化后的前端页面'
        report['checks'].append('Vue 优化产物已部署，首屏不依赖在线字体')
    before_audits = record_audit_count()
    samples = []
    results = []
    for _ in range(args.samples):
        html_result = read('/projects',False)
        started = perf_counter()
        gate = [read(p) for p in (['/api/me'] if optimized else
                                 ['/api/auth/options','/api/me','/api/roles','/api/projects'])]
        gate_ms = (perf_counter()-started)*1000
        assert next(json.loads(r['body']) for r in gate if r['path']=='/api/me')['id'] == admin_id
        paths = ['/api/projects','/api/roles'] if optimized else ['/api/projects','/api/users','/api/roles']
        with ThreadPoolExecutor(max_workers=3) as pool:
            page = list(pool.map(read,paths))
        data_ms = (perf_counter()-started)*1000
        results.extend([html_result,*gate,*page])
        request_count += len(gate) + len(page)
        samples.append({'html_ms':round(html_result['ms'],2), 'session_gate_ms':round(gate_ms,2),
                        'project_data_ready_ms':round(data_ms,2), 'api_requests':len(gate)+len(page)})
    options_result = read('/api/auth/options',False)
    request_count += 1
    assert json.loads(options_result['body']) == options, '公共配置与数据库不一致'
    after_audits = record_audit_count()
    assert after_audits-before_audits >= request_count, '有 HTTP 操作未留下审计日志'
    report['checks'] += ['原登录会话有效，三轮恢复与项目读取成功','后台平台名称保持一致','全部验收 API 请求均已提交审计日志']
    report['audit_records_added'] = after_audits-before_audits
    report['samples'] = samples
    report['median'] = {key:round(median(s[key] for s in samples),2)
                        for key in ['html_ms','session_gate_ms','project_data_ready_ms','api_requests']}
    report['endpoints'] = {}
    for path in sorted({r['path'] for r in results}):
        group = [r for r in results if r['path']==path]
        item = {'median_ms':round(median(r['ms'] for r in group),2),'samples':len(group)}
        for part in ['app','audit']:
            durations = [float(m.group(1)) for r in group if (m:=re.search(r'\b'+part+r';dur=([\d.]+)',r['server_timing']))]
            if durations: item[part+'_median_ms'] = round(median(durations),2)
        report['endpoints'][path] = item
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text())
        assert baseline['passed'], '升级前检查未完成'
        assert report['site_name']==baseline['site_name'], '后台名称发生变化'
        assert report['database_name']==baseline['database_name'], '数据库发生变化'
        assert all(count>=baseline['data_counts'][table] for table,count in report['data_counts'].items()), '业务记录数量减少'
        report['checks'].append('升级前后使用同一数据库，业务记录与平台名称保留')
        report['comparison'] = {key:{'before':baseline['median'][key],'after':report['median'][key]}
                                for key in report['median']}
        report['comparison']['note'] = '相同数据上的 HTTP 请求链计时；耗时会受机器负载波动影响'
    report['passed'] = True
except Exception as error:
    report['error'] = str(error)
    raise
finally:
    output = Path(args.report)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
