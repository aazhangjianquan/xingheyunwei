"""An existing license table gains review fields without losing its encrypted key."""
from pathlib import Path
import tempfile
import unittest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.migrations import upgrade, finish_upgrade
from app.models import LicenseRequest, User
from app.licenses import seed_license_workflow, can_view_key


class LicenseUpgradeTests(unittest.TestCase):
    def test_previous_license_schema_is_upgraded_idempotently_and_old_keys_are_sealed(self):
        with tempfile.TemporaryDirectory(prefix='license-upgrade-') as folder:
            engine=create_engine('sqlite:///'+str(Path(folder)/'upgrade.db'))
            # Exact original column set, before workflow stage/final-approver existed.
            with engine.begin() as conn:
                conn.execute(text('''CREATE TABLE license_requests (
                  id INTEGER PRIMARY KEY, code VARCHAR(40) NOT NULL, applicant_id INTEGER NOT NULL,
                  project_name VARCHAR(150) NOT NULL, customer VARCHAR(180) NOT NULL, product VARCHAR(150) NOT NULL,
                  tenant_id INTEGER NOT NULL, expire_time BIGINT NOT NULL, account_number INTEGER NOT NULL,
                  agent_number INTEGER NOT NULL, purpose TEXT NOT NULL, status VARCHAR(30) NOT NULL,
                  license_key_ciphertext TEXT, license_key_hash VARCHAR(64), generated_by INTEGER,
                  generated_at DATETIME, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)'''))
                conn.execute(text("""INSERT INTO license_requests (id,code,applicant_id,project_name,customer,product,tenant_id,
                  expire_time,account_number,agent_number,purpose,status,license_key_ciphertext,generated_by,created_at,updated_at)
                  VALUES (1,'OLD',1,'原项目','原客户','原产品',1,1893427199999,-1,-1,'原用途','generated',
                  'unchanged-encrypted-key',2,'2026-10-26 00:00:00','2026-10-26 00:00:00')"""))
            upgrade(engine)
            Base.metadata.create_all(engine)
            finish_upgrade(engine)
            upgrade(engine)
            finish_upgrade(engine)
            with sessionmaker(engine,expire_on_commit=False)() as db:
                seed_license_workflow(db);seed_license_workflow(db)
                item=db.get(LicenseRequest,1)
                self.assertIsNone(item.project_id)
                self.assertEqual(item.project_name,'原项目')
                self.assertEqual(item.license_key_ciphertext,'unchanged-encrypted-key')
                self.assertEqual((item.status,item.stage,item.final_approver_id),('pending','awaiting_workflow',None))
                self.assertFalse(can_view_key(item,User(id=1)))
                self.assertFalse(can_view_key(item,User(id=2)))
            columns={c['name'] for c in inspect(engine).get_columns('license_requests')}
            self.assertTrue({'stage','final_approver_id','project_id'} <= columns)
            self.assertIn('ix_license_requests_project_id',{i['name'] for i in inspect(engine).get_indexes('license_requests')})
            self.assertIn('ix_license_requests_final_approver_id',{i['name'] for i in inspect(engine).get_indexes('license_requests')})
            engine.dispose()
