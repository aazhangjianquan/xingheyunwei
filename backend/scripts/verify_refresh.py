"""Read-only deployed checks for refresh branding, existing sessions and aliases."""
import argparse
import json
import re
import sys
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from sqlalchemy import select, text
from app.database import SessionLocal, engine
from app.models import User
from app.roles import has_role
from app.security import make_token
from app.settings import public_settings, value

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', default='http://127.0.0.1:8000')
parser.add_argument('--report', default='/app/reports/refresh-verification.json')
parser.add_argument('--token-stdin', action='store_true')
args = parser.parse_args()
report = {'passed': False, 'checks': [], 'database_engine': engine.dialect.name}
base = args.base_url.rstrip('/')

def read(path, token=None, host=None):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    if host: headers['Host'] = host
    with urlopen(Request(base + path, headers=headers), timeout=15) as r:
        return r.read().decode(), r.headers


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


try:
    assert engine.dialect.name == 'mysql', '此部署验收需要真实 MySQL'
    with SessionLocal() as db:
        options = public_settings(db)
        report['site_name'] = options['site_name']
        configured = urlsplit(value(db, 'public_base_url'))
        admin = next((p for p in db.scalars(select(User).where(User.active == True)) if has_role(p, 'admin')), None)
        assert admin, '没有启用的管理员'
        token = sys.stdin.read().strip() if args.token_stdin else make_token(admin.id)
        assert token, '缺少验证会话'
        report['database_name'], report['mysql_version'] = db.execute(text('SELECT DATABASE(), VERSION()')).one()
    for path in ['/projects', '/wiki', '/resources', '/resource/1']:
        for _ in range(2):
            body, headers = read(path)
            assert headers['Cache-Control'] == 'no-store'
            assert '<title>' + escape(options['site_name']) + '</title>' in body
            match = re.search(r'<script type="application/json" id="yunwei-public-settings">(.*?)</script>', body, re.S)
            assert match and json.loads(match.group(1)) == options, '首屏未直接使用数据库名称'
        report['checks'].append(path + ' 连续刷新返回后台名称和公共首屏配置')
    body, headers = read('/api/auth/options')
    assert json.loads(body) == options and headers['Cache-Control'] == 'no-store'
    for _ in range(3):
        me, headers = read('/api/me', token)
        assert json.loads(me)['id'] == admin.id and headers['Cache-Control'] == 'no-store'
        read('/api/roles', token)
        read('/api/projects', token)
    report['checks'].append('同一登录令牌重复恢复会话与读取项目成功')
    if args.token_stdin: report['checks'].append('升级重启前签发的登录令牌在重启后仍有效')
    if configured.hostname in {'localhost', '127.0.0.1'}:
        alias = '127.0.0.1' if configured.hostname == 'localhost' else 'localhost'
        authority = alias + (':' + str(configured.port) if configured.port else '')
        opener = build_opener(NoRedirect)
        request = Request(base + '/projects?tab=members', headers={'Host': authority})
        try:
            opener.open(request, timeout=10)
            raise AssertionError('本机地址别名没有统一到配置入口')
        except HTTPError as result:
            assert result.code == 307
            expected = configured.scheme + '://' + configured.netloc + '/projects?tab=members'
            assert result.headers['Location'] == expected
        read('/api/auth/options', host=authority)
        report['checks'].append('localhost / 127.0.0.1 页面统一到配置入口，保留路径和参数；API 不跳转')
    # Verify the image serves the current compiled session loader.
    body, _ = read('/projects')
    asset = re.search(r'src="(/assets/[^\"]+\.js)"', body)
    assert asset, '找不到前端构建产物'
    js, _ = read(asset.group(1))
    assert 'yunwei-public-settings' in js and 'session-pending' in js, '镜像仍使用旧登录初始化代码'
    assert 'startup-spinner' not in js and '正在加载平台' not in js, '镜像仍显示刷新启动画面'
    report['checks'].append('已部署静默恢复会话的前端产物，刷新无名称启动画面或转圈')
    report['passed'] = True
finally:
    output = Path(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
