import json
from fastapi import BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from .database import get_db
from .models import (User, Project, ProjectMember, WorkflowTemplate, WorkflowVersion,
                     WorkflowNode, WorkflowInstance, Approval, TaskTransfer, UserRole, SystemSetting)
from .roles import (ROLES, PROJECT_ROLES, PROJECT_MEMBER_ROLES, role_catalog,
                    role_ids, has_role, canonical_role, has_department_role,
                    retained_snapshot_roles, managed_roles)
from . import flow_engine as flow
from . import audit


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    department: str = Field(min_length=1, max_length=100)
    active: bool = True
    members: list[dict]


class MembersIn(BaseModel):
    members: list[dict]


class DraftIn(BaseModel):
    definition: dict
    revision: int


def register_routes(app, current_user, require_role):
    from .main import user_out, serialize, notify, OBJECTS, detail
    def admin(user=Depends(current_user)):
        require_role(user, 'admin')
        return user

    @app.get('/api/roles')
    def catalog(user=Depends(current_user)):
        return role_catalog()

    def project_out(db, p, user):
        return projects_out(db, [p], user)[0]

    def projects_out(db, rows, user):
        if not rows:
            return []
        by_project = {p.id: [] for p in rows}
        members = db.scalars(select(ProjectMember).where(ProjectMember.project_id.in_(by_project))).all()
        member_ids = {m.user_id for m in members}
        # Hold members and people for the entire response; User.role_grants is
        # select-in loaded once for the batch rather than once per member.
        people = {p.id: p for p in db.scalars(select(User).where(User.id.in_(member_ids)))} if member_ids else {}
        for m in members:
            by_project[m.project_id].append(m)
        result = []
        for p in rows:
            members = by_project[p.id]
            scope = flow.project_manage_roles(p, user, members)
            can_apply = has_role(user, 'admin') or any(m.user_id == user.id and has_role(user, m.role) for m in members)
            # Canonical aliases can collapse two historical positions. Expose a
            # single checkbox for the resulting person/position combination.
            canonical_members = {(m.user_id, canonical_role(m.role)): m for m in members}
            result.append(serialize(p, can_manage=bool(scope), can_apply=can_apply, manageable_roles=sorted(scope),
                members=[{'user_id': m.user_id, 'role': canonical_role(m.role),
                          'name': people[m.user_id].display_name if m.user_id in people else '已删除成员',
                          'eligible': m.user_id in people and people[m.user_id].active and has_role(people[m.user_id], canonical_role(m.role))} for m in canonical_members.values()]))
        return result

    @app.get('/api/projects')
    def projects(db: Session = Depends(get_db), user=Depends(current_user)):
        query = flow.project_scope(select(Project).order_by(Project.id.desc()), user, management=True)
        return projects_out(db, db.scalars(query).all(), user)

    def set_members(db, p, members, actor):
        old = db.scalars(select(ProjectMember).where(ProjectMember.project_id == p.id)).all()
        old_unique = {(m.user_id, canonical_role(m.role)) for m in old}
        scope = flow.project_manage_roles(p, actor, old)
        unique = set()
        for m in members:
            uid, role = m.get('user_id'), canonical_role(m.get('role'))
            if type(uid) is not int or role not in PROJECT_MEMBER_ROLES:
                flow.fail('成员或岗位无效/重复')
            if (uid, role) in unique:
                # A legacy client may submit project_owner and project_manager
                # for the same person; both canonicalize to project_manager.
                continue
            unique.add((uid, role))
        if any(role not in scope for _, role in unique ^ old_unique):
            flow.fail('只能调整本项目可管理的岗位；项目经理由本部门项目经理负责人或管理员指定', 403)
        for uid, role in unique - old_unique:
            person = db.get(User, uid)
            if not person or not person.active or not has_role(person, role):
                flow.fail('新增岗位只能选择已启用且已获授对应角色的成员')
            if not has_role(actor, 'admin') and role in managed_roles(actor) and person.department != actor.department:
                flow.fail('部门负责人只能分配本部门对应岗位的成员', 403)
        if 'project_manager' not in {r for _, r in unique}:
            flow.fail('项目必须指定至少一名项目经理')
        db.execute(delete(ProjectMember).where(ProjectMember.project_id == p.id))
        for uid, role in unique:
            db.add(ProjectMember(project_id=p.id, user_id=uid, role=role))
        db.flush()

    @app.post('/api/projects')
    def create_project(data: ProjectIn, db: Session = Depends(get_db), user=Depends(admin)):
        if db.scalar(select(Project.id).where(Project.name == data.name.strip())):
            flow.fail('项目名称已存在', 409)
        p = Project(name=data.name.strip(), department=data.department, active=data.active, created_by=user.id)
        db.add(p); db.flush()
        set_members(db, p, data.members, user)
        audit.record(db, user, 'create', 'project', p)
        db.commit()
        return project_out(db, p, user)

    @app.put('/api/projects/{project_id}')
    def update_project(project_id: int, data: ProjectIn, db: Session = Depends(get_db), user=Depends(admin)):
        p = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
        if not p: flow.fail('项目不存在', 404)
        if db.scalar(select(Project.id).where(Project.name == data.name.strip(), Project.id != p.id)):
            flow.fail('项目名称已存在', 409)
        before = audit.snapshot(db, 'project', p)
        p.name, p.department, p.active = data.name.strip(), data.department, data.active
        set_members(db, p, data.members, user)
        action = 'disable' if before['active'] and not p.active else 'enable' if not before['active'] and p.active else 'update'
        audit.record(db, user, action, 'project', p, before=before)
        db.commit()
        return project_out(db, p, user)

    @app.put('/api/projects/{project_id}/members')
    def update_members(project_id: int, data: MembersIn, db: Session = Depends(get_db), user=Depends(current_user)):
        p = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
        flow.project_access(db, project_id, user, manage=True)
        before = audit.snapshot(db, 'project', p)
        set_members(db, p, data.members, user)
        audit.record(db, user, 'members', 'project', p, before=before)
        db.commit()
        return project_out(db, p, user)

    @app.get('/api/admin/flows/{kind}')
    def read_flow(kind: str, db: Session = Depends(get_db), user=Depends(admin)):
        t = db.get(WorkflowTemplate, kind)
        if not t: flow.fail('流程不存在', 404)
        versions = db.scalars(select(WorkflowVersion).where(WorkflowVersion.kind == kind).order_by(WorkflowVersion.version.desc())).all()
        return {'kind': kind, 'draft': flow.normalize_definition(json.loads(t.draft)), 'revision': t.revision,
                'versions': [serialize(v, definition=json.loads(v.definition)) for v in versions], 'fields': sorted(flow.FIELDS[kind])}

    @app.put('/api/admin/flows/{kind}/draft')
    def save_draft(kind: str, data: DraftIn, db: Session = Depends(get_db), user=Depends(admin)):
        t = db.scalar(select(WorkflowTemplate).where(WorkflowTemplate.kind == kind).with_for_update())
        if not t: flow.fail('流程不存在', 404)
        if t.revision != data.revision: flow.fail('草稿已被其他管理员修改，请重新加载', 409)
        if len(flow.dump(data.definition)) > 250000: flow.fail('草稿过大')
        # Incomplete drafts are allowed; publish and preview enforce validation.
        before = {'revision': t.revision}
        t.draft, t.updated_by, t.revision = flow.dump(flow.normalize_definition(data.definition)), user.id, t.revision + 1
        audit.record(db, user, 'draft', 'workflow', before=before, after={'revision': t.revision},
                     target_id=kind, target_name=audit.TYPES.get(kind, kind) + '审批流程')
        db.commit()
        return {'revision': t.revision, 'message': '草稿已保存'}

    @app.post('/api/admin/flows/{kind}/validate')
    def validate_flow(kind: str, data: dict, user=Depends(admin)):
        return flow.validate(kind, data.get('definition'))

    @app.post('/api/admin/flows/{kind}/preview')
    def preview_flow(kind: str, data: dict, db: Session = Depends(get_db), user=Depends(admin)):
        owner = db.get(User, data.get('applicant_id'))
        if not owner or not owner.active: flow.fail('请选择已启用的申请人')
        return flow.resolve(db, kind, data.get('definition'), data.get('data', {}), owner, data.get('project_id'))

    @app.post('/api/admin/flows/{kind}/publish')
    def publish_flow(kind: str, data: dict, db: Session = Depends(get_db), user=Depends(admin)):
        t = db.scalar(select(WorkflowTemplate).where(WorkflowTemplate.kind == kind).with_for_update())
        if not t: flow.fail('流程不存在', 404)
        if t.revision != data.get('revision'): flow.fail('草稿已更新，请重新加载后发布', 409)
        definition = flow.normalize_definition(json.loads(t.draft))
        flow.validate(kind, definition)
        # Check fixed member existence/grants now; project resolution happens per submission.
        def walk(steps):
            for n in steps:
                if n['type'] == 'branch':
                    for a in n['branches']: walk(a['steps'])
                elif n['assignee']['source'] == 'fixed':
                    for uid in n['assignee']['user_ids']:
                        p = db.get(User, uid)
                        if not p or not p.active or (n['type'] == 'execution' and not has_role(p, 'operator')):
                            flow.fail(f"{n['name']}：固定成员无效或无运维权限")
        walk(definition['steps'])
        current = db.scalar(select(WorkflowVersion).where(WorkflowVersion.kind == kind).order_by(WorkflowVersion.version.desc()))
        v = WorkflowVersion(kind=kind, version=(current.version if current else 0) + 1, definition=flow.dump(definition), published_by=user.id)
        db.add(v)
        audit.record(db, user, 'publish', 'workflow', after={'version': v.version},
                     target_id=kind, target_name=audit.TYPES.get(kind, kind) + '审批流程')
        db.commit()
        return serialize(v, message='已发布；只对新提交和重新提交生效')

    @app.post('/api/admin/tasks/{task_id}/reassign')
    def reassign_task(task_id: int, data: dict, tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(admin)):
        return transfer(db, task_id, data, tasks, user, OBJECTS, notify)


