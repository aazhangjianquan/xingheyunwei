"""Offline Kustomize/SQL/production configuration checks; never applies to a cluster."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

try:
    import yaml
except ImportError:
    raise SystemExit('请在当前 Python 环境安装 PyYAML 后重试') from None

ROOT = Path(__file__).resolve().parents[1]


def validate(ready=False):
    rendered = subprocess.run(['kubectl', 'kustomize', str(ROOT)], capture_output=True, text=True, check=True).stdout
    documents = [doc for doc in yaml.safe_load_all(rendered) if doc]
    optional = list(yaml.safe_load_all((ROOT/'optional/mysql.yaml').read_text()))
    schema_checked = False
    try:
        from kubernetes_validate import validate as validate_schema
    except ImportError:
        pass
    else:
        for doc in documents + optional:
            validate_schema(doc, desired_version='1.31.0', strict=True)
        schema_checked = True
    indexed = {(doc['kind'], doc['metadata']['name']): doc for doc in documents}
    maps = {name: doc['data'] for (kind, name), doc in indexed.items() if kind=='ConfigMap'}
    config = next(data for data in maps.values() if 'SEED_DEMO_DATA' in data)
    assert config['SEED_DEMO_DATA']=='false'
    assert config['REQUIRE_SHARED_KEYS']=='true'
    assert config['DATABASE_CONFIG_MODE']=='environment'
    assert config['ATTACHMENT_STORAGE']=='minio'
    for variable in ('TRUSTED_PROXY_CIDRS', 'INGRESS_PROXY_CIDR'):
        assert config[variable] and '0.0.0.0/0' not in config[variable]
    for name in ('workspace-api','workspace-worker','workspace-reminder-scheduler'):
        pod = indexed[('Deployment',name)]['spec']['template']['spec']
        container = pod['containers'][0]
        assert pod['securityContext']['runAsNonRoot'] is True
        assert any(source.get('secretRef',{}).get('name')=='workspace-secrets' for source in container['envFrom'])
    frontend = indexed[('Deployment','workspace-frontend')]['spec']['template']['spec']
    assert not any('secretRef' in source for source in frontend['containers'][0]['envFrom'])
    for (kind, _), doc in indexed.items():
        if kind != 'Namespace': assert doc['metadata']['namespace']=='project-workspace'
        if kind not in {'Deployment','StatefulSet'}: continue
        pod = doc['spec']['template']['spec']
        for volume in pod.get('volumes',[]):
            if 'configMap' in volume: assert volume['configMap']['name'] in maps
        for container in pod['containers']:
            for source in container.get('envFrom',[]):
                if 'configMapRef' in source: assert source['configMapRef']['name'] in maps
    sql = (ROOT/'v1.0.0.sql').read_text()
    assert not re.search(r'^\s*(INSERT|REPLACE|UPDATE|DELETE|TRUNCATE|DROP)\b', sql, re.I|re.M)
    tables = re.findall(r'^CREATE TABLE IF NOT EXISTS\s+`?(\w+)', sql, re.M)
    assert len(tables)==len(set(tables))==31
    if ready:
        assert 'example.com' not in rendered, '请替换镜像和 Ingress 域名占位值'
        secret_path = ROOT/'.private/workspace-secrets.yaml'
        assert secret_path.exists(), '请先生成私有 Secret'
        secret = yaml.safe_load(secret_path.read_text())
        for variable in ('DATABASE_URL','APP_SECRET','FERNET_KEY','REDIS_PASSWORD','MINIO_ACCESS_KEY','MINIO_SECRET_KEY'):
            assert secret['stringData'].get(variable), 'Secret 缺少 ' + variable
        assert secret_path.stat().st_mode & 0o077 == 0, 'Secret 文件权限需为 600'
    report = {'passed':True, 'rendered_resources':len(documents),'optional_resources':len(optional),
              'kubernetes_1_31_schema_checked':schema_checked,'schema_tables':len(tables),
              'sql_has_no_data_or_credentials':True, 'production_bootstrap_enabled':True,
              'shared_secret_references_checked':True,'cluster_was_not_modified':True,
              'operator_values_checked':ready}
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ready', action='store_true', help='同时拒绝仓库/域名占位值，并检查本地 Secret')
    arguments = parser.parse_args()
    try:
        validate(arguments.ready)
    except (AssertionError, subprocess.CalledProcessError, ValueError) as error:
        raise SystemExit('校验失败：' + (str(error) or '请检查清单中的引用与配置')) from None
