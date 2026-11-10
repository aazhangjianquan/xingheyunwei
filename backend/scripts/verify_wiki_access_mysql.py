"""Run ACL tests and both v1.0.9 upgrade paths in disposable MySQL schemas."""
import json
from pathlib import Path
import secrets
import sys
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine, MetaData, Table, select
from sqlalchemy.orm import Session
from app.database import engine as configured_engine, Base
from app.models import WikiSpace, WikiSpaceMember, User
from app.migrations import upgrade

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_wiki_spaces
from verify_wiki_mysql import sql_upgrade


def main():
    assert configured_engine.dialect.name=='mysql'
    schemas=[]
    def scratch_engine(*args, **kwargs):
        name='wiki_access_check_'+secrets.token_hex(6)
        with configured_engine.connect() as conn: conn.exec_driver_sql(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4')
        schemas.append(name)
        return create_engine(configured_engine.url.set(database=name))
    try:
        with patch.object(test_wiki_spaces,'create_engine',scratch_engine):
            result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(test_wiki_spaces.WikiSpaceTests))
        assert result.wasSuccessful(),'MySQL access regression failed'
        for mode in ['automatic','manual_sql']:
            target=scratch_engine()
            metadata=MetaData()
            for name,model in Base.metadata.tables.items():
                if name=='wiki_space_departments':continue
                table=Table(name,metadata)
                for col in model.columns:
                    if name=='wiki_spaces' and col.name in {'access_scope','include_subdepartments'}:continue
                    table.append_column(col._copy())
            metadata.create_all(target)
            with Session(target) as db:
                owner=User(username='owner',display_name='原负责人',role='applicant',password_hash='unused')
                member=User(username='member',display_name='原成员',role='applicant',password_hash='unused')
                db.add_all([owner,member]);db.flush()
                # ORM model includes new columns, so seed the legacy row with SQL.
                db.connection().exec_driver_sql("INSERT INTO wiki_spaces (`key`,name,description,created_by,owner_id,kind,category,personal_user_id,members_initialized,created_at) VALUES ('LEGACY','原有空间','保留说明',%s,%s,'shared','原分类',NULL,1,CURRENT_TIMESTAMP)",(owner.id,owner.id))
                space_id=db.connection().exec_driver_sql('SELECT id FROM wiki_spaces').scalar_one()
                db.add(WikiSpaceMember(space_id=space_id,user_id=member.id,role='editor',added_by=owner.id));db.commit()
            if mode=='automatic':
                upgrade(target);Base.metadata.create_all(target);upgrade(target)
            else:
                sql_upgrade(target,'/verification/database/v1.0.9.sql');sql_upgrade(target,'/verification/database/v1.0.9.sql')
            with Session(target) as db:
                space=db.scalar(select(WikiSpace))
                assert space.access_scope=='members' and space.include_subdepartments is True
                assert db.scalar(select(WikiSpaceMember)).role=='editor'
                assert space.name=='原有空间' and space.category=='原分类'
            target.dispose()
        # 验证完整安装 SQL 时显式去除库名语句，只在随机临时库建表。
        fresh=scratch_engine()
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(prefix='wiki-install-') as folder:
            schema=Path(folder)/'schema.sql'
            schema.write_text('\n'.join(line for line in Path('/verification/database/schema.sql').read_text().splitlines()
                if not line.upper().startswith(('CREATE DATABASE ', 'USE '))))
            sql_upgrade(fresh,schema);sql_upgrade(fresh,schema)
        from sqlalchemy import inspect
        assert {'access_scope','include_subdepartments'} <= {c['name'] for c in inspect(fresh).get_columns('wiki_spaces')}
        assert 'wiki_space_departments' in inspect(fresh).get_table_names()
        fresh.dispose()
        print(json.dumps({'fresh_install_sql':True, 'database':'MySQL 8.4','access_tests_passed':result.testsRun,'upgrade_paths':['automatic','manual_sql'],'idempotent':True,'legacy_scope':'members','legacy_editor_preserved':True,'production_data_changed':False},ensure_ascii=False))
    finally:
        for name in schemas:
            with configured_engine.connect() as conn:conn.exec_driver_sql(f'DROP DATABASE IF EXISTS `{name}`')


if __name__=='__main__':main()
