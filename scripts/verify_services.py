"""Read-only checks against all actual replicas and the independently built UI."""
import hashlib
from html.parser import HTMLParser
import json
import re
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]


def command(*args, source=None):
    result = subprocess.run(args, input=source, text=True, capture_output=True, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(result.stderr or '子进程验证未通过')
    return result.stdout


def get(url, token=None):
    for attempt in range(2):
        try:
            with urlopen(Request(url, headers={'Authorization': 'Bearer ' + token} if token else {}), timeout=20) as response:
                return response.read()
        except HTTPError as error:
            if error.code != 429 or attempt:
                raise
            time.sleep(min(60, int(error.headers.get('Retry-After', 10))))


class Assets(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.paths = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        path = attrs.get('src') if tag == 'script' else attrs.get('href') if tag == 'link' else None
        if path and path.startswith('/assets/'):
            self.paths.append(path)


def verify():
    replicas = command('docker', 'compose', 'ps', '-q', 'api').split()
    frontends = command('docker', 'compose', 'ps', '-q', 'frontend').split()
    assert len(replicas) >= 2, '多副本验收需要至少两个健康 API 容器'
    assert len(frontends) == 1
    token = None
    results = []
    # Only the parent keeps transient tokens in memory; none go into the report.
    source = '''import hashlib,json,urllib.request
from app.config import KEY_FILE
from app.database import SessionLocal
from app.models import User
from app.security import make_token
from sqlalchemy import select
with SessionLocal() as db:
    user=db.scalar(select(User).where(User.active.is_(True), User.role=='admin'))
    assert user is not None
    token=make_token(user.id)
    print(json.dumps({'keys':hashlib.sha256(KEY_FILE.read_bytes()).hexdigest(),'token':token,'user':user.id}))
'''
    key_hashes = []
    for replica in replicas:
        metadata = json.loads(command('docker', 'exec', '-i', replica, 'python', '-', source=source))
        key_hashes.append(metadata['keys'])
        token = token or metadata['token']
        probe = '''import json,sys,time,urllib.request,urllib.error
from app.database import SessionLocal
from app.models import Approval
from app.security import token_user_id
from sqlalchemy import select,func
token=json.loads(sys.stdin.read())
def read(path):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000'+path,headers={'Authorization':'Bearer '+token})) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code!=429 or attempt==2: raise
            time.sleep(min(60,int(error.headers.get('Retry-After',10))))
me=read('/api/me')
tasks=read('/api/tasks?page_size=10')
with SessionLocal() as db:
    assert all(db.get(Approval,row['id']).assignee_id==me['id'] for row in tasks['items'])
print(json.dumps({'same_session_accepted':True,'inbox_personal':True,'user':me['id']}))
'''
        result = json.loads(command('docker', 'exec', '-i', replica, 'python', '-c', probe, source=json.dumps(token)))
        assert result['user'] == metadata['user']
        results.append({'container':replica[:12], **result})
    assert len(set(key_hashes)) == 1, 'API 副本主密钥不一致'
    time.sleep(10)  # Respect the existing rolling per-user request quota.
    dashboard = json.loads(get('http://127.0.0.1:8080/api/dashboard', token))
    licenses = json.loads(get('http://127.0.0.1:8080/api/licenses', token))
    assert dashboard['license_count'] == licenses['total']
    assert 'license_key' not in json.dumps(dashboard)
    html = get('http://127.0.0.1:8080/dashboard').decode()
    packaged = command('docker','exec',frontends[0],'cat','/usr/share/nginx/html/index.html')
    paths = Assets(html).paths
    assert paths and paths == Assets(packaged).paths
    scripts = []
    assets = []
    for path in paths:
        served = get('http://127.0.0.1:8080' + path)
        packaged_bytes = subprocess.run(['docker','exec',frontends[0],'cat','/usr/share/nginx/html'+path],capture_output=True,check=True).stdout
        assert served == packaged_bytes, path
        assets.append({'path':path,'sha256':hashlib.sha256(served).hexdigest()})
        if path.endswith('.js'): scripts.append(served.decode())
    script = '\n'.join(scripts)
    for text in ['PROJECT WORKSPACE','统一工作台','License 申请','license_count']:
        assert text in script, text
    assert 'OPS WORKSPACE' not in script
    assert '项目协作空间' not in script
    for _ in range(12):
        get('http://127.0.0.1:8080/api/health')
    gateway_log = command('docker','compose','logs','--no-color','--tail','100','frontend')
    upstreams = sorted(set(re.findall(r'upstream=(172\.29\.90\.\d+:8000)', gateway_log)))
    assert len(upstreams) >= 2, '健康请求没有实际分发至至少两个后端副本'
    log = command('docker','compose','logs','--no-color','--tail','250','api')
    assert 'Traceback' not in log
    report = {'passed': True, 'api_replicas':len(replicas), 'replicas': results,
              'shared_keys':True,'load_balanced_upstreams':upstreams,'dashboard_license_count':dashboard['license_count'],
              'license_count_matches_list': True,'frontend_assets_match_image':True, 'assets':assets}
    output = ROOT/'reports/services-verification.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    verify()