def transfer(db, task_id, data, tasks, user, objects, notify):
    task = db.get(Approval, task_id)
    if not task: flow.fail('任务不存在', 404)
    obj = db.scalar(select(objects[task.object_type]).where(objects[task.object_type].id == task.object_id).with_for_update())
    task = db.scalar(select(Approval).where(Approval.id == task_id).with_for_update().execution_options(populate_existing=True))
    if task.action != 'pending': flow.fail('只能转交待处理任务', 409)
    person = db.get(User, data.get('assignee_id'))
    snap = json.loads(task.assignee_snapshot or '{}')
    if not person or not person.active or person.id == flow.owner_id(task.object_type, obj):
        flow.fail('处理人无效或与申请人相同')
    required = snap.get('required_role')
    matches = has_department_role(person, required) if snap.get('department') else has_role(person, required)
    if required and not matches: flow.fail('新处理人未获对应岗位角色')
    if snap.get('department') and person.department != snap['department']: flow.fail('新处理人不属于申请部门')
    if not required and snap.get('roles') and not retained_snapshot_roles(person, snap['roles']): flow.fail('新处理人未获原节点所需角色')
    if db.scalar(select(Approval.id).where(Approval.node_run_id == task.node_run_id, Approval.assignee_id == person.id, Approval.id != task.id)):
        flow.fail('此人已在当前节点中，不能重复分配')
    reason = str(data.get('reason', '')).strip()
    if not reason: flow.fail('请填写转交原因')
    previous = db.get(User, task.assignee_id) if task.assignee_id else None
    node = db.get(WorkflowNode, task.node_run_id) if task.node_run_id else None
    audit.record(db, user, 'transfer', task.object_type, obj, after={}, extra={
        'handler': (f'{previous.display_name}（{previous.username}）' if previous else '未分配', f'{person.display_name}（{person.username}）'),
        'node': (None, node.name if node else task.stage), 'reason': (None, reason)})
    db.add(TaskTransfer(task_id=task.id, from_user_id=task.assignee_id, to_user_id=person.id, actor_id=user.id, reason=reason))
    task.assignee_id = person.id
    # Grant requirements remain fixed; assignment history and original instance snapshot remain intact.
    task.assignee_snapshot = flow.dump({**snap, 'id': person.id, 'name': person.display_name})
    from .main import workflow_notifications
    owner = db.get(User, flow.owner_id(task.object_type, obj))
    workflow_notifications(db, tasks, task.object_type, obj, owner, event='approval_reassigned', new_task_ids={task.id})
    return {'message': '待办已转交'}


