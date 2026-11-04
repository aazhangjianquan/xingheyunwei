from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from .roles import has_role, role_aliases, has_department_role
from .models import Approval, User, WorkflowAssignment

FLOWS = {
    "resource": ["department_head", "ops_head", "manager", "operator"],
    "change": ["ops_head", "manager", "operator"],
    "delivery": ["ops_head", "manager", "operator"],
}


def assignee(db: Session, kind: str, stage: str, owner: User) -> int:
    department = owner.department if stage == "department_head" else ""
    route = db.scalar(select(WorkflowAssignment).where(WorkflowAssignment.object_type == kind, WorkflowAssignment.stage.in_(role_aliases(stage)), WorkflowAssignment.department == department).order_by((WorkflowAssignment.stage == stage).desc()))
    person = db.get(User, route.assignee_id) if route else None
    if not person or not person.active or not has_department_role(person, stage) or (stage == "department_head" and person.department != department):
        raise HTTPException(409, f"审批人未配置或已停用：{kind} / {stage} / {department}，请管理员在系统配置中指定")
    return person.id


def validate_route(db: Session, kind: str, owner: User):
    for stage in FLOWS[kind]:
        assignee(db, kind, stage, owner)


def make_approval(db: Session, kind: str, obj, owner: User):
    item = Approval(object_type=kind, object_id=obj.id, stage=obj.stage, assignee_id=assignee(db, kind, obj.stage, owner))
    db.add(item)
    return item


def seed_assignments(db: Session):
    for kind, stages in FLOWS.items():
        for stage in stages:
            people = [person for person in db.scalars(select(User).where(User.active == True).order_by(User.id)).all() if has_role(person, stage)]
            for person in people:
                department = person.department if stage == "department_head" else ""
                exists = db.scalar(select(WorkflowAssignment).where(WorkflowAssignment.object_type == kind, WorkflowAssignment.stage == stage, WorkflowAssignment.department == department))
                if not exists:
                    db.add(WorkflowAssignment(object_type=kind, stage=stage, department=department, assignee_id=person.id))
                    db.flush()
    db.commit()
