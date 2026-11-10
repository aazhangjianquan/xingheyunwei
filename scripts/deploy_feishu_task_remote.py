"""Run on the authorized production host; preserves existing Compose settings and keys."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path('/root/xinghe')
RELEASE = Path(__file__).resolve().parent
COMPOSE = ROOT / 'docker-compose.yml'
API_IMAGE = 'yunweishenji-api-v1.0.8-feishu-task-20261109:latest'
FRONTEND_IMAGE = 'yunweishenji-frontend-v1.0.8-feishu-task-20261109:latest'


def run(args, **kwargs):
    return subprocess.run(args, check=True, cwd=ROOT, **kwargs)


def output(args, **kwargs):
    return subprocess.check_output(args, cwd=ROOT, **kwargs)


def compose(*args):
    return run(['docker-compose', *args])


def api_python(source):
    return output(['docker', 'exec', '-i', 'xinghe_api', 'python', '-'], input=source.encode())


SNAPSHOT = '''
import hashlib,json
from sqlalchemy import text
from app.database import engine
from app.config import KEY_FILE
report={'tables':{},'key_sha256':hashlib.sha256(KEY_FILE.read_bytes()).hexdigest()}
with engine.connect() as c:
 for name in c.execute(text('SHOW TABLES')).scalars():
  if name in {'audit_logs','oauth_states','login_tickets','system_settings'}: continue
  rows=[json.dumps(list(r),ensure_ascii=False,default=str,separators=(',',':')) for r in c.execute(text('SELECT * FROM `'+name+'`'))]
  rows.sort()
  report['tables'][name]={'count':len(rows),'sha256':hashlib.sha256('\\n'.join(rows).encode()).hexdigest()}
 report['settings']={r[0]:list(r[1:]) for r in c.execute(text('SELECT `key`, value, is_secret, updated_by FROM system_settings'))}
print(json.dumps(report,ensure_ascii=False))
'''


def snapshot(path):
    data = json.loads(api_python(SNAPSHOT))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    path.chmod(0o600)
    return data


def key_check():
    hashes = {}
    for name in ['xinghe_api', 'xinghe_notification_worker', 'xinghe_reminder_scheduler']:
        hashes[name] = output(['docker', 'exec', name, 'python', '-c',
            'from app.config import KEY_FILE; import hashlib; print(hashlib.sha256(KEY_FILE.read_bytes()).hexdigest())'], text=True).strip()
    assert len(set(hashes.values())) == 1, 'Service encryption keys differ'
    return next(iter(hashes.values()))


def main():
    os.umask(0o077)
    assert ROOT.is_dir() and COMPOSE.is_file()
    assert RELEASE.parent == ROOT / 'releases'
    run(['tar', '-xzf', str(RELEASE / 'feishu-task-update.tar.gz'), '-C', str(RELEASE)])
    manifest = json.loads((RELEASE / 'manifest.json').read_text())
    for name, expected in manifest.items():
        assert hashlib.sha256((RELEASE / name).read_bytes()).hexdigest() == expected, name
    config = json.loads(output(['docker-compose', 'config', '--format', 'json'], stderr=subprocess.DEVNULL))
    assert all(config['services'][s]['image'] == 'yunweishenji-api-v1.0.8:latest'
               for s in ['api', 'worker', 'reminder-scheduler']), 'Unexpected current API image'
    assert config['services']['frontend']['image'] == 'yunweishenji-frontend-v1.0.8:latest'
    old_key = key_check()
    backup = ROOT / 'backups' / ('feishu-task-' + time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    shutil.copy2(COMPOSE, backup / 'docker-compose.yml')
    (backup / 'containers.json').write_bytes(output(['docker', 'inspect', *[
        'xinghe_api', 'xinghe_frontend', 'xinghe_notification_worker', 'xinghe_reminder_scheduler']]))
    run(['docker', 'cp', 'xinghe_api:/.instance', str(backup / 'instance')])
    assert hashlib.sha256((backup / 'instance/keys.json').read_bytes()).hexdigest() == old_key
    shutil.copytree(backup / 'instance', RELEASE / 'runtime-instance', dirs_exist_ok=True)
    print('Backup:', backup, flush=True)
    print('Building API and frontend while existing services remain available', flush=True)
    run(['docker', 'build', '--network=none', '-f', str(RELEASE / 'Dockerfile.api'), '-t', API_IMAGE, str(RELEASE)])
    run(['docker', 'build', '--network=none', '-f', str(RELEASE / 'Dockerfile.frontend'), '-t', FRONTEND_IMAGE, str(RELEASE)])
    # 旧镜像中的运行期主密钥未必持久化；回退镜像显式带入已备份主密钥。
    (RELEASE / 'Dockerfile.rollback').write_text('FROM yunweishenji-api-v1.0.8:latest\nCOPY runtime-instance/ /.instance/\n')
    rollback_image = 'yunweishenji-api-feishu-task-rollback-' + backup.name.rsplit('-', 1)[-1] + ':latest'
    run(['docker', 'build', '--network=none', '-f', str(RELEASE / 'Dockerfile.rollback'), '-t', rollback_image, str(RELEASE)])
    previous = COMPOSE.read_text()
    replacement, api_count = re.subn(r'(?m)^(\s*image:\s*)yunweishenji-api-v1\.0\.8:latest\s*$',
                                     lambda m: m[1] + API_IMAGE, previous)
    replacement, frontend_count = re.subn(r'(?m)^(\s*image:\s*)yunweishenji-frontend-v1\.0\.8:latest\s*$',
                                          lambda m: m[1] + FRONTEND_IMAGE, replacement)
    assert (api_count, frontend_count) == (3, 1), (api_count, frontend_count)
    # 确认除了四处镜像外，现有环境变量、端口、网络和存储声明字节保持不变。
    candidate = RELEASE / 'docker-compose.next.yml'
    candidate.write_text(replacement)
    run(['docker-compose', '-f', str(candidate), 'config', '--quiet'])
    stopped, replacing = False, False
    try:
        print('Pausing writers and creating a consistent database backup', flush=True)
        compose('stop', 'frontend', 'worker', 'reminder-scheduler')
        stopped = True
        before = snapshot(backup / 'before.json')
        compose('stop', 'api')
        with (backup / 'database.sql').open('wb') as file:
            run(['docker', 'exec', 'xinghe_mysql', 'sh', '-c',
                 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; export MYSQL_PWD; exec mysqldump -uroot --single-transaction --no-tablespaces --set-gtid-purged=OFF --databases yunweishenji'], stdout=file)
        assert (backup / 'database.sql').stat().st_size > 1000
        replacing = True
        temporary = COMPOSE.with_suffix('.yml.next')
        temporary.write_text(replacement)
        temporary.chmod(0o600)
        temporary.replace(COMPOSE)
        compose('up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', 'api')
        after = snapshot(backup / 'after-startup.json')
        assert before['tables'] == after['tables'], 'Business data changed unexpectedly during startup'
        assert before['key_sha256'] == after['key_sha256'], 'Master key changed'
        for key, value in before['settings'].items():
            assert after['settings'].get(key) == value, 'Unexpected setting change: ' + key
        assert after['settings']['feishu_task_login_enabled'][0] == 'false'
        compose('up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', 'frontend', 'worker', 'reminder-scheduler')
        assert key_check() == old_key
        run(['docker', 'exec', 'xinghe_frontend', 'nginx', '-t'])
        run(['curl', '--fail', '--silent', '--show-error', '--max-time', '15', 'http://127.0.0.1/api/health'])
        report = {'status': 'deployed_switch_disabled', 'backup_directory': str(backup),
            'compose': str(COMPOSE), 'api_image': API_IMAGE, 'frontend_image': FRONTEND_IMAGE,
            'rollback_api_image': rollback_image, 'business_tables_unchanged': sorted(before['tables']),
            'master_keys_preserved': True, 'existing_settings_preserved': True, 'release_directory': str(RELEASE)}
        (RELEASE / 'deployment.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print('\n' + json.dumps(report, ensure_ascii=False), flush=True)
    except Exception:
        if replacing:
            print('Deployment failed; restoring previous services with preserved keys', flush=True)
            rollback = previous.replace('yunweishenji-api-v1.0.8:latest', rollback_image)
            COMPOSE.write_text(rollback)
            compose('up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', 'api', 'frontend', 'worker', 'reminder-scheduler')
        elif stopped:
            compose('start', 'api', 'frontend', 'worker', 'reminder-scheduler')
        raise


if __name__ == '__main__':
    main()