def seed_demo(db):
    """Add V3 sample grants/members once in the bundled demonstration installation."""
    if db.get(SystemSetting, 'demo_v3_seeded') or not db.scalar(select(User).where(User.username == 'applicant', User.display_name == '陈一诺')):
        return
    from .security import hash_password
    additions = [
        ('projectowner', '沈知遥', ['project_owner', 'project_manager'], '产品研发部'),
        ('projectmanager', '顾景行', ['project_manager'], '市场部'),
        ('projectdirector', '陆承安', ['project_director'], '管理层'),
        ('saleshead', '宋予安', ['sales_head'], '市场部'),
        ('salesdirector', '程以宁', ['sales_director'], '市场部'),
        ('devhead', '江屿', ['development_head'], '产品研发部'),
        ('devhead2', '苏念', ['development_head'], '产品研发部'),
    ]
    for username, name, roles, dept in additions:
        if not db.scalar(select(User).where(User.username == username)):
            p = User(username=username, display_name=name, role=roles[0], department=dept, password_hash=hash_password('Demo@2026'))
            p.role_grants = [UserRole(role=r) for r in roles]; db.add(p)
    db.flush()
    people = {p.username: p for p in db.scalars(select(User)).all()}
    for name, dept, manager, applicant in [('星河数据分析平台', '产品研发部', 'projectowner', 'applicant'), ('市场交付项目', '市场部', 'projectmanager', 'applicant2')]:
        p = Project(name=name, department=dept, created_by=people['admin'].id)
        db.add(p); db.flush()
        entries = [('projectowner', 'project_owner'), (manager, 'project_manager'), ('projectdirector', 'project_director'), ('saleshead', 'sales_head'), ('salesdirector', 'sales_director'), ('devhead', 'development_head'), ('devhead2', 'development_head'), (applicant, 'applicant'), ('operator', 'operator')]
        for username, role in entries:
            db.add(ProjectMember(project_id=p.id, user_id=people[username].id, role=role))
    db.add(SystemSetting(key='demo_v3_seeded', value='true', is_secret=False))
    db.commit()
