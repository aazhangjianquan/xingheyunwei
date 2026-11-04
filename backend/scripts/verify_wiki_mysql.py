"""Verify both upgrade paths in disposable MySQL schemas, never in the live DB."""
import argparse
import json
from pathlib import Path
import secrets
import re
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app import main
from app.database import Base, engine as live_engine, get_db
from app.models import User, WikiArticle, WikiArticleVersion, WikiSpace, WikiSpaceMember
from app.migrations import upgrade, finish_upgrade, seed_knowledge
from app.rate_limit import RateDecision
from app.security import make_token


def old_schema(engine):
    metadata=MetaData()
    for name, model in Base.metadata.tables.items():
        if name == 'wiki_space_members': continue
        omitted={'wiki_spaces': {'kind','personal_user_id','members_initialized'}, 'audit_logs': {'wiki_space_id'}}.get(name,set())
        table=Table(name,metadata)
        for column in model.columns:
            if column.name not in omitted: table.append_column(column._copy())
    metadata.create_all(engine)
    with engine.begin() as conn:
        for uid, role in [(1,'admin'),(2,'operator'),(3,'applicant')]:
            conn.execute(text("INSERT INTO users(id,username,display_name,department,role,password_hash,active,created_at) VALUES(:id,:name,:name,'test',:role,'unused',1,CURRENT_TIMESTAMP)"),{'id':uid,'name':f'isolated{uid}','role':role})
        conn.execute(text("INSERT INTO wiki_spaces(id,`key`,name,description,created_by,owner_id,created_at) VALUES(10,'OPS','旧空间','保留说明',1,1,CURRENT_TIMESTAMP)"))
        conn.execute(text("INSERT INTO wiki_articles(id,title,category,content,author_id,space_id,parent_id,status,version,updated_at) VALUES(20,'旧文章','规范','ORIGINAL_CONTENT',2,10,NULL,'published',1,'2026-01-02 03:04:05')"))


def sql_upgrade(engine, path):
    delimiter=';'; pending=''
    with engine.connect() as conn:
        for line in Path(path).read_text().splitlines():
            if line.strip().startswith('--') or not line.strip(): continue
            if line.upper().startswith('USE '): continue
            if line.upper().startswith('DELIMITER '): delimiter=line.split()[1]; continue
            pending += line+'\n'
            if pending.rstrip().endswith(delimiter):
                statement=pending.rstrip()[:-len(delimiter)].strip();pending=''
                conn.exec_driver_sql(statement)
        assert not pending.strip(), 'Unterminated SQL statement'
        conn.commit()


def http_verify(engine):
    sessions=sessionmaker(engine,expire_on_commit=False,autoflush=False)
    def dependency():
        with sessions() as db: yield db
    main.app.dependency_overrides[get_db]=dependency
    headers={uid:{'Authorization':'Bearer '+make_token(uid)} for uid in [1,2,3]}
    client=TestClient(main.app)
    try:
        with patch('app.main.SessionLocal',sessions), patch('app.main.check_request_rate',return_value=RateDecision()):
            private=client.post('/api/wiki',headers=headers[2],json={'title':'PRIVATE_TITLE','category':'规范','content':'PRIVATE_BODY'})
            assert private.status_code==200,private.text
            private=private.json()
            assert client.get(f'/api/wiki/{private["id"]}',headers=headers[1]).status_code==404
            assert client.get('/api/audit?q=PRIVATE_TITLE',headers=headers[1]).json()['total']==0
            assert client.get('/api/wiki/20',headers=headers[2]).status_code==200
            path='/api/wiki/spaces/10/members/2'
            assert client.patch(path,headers=headers[1],json={'role':'viewer'}).status_code==200
            assert client.put('/api/wiki/20',headers=headers[2],json={'title':'不应成功','category':'规范','content':'unauthorized','space_id':10}).status_code==403
            assert client.delete(path,headers=headers[1]).status_code==200
            assert client.get('/api/wiki/20/versions',headers=headers[2]).status_code==404
            assert client.get('/api/wiki/20',headers=headers[3]).status_code==404
            with sessions() as db:
                seed_knowledge(db);db.commit()
                assert db.get(WikiSpaceMember,(10,2)) is None
                assert db.get(WikiArticle,20).content=='ORIGINAL_CONTENT'
    finally:
        client.close();main.app.dependency_overrides.pop(get_db,None)


def main_check():
    parser=argparse.ArgumentParser();parser.add_argument('--sql',required=True)
    parser.add_argument('--precreated-prefix')
    args=parser.parse_args();report={}
    if args.precreated_prefix: assert re.fullmatch(r'wiki_acl_check_[a-f0-9]+',args.precreated_prefix)
    assert live_engine.dialect.name=='mysql'
    for mode in ['automatic','manual_sql']:
        name=(args.precreated_prefix+'_'+mode) if args.precreated_prefix else 'wiki_acl_check_'+secrets.token_hex(6)
        if not args.precreated_prefix:
            with live_engine.connect() as conn: conn.exec_driver_sql(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4')
        engine=create_engine(live_engine.url.set(database=name))
        try:
            old_schema(engine)
            if mode=='automatic':
                upgrade(engine);Base.metadata.create_all(engine);finish_upgrade(engine)
                with sessionmaker(engine,autoflush=False)() as db: seed_knowledge(db);db.commit()
            else: sql_upgrade(engine,args.sql)
            with sessionmaker(engine)() as db:
                assert db.get(WikiSpaceMember,(10,2)).role=='editor'
                assert db.get(WikiArticle,20).updated_at.isoformat()=='2026-01-02T03:04:05'
                assert len(db.scalars(select(WikiSpace).where(WikiSpace.kind=='personal')).all())==3
            http_verify(engine)
            if mode=='manual_sql':
                sql_upgrade(engine,args.sql)
                with sessionmaker(engine)() as db: assert db.get(WikiSpaceMember,(10,2)) is None
            report[mode]='passed: preserved content/history, personal isolation, member downgrade/removal and restart'
        finally:
            engine.dispose()
            if not args.precreated_prefix:
                with live_engine.connect() as conn: conn.exec_driver_sql(f'DROP DATABASE `{name}`')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main_check()
