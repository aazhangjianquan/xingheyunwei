"""Presentation-only notification update on the already authorized production host."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/root/xinghe')
RELEASE = Path(__file__).resolve().parent
OLD = 'yunweishenji-api-v1.0.8-feishu-task-20261109-r2:latest'
NEW = 'yunweishenji-api-v1.0.8-feishu-cards-20261109:latest'


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def output(args, **kwargs):
    return subprocess.check_output(args, cwd=ROOT, **kwargs)


def main():
    os.umask(0o077)
    config = ROOT / 'docker-compose.yml'
    previous = config.read_text()
    assert previous.count(OLD) == 3
    backup = ROOT / 'backups' / ('feishu-progress-cards-' + time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    shutil.copy2(config, backup / 'docker-compose.yml')
    run(['docker', 'cp', 'xinghe_api:/.instance', str(backup / 'instance')])
    run(['docker', 'cp', 'xinghe_api:/app/app/notification_cards.py', str(backup / 'notification_cards.py')])
    key = hashlib.sha256((backup / 'instance/keys.json').read_bytes()).hexdigest()
    (backup / 'containers.json').write_bytes(output(['docker', 'inspect',
        'xinghe_api', 'xinghe_notification_worker', 'xinghe_reminder_scheduler', 'xinghe_frontend']))
    with (backup / 'database.sql').open('wb') as file:
        run(['docker', 'exec', 'xinghe_mysql', 'sh', '-c',
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; export MYSQL_PWD; exec mysqldump -uroot --single-transaction --no-tablespaces --set-gtid-purged=OFF --databases yunweishenji'], stdout=file)
    assert (backup / 'database.sql').stat().st_size > 1000
    print('Backup:', backup, flush=True)
    (RELEASE / 'Dockerfile').write_text('FROM ' + OLD + '\nCOPY app/notification_cards.py /app/app/notification_cards.py\nRUN python -m compileall -q /app/app/notification_cards.py\n')
    run(['docker', 'build', '--network=none', '-f', str(RELEASE / 'Dockerfile'), '-t', NEW, str(RELEASE)])
    try:
        config.write_text(previous.replace(OLD, NEW))
        run(['docker-compose', 'config', '--quiet'])
        run(['docker-compose', 'up', '-d', '--no-deps', '--wait', '--wait-timeout', '180',
             'api', 'worker', 'reminder-scheduler'])
        for name in ['xinghe_api', 'xinghe_notification_worker', 'xinghe_reminder_scheduler']:
            actual = output(['docker', 'exec', name, 'python', '-c',
                'from app.config import KEY_FILE; import hashlib; print(hashlib.sha256(KEY_FILE.read_bytes()).hexdigest())'], text=True).strip()
            assert actual == key, 'Master key differs: ' + name
            source = output(['docker', 'exec', name, 'cat', '/app/app/notification_cards.py'])
            assert source == (RELEASE / 'app/notification_cards.py').read_bytes(), name
        report = {'status': 'deployed', 'backup_directory': str(backup), 'release_directory': str(RELEASE),
                  'image': NEW, 'previous_image': OLD, 'master_keys_preserved': True,
                  'frontend_unchanged': True, 'database_migration': False,
                  'source_sha256': hashlib.sha256((RELEASE / 'app/notification_cards.py').read_bytes()).hexdigest()}
        (RELEASE / 'deployment.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False), flush=True)
    except Exception:
        config.write_text(previous)
        run(['docker-compose', 'up', '-d', '--no-deps', '--wait', '--wait-timeout', '180',
             'api', 'worker', 'reminder-scheduler'])
        raise


if __name__ == '__main__':
    main()
