"""Production bootstrap and release artifacts; isolated data, no external services."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base, get_db
from app.models import ChangeRequest, DeliveryProject, LicenseRequest, NotificationLog, Project, ResourceRequest, User, WikiArticle
from app.rate_limit import RateDecision
from app.security import make_token, verify_password

ROOT = Path(__file__).resolve().parents[2]


class ProductionBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///'+self.folder.name+'/production.db')
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.environment = patch.dict(os.environ, {'SEED_DEMO_DATA':'false', 'BOOTSTRAP_ADMIN_USERNAME':'production-admin',
            'BOOTSTRAP_ADMIN_PASSWORD':'StrongInitial@2026', 'BOOTSTRAP_ADMIN_NAME':'平台负责人'})
        self.patches = [self.environment, patch('app.main.engine',self.engine), patch('app.main.SessionLocal',self.sessions),
                        patch('app.flow_api.seed_demo',side_effect=AssertionError('禁止执行演示初始化'))]
        for item in self.patches: item.start()

    def tearDown(self):
        main.app.dependency_overrides.pop(get_db,None)
        for item in reversed(self.patches): item.stop()
        self.engine.dispose(); self.folder.cleanup()

    def test_initialization_only_creates_configured_admin_and_no_demo_business(self):
        main.initialize_database(); main.initialize_database()
        with self.sessions() as db:
            users = list(db.scalars(select(User)))
            self.assertEqual(len(users),1)
            admin = users[0]
            self.assertEqual((admin.username,admin.display_name,admin.role),('production-admin','平台负责人','admin'))
            self.assertTrue(verify_password('StrongInitial@2026',admin.password_hash))
            for model in (Project,ResourceRequest,ChangeRequest,DeliveryProject,LicenseRequest,WikiArticle,NotificationLog):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)),0,model.__name__)
            original_hash = admin.password_hash
        os.environ['BOOTSTRAP_ADMIN_PASSWORD']='ChangedEnvironment@2026'
        main.initialize_database()
        with self.sessions() as db:self.assertEqual(db.scalar(select(User)).password_hash,original_hash)

    def test_weak_or_missing_bootstrap_password_never_creates_default_accounts(self):
        os.environ['BOOTSTRAP_ADMIN_PASSWORD']='weak'
        with self.assertRaisesRegex(RuntimeError,'至少 12 位'):main.initialize_database()
        with self.sessions() as db:self.assertEqual(db.scalar(select(func.count()).select_from(User)),0)

    def test_environment_managed_database_is_readonly_and_sql_files_are_discovered(self):
        main.initialize_database()
        with self.sessions() as db: uid=db.scalar(select(User.id))
        def dependency():
            with self.sessions() as db:yield db
        main.app.dependency_overrides[get_db]=dependency
        with patch.dict(os.environ,{'DATABASE_CONFIG_MODE':'environment','DATABASE_URL':str(self.engine.url),'SQL_DIR':str(ROOT/'k8s-deploy')}), \
             patch('app.admin.engine',self.engine), patch('app.main.check_request_rate',return_value=RateDecision()):
            client=TestClient(main.app)
            headers={'Authorization':'Bearer '+make_token(uid)}
            try:
                info=client.get('/api/admin/database',headers=headers).json()
                self.assertTrue(info['managed_by_environment'])
                self.assertIn('v1.0.0.sql',info['sql_files'])
                result=client.put('/api/admin/database',headers=headers,json={'host':'other','username':'other'})
                self.assertEqual(result.status_code,409)
                self.assertIn('Secret',result.json()['detail'])
                self.assertEqual(client.get('/api/admin/sql/v1.0.0.sql',headers=headers).status_code,200)
            finally:client.close()


class KubernetesArtifactTests(unittest.TestCase):
    def test_schema_covers_every_current_model_without_data_writes(self):
        sql=(ROOT/'k8s-deploy/v1.0.0.sql').read_text()
        tables=set(re.findall(r'^CREATE TABLE IF NOT EXISTS\s+`?(\w+)',sql,re.M))
        self.assertEqual(tables,set(Base.metadata.tables))
        self.assertIsNone(re.search(r'^\s*(INSERT|REPLACE|UPDATE|DELETE|TRUNCATE|DROP)\b',sql,re.I|re.M))
        self.assertNotIn('Demo@2026',sql)

    def test_production_keys_are_required_and_database_environment_overrides_local_file(self):
        with tempfile.TemporaryDirectory() as folder:
            environment={**os.environ,'INSTANCE_DIR':folder,'REQUIRE_SHARED_KEYS':'true','PYTHONPATH':str(ROOT/'backend')}
            environment.pop('APP_SECRET',None); environment.pop('FERNET_KEY',None)
            failed=subprocess.run([sys.executable,'-c','from app.config import KEYS'],env=environment,capture_output=True,text=True)
            self.assertNotEqual(failed.returncode,0)
            self.assertFalse(Path(folder,'keys.json').exists())
            environment.update(APP_SECRET='a'*48, FERNET_KEY=Fernet.generate_key().decode(),
                               DATABASE_CONFIG_MODE='environment',DATABASE_URL='sqlite:///from-environment.db')
            source='''from app.config import CONNECTION_FILE,database_url,save_database_url
CONNECTION_FILE.write_text('{"url_ciphertext":"invalid-local-file"}')
assert database_url()=='sqlite:///from-environment.db'
try:save_database_url('sqlite:///wrong.db')
except RuntimeError:pass
else:raise AssertionError('不应允许覆盖环境数据库配置')
'''
            subprocess.run([sys.executable,'-c',source],env=environment,capture_output=True,check=True)

    def test_secret_generator_preserves_original_keys_for_migration(self):
        spec=importlib.util.spec_from_file_location('prepare_k8s_secret',ROOT/'k8s-deploy/scripts/prepare_secrets.py')
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        inputs={'DATABASE_URL':'mysql+pymysql://user:encoded-password@db.internal/yunweishenji',
                'MINIO_ACCESS_KEY':'unit-test-access','MINIO_SECRET_KEY':'unit-test-secret'}
        with self.assertRaises(ValueError):module.prepare({**inputs,'SEED_DEMO_DATA':'true'},'new')
        with self.assertRaises(ValueError):module.prepare(inputs,'migrate')
        original={'app_secret':'original-signing-key-'+'a'*40,'fernet_key':Fernet.generate_key().decode()}
        result=module.prepare(inputs,'migrate',original)['stringData']
        self.assertEqual(result['APP_SECRET'],original['app_secret'])
        self.assertEqual(result['FERNET_KEY'],original['fernet_key'])
        self.assertGreaterEqual(len(result['BOOTSTRAP_ADMIN_PASSWORD']),12)
