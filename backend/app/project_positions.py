"""Project appointments are independent of platform roles and department jobs."""
import json
from sqlalchemy import select
from sqlalchemy.orm import object_session
from .models import Project, ProjectMember, User
from .roles import canonical_role

POSITIONS = {
    'sales': '销售', 'presales': '售前', 'project_manager': '项目经理',
    'applicant': '申请人', 'technical_lead': '技术负责人',
    'agent_developer': '智能体开发', 'ops_head': '运维负责人',
    'operator': '运维人员', 'tester': '测试人员',
}


def migrated_positions(role):
    role = canonical_role(role)
    if role == 'sales_presales':
        return ('sales', 'presales')
    if role == 'department_head':
        return ('technical_lead',)
    return (role,) if role in POSITIONS else ()


def catalog():
    return [{'id': key, 'name': name} for key, name in POSITIONS.items()]


def appointed(user, project_id, *positions):
    """Use the caller's current SQLAlchemy session, including uncommitted changes."""
    db = object_session(user) if user else None
    if not user or not user.active or not db or not project_id:
        return False
    project = db.get(Project, project_id)
    if not project:
        return False
    return any(not positions or set(migrated_positions(m.role)) & set(positions) for m in db.scalars(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)))


def policy_eligible(user, policy):
    if not user or not user.active or user.id == policy.get('owner_id'):
        return False
    if policy.get('source') == 'project_role':
        return appointed(user, policy.get('project_id'), policy.get('role'))
    return policy.get('source') == 'fixed'


def task_policy(task):
    db = object_session(task)
    if db and task.node_run_id:
        from .models import WorkflowNode
        node = db.get(WorkflowNode, task.node_run_id)
        if node and node.assignment_policy:
            return json.loads(node.assignment_policy)
    return None
