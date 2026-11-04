"""Exercise a V2 schema upgrade with an in-flight record; never touch live DB."""
import unittest
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import Session
from app.database import Base
from app.migrations import upgrade, finish_upgrade
from app.models import User, ResourceRequest, Approval, WorkflowNode, WorkflowInstance, UserRole
from app.flow_engine import seed_v3
from app.workflows import seed_assignments


class WorkflowMigrationTests(unittest.TestCase):
    def test_v2_inflight_progress_survives_upgrade(self):
        from app.main import OBJECTS
        engine=create_engine('sqlite:///:memory:')
        # Build the former layout by excluding only the additive V3 columns.
        old_tables={'users','resource_requests','change_requests','delivery_projects','delivery_credentials','approvals','workflow_assignments'}
        from sqlalchemy.schema import CreateTable
        from sqlalchemy import MetaData, Table
        metadata=MetaData()
        for name in old_tables:
            model=Base.metadata.tables[name]
            table=Table(name,metadata)
            for col in model.columns:
                if col.name not in {'project_id','node_run_id','assignee_snapshot'} or name=='delivery_credentials':
                    if name=='change_requests' and col.name=='environment': continue
                    table.append_column(col._copy())
        metadata.create_all(engine)
        with engine.begin() as conn:
            for uid,role in [(1,'applicant'),(2,'department_head'),(3,'ops_head'),(4,'leader'),(5,'operator')]:
                conn.execute(text("INSERT INTO users(id,username,display_name,department,role,password_hash,active,created_at) VALUES (:id,:name,:name,'研发部',:role,'existing-hash',1,CURRENT_TIMESTAMP)"),{'id':uid,'name':'old'+str(uid),'role':role})
            conn.execute(text("INSERT INTO resource_requests(id,code,project_name,applicant_id,environment,resource_type,specification,start_date,end_date,purpose,capacity_basis,ports,whitelist,fulfillment,status,stage,created_at,updated_at) VALUES (1,'OLD-001','旧文本项目',1,'生产','ECS','2C4G','2026-01-01','2027-01-01','test','test','','','','in_progress','leader',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            for id_,stage,actor,action in [(10,'department_head',2,'approve'),(11,'ops_head',3,'approve'),(12,'leader',None,'pending')]:
                conn.execute(text("INSERT INTO approvals(id,object_type,object_id,stage,actor_id,assignee_id,action,comment,created_at) VALUES (:id,'resource',1,:stage,:actor,:assignee,:action,'保留意见',CURRENT_TIMESTAMP)"),{'id':id_,'stage':stage,'actor':actor,'assignee':actor or 4,'action':action})
        upgrade(engine); upgrade(engine)
        Base.metadata.create_all(engine); finish_upgrade(engine)
        with Session(engine) as db:
            seed_assignments(db); seed_v3(db,OBJECTS); seed_v3(db,OBJECTS)
            item=db.get(ResourceRequest,1)
            self.assertIsNone(item.project_id)
            self.assertEqual((item.stage,item.status,item.project_name),('leader','in_progress','旧文本项目'))
            self.assertEqual([(a.id,a.action,a.actor_id,a.comment) for a in db.scalars(select(Approval).order_by(Approval.id))],[(10,'approve',2,'保留意见'),(11,'approve',3,'保留意见'),(12,'pending',None,'保留意见')])
            inst=db.scalars(select(WorkflowInstance)).all()
            self.assertEqual(len(inst),1)
            self.assertEqual([n.status for n in db.scalars(select(WorkflowNode).order_by(WorkflowNode.position))],['completed','completed','pending','waiting'])
            self.assertEqual(len(db.scalars(select(UserRole)).all()),5)
        engine.dispose()


if __name__=='__main__': unittest.main()
