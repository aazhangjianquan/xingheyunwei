"""Authorized incremental production upgrade; keep all prior cards, OAuth and keys."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT=Path('/root/xinghe')
RELEASE=Path(__file__).resolve().parent
OLD_API='yunweishenji-api-v1.0.8-feishu-cards-20261109:latest'
OLD_FRONT='yunweishenji-frontend-v1.0.8-feishu-task-20261109:latest'
NEW_API='yunweishenji-api-v1.0.9-wiki-access-20261110:latest'
NEW_FRONT='yunweishenji-frontend-v1.0.9-wiki-access-20261110:latest'
SERVICES=('api','frontend','worker','reminder-scheduler')
CONTAINERS=('xinghe_api','xinghe_notification_worker','xinghe_reminder_scheduler')


def run(args,**kwargs):return subprocess.run(args,cwd=ROOT,check=True,**kwargs)
def output(args,**kwargs):return subprocess.check_output(args,cwd=ROOT,**kwargs)
def compose(*args):return run(['docker-compose',*args])
def api_python(source):return output(['docker','exec','-i','xinghe_api','python','-'],input=source.encode())


SNAPSHOT='''
import hashlib,json
from sqlalchemy import text
from app.database import engine
from app.config import KEY_FILE
result={'tables':{},'key_sha256':hashlib.sha256(KEY_FILE.read_bytes()).hexdigest()}
with engine.connect() as conn:
 names=conn.execute(text('SHOW TABLES')).scalars().all() if specs is None else specs
 for name in names:
  if name in {'audit_logs','oauth_states','login_tickets'}:continue
  assert name.replace('_','').isalnum()
  columns=[r[0] for r in conn.execute(text('SHOW COLUMNS FROM `'+name+'`'))] if specs is None else specs[name]['columns']
  query='SELECT '+','.join('`'+col+'`' for col in columns)+' FROM `'+name+'`'
  rows=sorted(json.dumps(list(row),ensure_ascii=False,default=str,separators=(',',':')) for row in conn.execute(text(query)))
  result['tables'][name]={'columns':columns,'count':len(rows),'sha256':hashlib.sha256('\\n'.join(rows).encode()).hexdigest()}
print(json.dumps(result,ensure_ascii=False))
'''


def snapshot(specs=None):
    return json.loads(api_python('specs='+repr(specs)+'\n'+SNAPSHOT))


def main():
    os.umask(0o077)
    manifest=json.loads((RELEASE/'manifest.json').read_text())
    for name,digest in manifest.items():assert hashlib.sha256((RELEASE/name).read_bytes()).hexdigest()==digest,name
    config=ROOT/'docker-compose.yml';previous=config.read_text()
    assert previous.count(OLD_API)==3 and previous.count(OLD_FRONT)==1
    backup=ROOT/'backups'/('wiki-access-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
    shutil.copy2(config,backup/'docker-compose.yml')
    (backup/'containers.json').write_bytes(output(['docker','inspect',*CONTAINERS,'xinghe_frontend']))
    run(['docker','cp','xinghe_api:/.instance',str(backup/'instance')])
    key_hash=hashlib.sha256((backup/'instance/keys.json').read_bytes()).hexdigest()
    before_modules=json.loads(api_python("import pathlib,hashlib,json; print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('/app/app').glob('*.py')}))"))
    (backup/'module-hashes.json').write_text(json.dumps(before_modules,indent=2))
    print('Backup directory:',backup,flush=True)
    (RELEASE/'Dockerfile.api').write_text('FROM '+OLD_API+'\nCOPY app/ /app/app/\nRUN python -m compileall -q /app/app\n')
    (RELEASE/'Dockerfile.frontend').write_text('FROM '+OLD_FRONT+'\nRUN rm -rf /usr/share/nginx/html/*\nCOPY dist/ /usr/share/nginx/html/\nRUN chmod -R a+rX /usr/share/nginx/html\n')
    run(['docker','build','--network=none','-f',str(RELEASE/'Dockerfile.api'),'-t',NEW_API,str(RELEASE)])
    run(['docker','build','--network=none','-f',str(RELEASE/'Dockerfile.frontend'),'-t',NEW_FRONT,str(RELEASE)])
    replacement=previous.replace(OLD_API,NEW_API).replace(OLD_FRONT,NEW_FRONT)
    candidate=RELEASE/'docker-compose.next.yml';candidate.write_text(replacement)
    # Validate at the original location so relative deployment paths retain their meaning.
    stopped=False
    try:
        compose('stop','frontend','worker','reminder-scheduler');stopped=True
        before=snapshot()
        assert before['key_sha256']==key_hash
        (backup/'before.json').write_text(json.dumps(before,indent=2))
        compose('stop','api')
        with (backup/'database.sql').open('wb') as file:
            run(['docker','exec','xinghe_mysql','sh','-c',
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; export MYSQL_PWD; exec mysqldump -uroot --single-transaction --no-tablespaces --set-gtid-purged=OFF --databases yunweishenji'],stdout=file)
        assert (backup/'database.sql').stat().st_size>1000
        print('Consistent database backup complete; applying additive v1.0.9 SQL',flush=True)
        run(['docker','exec','-i','xinghe_mysql','sh','-c',
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; export MYSQL_PWD; exec mysql -uroot'],input=(RELEASE/'v1.0.9.sql').read_bytes())
        config.write_text(replacement);compose('config','--quiet')
        compose('up','-d','--no-deps','--wait','--wait-timeout','180','api')
        after=snapshot(before['tables'])
        (backup/'after-startup.json').write_text(json.dumps(after,indent=2))
        assert before==after,'Existing data or settings changed unexpectedly'
        scope=json.loads(api_python("from app.database import engine; from sqlalchemy import text; import json; c=engine.connect(); print(json.dumps({'expanded_spaces':c.scalar(text(\"SELECT COUNT(*) FROM wiki_spaces WHERE access_scope <> 'members'\")), 'department_grants':c.scalar(text('SELECT COUNT(*) FROM wiki_space_departments')), 'spaces':c.scalar(text('SELECT COUNT(*) FROM wiki_spaces')), 'direct_members':c.scalar(text('SELECT COUNT(*) FROM wiki_space_members'))}));c.close()"))
        assert scope['expanded_spaces']==0 and scope['department_grants']==0
        new_modules=json.loads(api_python("import pathlib,hashlib,json;print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('/app/app').glob('*.py')}))"))
        changed=sorted(name for name in before_modules if before_modules[name]!=new_modules.get(name))
        assert changed==['audit.py','knowledge.py','migrations.py','models.py','organization.py'],changed
        compose('up','-d','--no-deps','--wait','--wait-timeout','180','frontend','worker','reminder-scheduler')
        for name in CONTAINERS:
            actual=output(['docker','exec',name,'python','-c','from app.config import KEY_FILE; import hashlib;print(hashlib.sha256(KEY_FILE.read_bytes()).hexdigest())'],text=True).strip()
            assert actual==key_hash,name+' master key changed'
            for file in (RELEASE/'app').glob('*.py'):
                assert output(['docker','exec',name,'cat','/app/app/'+file.name])==file.read_bytes(),name+file.name
        run(['docker','exec','xinghe_frontend','nginx','-t'])
        run(['curl','--fail','--silent','--show-error','--max-time','20','http://127.0.0.1/api/health'])
        report={'status':'deployed','backup_directory':str(backup),'release_directory':str(RELEASE),'api_image':NEW_API,'frontend_image':NEW_FRONT,
            'previous_api_image':OLD_API,'previous_frontend_image':OLD_FRONT,'master_keys_preserved':True,'existing_tables_and_settings_preserved':True,
            'existing_direct_member_permissions_preserved':True,'existing_scope_check':scope,'changed_backend_modules':changed,
            'card_and_oauth_modules_unchanged':True,'database_migration':'additive v1.0.9','frontend_manifest':json.loads((RELEASE/'frontend-manifest.json').read_text())}
        (RELEASE/'deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print('\n'+json.dumps(report,ensure_ascii=False),flush=True)
    except Exception:
        config.write_text(previous)
        if stopped:
            print('Restoring previous service images; additive database fields remain compatible',flush=True)
            compose('up','-d','--no-deps','--wait','--wait-timeout','180',*SERVICES)
        raise


if __name__=='__main__':main()
