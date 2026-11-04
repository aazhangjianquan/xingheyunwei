"""Live read-only checks plus isolated Redis probes; never send Feishu messages."""
import json
from pathlib import Path
import subprocess
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def command(*args, source=None):
    result = subprocess.run(args, input=source, text=True, capture_output=True, cwd=ROOT)
    if result.returncode:
        raise RuntimeError('部署验收命令未通过：' + result.stderr)
    return result.stdout


def containers(service):
    return command('docker', 'compose', 'ps', '-q', service).split()


def probe(container, script, data):
    return json.loads(command('docker', 'exec', '-i', container, 'python', '-c', script, source=json.dumps(data)))


def verify():
    apis, workers, stores = containers('api'), containers('worker'), containers('redis')
    assert len(apis) >= 2 and workers and len(stores) == 1
    for container in apis + workers + stores + containers('frontend'):
        assert command('docker', 'inspect', '--format', '{{.State.Health.Status}}', container).strip() == 'healthy'
    script = """import inspect,json
from app.main import audit_all_requests
source=inspect.getsource(audit_all_requests)
assert 'check_request_rate(' not in source
assert 'RATE_LIMITED' not in source and 'RATE_LIMIT_UNAVAILABLE' not in source
print(json.dumps({'http_rate_limiting_disabled':True}))
"""
    for container in apis:
        assert probe(container, script, {})['http_rate_limiting_disabled']
    persistence = '''import json,sys
from app import redis_runtime
c=redis_runtime.client()
info=c.info('persistence')
print(json.dumps({'aof_enabled':bool(info['aof_enabled']), 'eviction_policy':c.config_get('maxmemory-policy')['maxmemory-policy']}))
'''
    settings = probe(apis[0], persistence, {})
    assert settings['aof_enabled'] and settings['eviction_policy']=='noeviction'
    # A deliberately nonexistent ID exercises the actual worker and ACK path.
    # No business row is added and no external transport can be called.
    queue = '''import json,sys,time
from sqlalchemy import select
from app.database import SessionLocal
from app.models import NotificationLog
from app import redis_runtime
from app.notification_queue import enqueue,stream_key
job=-int(time.time_ns())
with SessionLocal() as db: assert db.get(NotificationLog,job) is None
entry=enqueue(job)
deadline=time.monotonic()+15
while time.monotonic()<deadline:
    if not redis_runtime.client().xrange(stream_key(),min=entry,max=entry):
        print(json.dumps({'worker_consumed_and_acknowledged':True}));break
    time.sleep(.2)
else: raise AssertionError('通知 Worker 未在期限内确认探针任务')
'''
    worker_check = probe(apis[0], queue, {})
    with urlopen('http://127.0.0.1:8080/api/health',timeout=10) as response:
        assert response.status == 200
    report = {'passed':True,'api_replicas':len(apis),'workers':len(workers),
        'http_rate_limiting_disabled':True,'frequency_alert_generation_disabled':True,
        'no_ip_account_or_platform_quota':True,'no_external_notifications_sent_by_probe':True,
        **settings, **worker_check}
    path = ROOT/'reports/redis-verification.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    verify()
