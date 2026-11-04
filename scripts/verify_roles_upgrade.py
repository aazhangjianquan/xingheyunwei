"""Verify v1.0.1 against local MySQL in a disposable database, never the platform DB."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NAME = 'xinghe_role_upgrade_check_' + str(os.getpid())

def sql(statement):
    result = subprocess.run(['docker','compose','exec','-T','mysql','sh','-c',
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --default-character-set=utf8mb4 -uroot --batch --skip-column-names'],
        input=statement, text=True, capture_output=True, cwd=ROOT)
    if result.returncode:
        raise RuntimeError('Temporary MySQL role migration check failed: ' + result.stderr)
    return result.stdout.strip()

migration=(ROOT/'k8s-deploy/v1.0.1.sql').read_text()
assert migration == (ROOT/'database/v1.0.1.sql').read_text() == (ROOT/'docker-deploy/v1.0.1.sql').read_text()
assert 'USE ' not in migration and 'DROP ' not in migration and 'TRUNCATE ' not in migration
schema=(ROOT/'k8s-deploy/v1.0.0.sql').read_text().replace('`yunweishenji`','`'+NAME+'`')
try:
    sql(schema)
    setup='''
INSERT INTO users (id,username,display_name,department,role,password_hash,active,created_at) VALUES
(1,'owner','既有成员','研发部','project_owner','preserved-password',1,NOW()),
(2,'sales','既有负责人','商务部','sales_head','preserved-password',1,NOW()),
(3,'dev','既有开发负责人','研发部','development_head','preserved-password',1,NOW()),
(4,'leader','既有领导','管理层','leader','preserved-password',1,NOW()),
(5,'director','既有总监','项目部','project_director','preserved-password',1,NOW());
INSERT INTO user_roles VALUES (1,'project_owner'),(1,'project_manager'),(2,'sales_head'),(2,'sales_director'),(3,'development_head'),(4,'leader'),(5,'project_director');
INSERT INTO projects (id,name,department,active,created_by,created_at) VALUES (1,'保留项目','研发部',1,1,NOW());
INSERT INTO project_members VALUES (1,1,'project_owner'),(1,1,'project_manager'),(1,2,'sales_head'),(1,2,'sales_director'),(1,3,'development_head'),(1,5,'project_director');
INSERT INTO workflow_versions (id,kind,version,definition,created_at) VALUES (1,'resource',1,'{"steps":[{"name":"领导","assignee":{"role":"leader"}}]}',NOW());
INSERT INTO workflow_instances (id,object_type,object_id,round,version_id,snapshot,status,created_at) VALUES (1,'resource',1,1,1,'{"roles":["leader"],"history":"原样保留"}','pending',NOW());
INSERT INTO workflow_nodes (id,instance_id,position,name,node_type,mode,config,status) VALUES (1,1,0,'领导','approval','all','{"roles":["leader"]}','pending');
INSERT INTO approvals (id,node_run_id,assignee_snapshot,object_type,object_id,stage,assignee_id,action,comment,created_at) VALUES (1,1,'{"roles":["leader"]}','resource',1,'leader',4,'pending','原意见',NOW());
'''
    sql('USE `'+NAME+'`;'+setup)
    history_query='SELECT definition FROM workflow_versions; SELECT snapshot FROM workflow_instances; SELECT name,config,status FROM workflow_nodes; SELECT stage,assignee_snapshot,comment,action FROM approvals;'
    history=sql('USE `'+NAME+'`;'+history_query)
    expected='1\tproject_manager\n2\tcommercial_head\n3\ttechnical_head\n4\tmanager\n5\tproject_manager_head'
    for _ in range(2):
        sql('USE `'+NAME+'`;'+migration)
        assert sql('USE `'+NAME+'`;SELECT id,role FROM users ORDER BY id;') == expected
        assert sql('USE `'+NAME+'`;SELECT COUNT(*) FROM user_roles;') == '5'
        assert sql('USE `'+NAME+'`;SELECT COUNT(*) FROM project_members;') == '4'
        # Role migration must preserve authorization, not only row counts.
        assert sql('USE `'+NAME+'`;SELECT COUNT(*) FROM project_members p JOIN user_roles r ON r.user_id=p.user_id AND r.role=p.role;') == '4'
        assert sql('USE `'+NAME+'`;SELECT COUNT(*) FROM projects;') == '1'
        assert sql('USE `'+NAME+'`;SELECT COUNT(*) FROM users WHERE password_hash="preserved-password";') == '5'
        assert sql('USE `'+NAME+'`;'+history_query) == history
    sql('USE `'+NAME+'`;DELETE FROM user_roles WHERE user_id=2; DELETE FROM project_members WHERE user_id=2;')
    sql('USE `'+NAME+'`;'+migration)
    assert sql('USE `'+NAME+'`;SELECT COUNT(*) FROM user_roles WHERE user_id=2; SELECT COUNT(*) FROM project_members WHERE user_id=2;') == '0\n0'
    report={'mysql_migration':'passed','repeat_execution':'passed','revoked_grants_stay_revoked':True,
            'history_unchanged':True,'passwords_and_projects_preserved':True,'sql_copies_identical':True}
    (ROOT/'reports/roles-upgrade-verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
finally:
    sql('DROP DATABASE IF EXISTS `'+NAME+'`;')
