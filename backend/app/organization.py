"""Organization directory, grant sources and transaction-level scope checks."""
import json
import os
import unicodedata
from collections import defaultdict
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func, or_, and_, delete
from sqlalchemy.orm import Session, object_session, selectinload
from .database import get_db
from .models import (User, Project, ProjectMember, Approval, Department, DepartmentAlias,
                     DepartmentManager, DepartmentPosition, DepartmentPositionRole,
                     DepartmentUserPosition, DepartmentMembership, WorkflowTemplate, WorkflowVersion)
from .roles import (ROLES, canonical_role, direct_role_ids, effective_role_ids, has_role,
                    role_name, managed_roles, is_department_manager, can_manage_people,
                    MANAGER_ROLE_SCOPE, role_aliases, RETIRED_HEAD_ROLES, DEPARTMENT_MEMBER_ROLES,
                    member_departments, managed_department_ids)
from . import audit

DELEGATABLE_ROLES = DEPARTMENT_MEMBER_ROLES


def name_key(name):
    return ''.join(c for c in unicodedata.normalize('NFKD', name.strip()).casefold() if not unicodedata.combining(c))


def ancestor_ids(department_id, parents):
    """Return this department and all ancestors, bounded even for legacy cycles."""
    result = set()
    while department_id is not None and department_id not in result:
        result.add(department_id)
        department_id = parents.get(department_id)
    return result


def hierarchy_parents(db):
    return dict(db.execute(select(Department.id, Department.parent_id)).all())


def validate_position_name(db, department_id, name, position=None):
    # 保留历史同名岗位和原授权；只有新增或实际改名才执行新的链路重名限制。
    if position and name_key(position.name) == name_key(name):
        return
    parents = hierarchy_parents(db)
    ancestors = ancestor_ids(department_id, parents)
    rows = db.scalars(select(DepartmentPosition).order_by(DepartmentPosition.id))
    for other in rows:
        if (position and other.id == position.id) or name_key(other.name) != name_key(name):
            continue
        if other.department_id == department_id:
            fail('本部门已有同名岗位')
        if other.department_id in ancestors:
            fail(f'上级部门「{other.department.name}」已有岗位「{other.name}」，请直接引用')
        if department_id in ancestor_ids(other.department_id, parents):
            fail(f'下级部门「{other.department.name}」已有岗位「{other.name}」，请先由管理员整理同名岗位')


def validate_department_move(db, department, parent_id):
    """Reject only new name conflicts and moves invalidating existing references."""
    before = hierarchy_parents(db)
    after = {**before, department.id: parent_id}
    positions = list(db.scalars(select(DepartmentPosition)))
    by_name = defaultdict(list)
    for position in positions:
        by_name[name_key(position.name)].append(position)
    for group in by_name.values():
        for index, left in enumerate(group):
            for right in group[index + 1:]:
                related = lambda parents: (left.department_id in ancestor_ids(right.department_id, parents)
                    or right.department_id in ancestor_ids(left.department_id, parents))
                if related(after) and not related(before):
                    fail(f'调整层级会造成「{left.department.name}」与「{right.department.name}」岗位「{left.name}」重名，请先整理')
    moved_ids = {key for key in before if department.id in ancestor_ids(key, before)}
    grants = db.scalars(select(DepartmentUserPosition).where(DepartmentUserPosition.department_id.in_(moved_ids)))
    for grant in grants:
        if grant.position.department_id not in ancestor_ids(grant.department_id, after):
            fail(f'「{grant.department.name}」正在引用「{grant.position.department.name}」的岗位「{grant.position.name}」，请先调整人员岗位再移动部门')


def writes_enabled():
    return os.getenv('ORGANIZATION_WRITES_ENABLED', 'true').strip().lower() not in {'false', '0'}


def ensure_writable():
    if not writes_enabled():
        fail('组织配置处于只读维护状态，请稍后重试')


def fail(message, status=409):
    raise HTTPException(status, message)


def seed_organization(db):
    """Import legacy labels once; never infer positions or manager appointments."""
    rows = [*db.scalars(select(User).where(User.department_id.is_(None))),
            *db.scalars(select(Project).where(Project.department_id.is_(None)))]
    names = {row.department for row in rows if row.department and row.department.strip()}
    # 名称冲突先停止升级，不把可能属于不同组织的历史名称静默合并。
    normalized = {}
    for name in sorted(names):
        key = name_key(name)
        if key in normalized and normalized[key] != name:
            raise RuntimeError(f'组织迁移发现部门名称冲突：{normalized[key]} / {name}，请先统一名称后重试')
        normalized[key] = name
    for raw in sorted(names):
        department = find_department(db, raw)
        if department and department.name != raw.strip() and not db.get(DepartmentAlias, raw):
            raise RuntimeError(f'组织迁移发现名称排序规则冲突：{department.name} / {raw}，请先确认部门映射')
        if not department:
            department = Department(name=raw.strip(), approval_mode='legacy_roles')
            db.add(department); db.flush()
        for alias in {raw, raw.strip()}:
            existing = db.get(DepartmentAlias, alias)
            if existing and existing.department_id != department.id:
                raise RuntimeError(f'组织迁移发现历史部门别名冲突：{alias}')
            if not existing:
                db.add(DepartmentAlias(name=alias, department_id=department.id))
                # MySQL 的名称排序规则可能忽略尾空格；先落库，使下一个别名查询
                # 能命中相同记录，避免单个历史名称的去空白别名重复插入。
                db.flush()
        for row in rows:
            if row.department == raw:
                row.department_id = department.id
                row.department = department.name
                if isinstance(row, User):
                    row.organization_department = department
        db.flush()

    # 主部门只用于默认归属；新增成员关系不改变原岗位、任命与直接角色。
    db.flush()
    for person in db.scalars(select(User).where(User.department_id.is_not(None))):
        if not any(m.department_id == person.department_id for m in person.department_memberships):
            person.department_memberships.append(DepartmentMembership(department_id=person.department_id))
    db.flush()



def migrate_business_grants_to_positions(db):
    """Move known grants to equivalent positions without guessing from job names."""
    from .models import SystemSetting, UserRole
    from .roles import SYSTEM_ROLES
    marker = 'organization_business_positions_v107'
    setting = db.get(SystemSetting, marker)
    if setting and not json.loads(setting.value).get('unmapped_users'):
        return
    business = set(ROLES) - SYSTEM_ROLES - RETIRED_HEAD_ROLES - {'department_head'}
    unmapped = []
    migrated = json.loads(setting.value).get('migrated', []) if setting else []
    for person in db.scalars(select(User).order_by(User.id)):
        original = direct_role_ids(person)
        grants = {canonical_role(r) for r in original} & business
        if not grants:
            continue
        departments = member_departments(person)
        department = departments.get(person.department_id)
        if not department or not department.active:
            department = next((d for d in departments.values() if d.active), None)
        if not department:
            # 异常历史成员先保留原授权，下次启动再次迁移；禁止静默撤销项目资格。
            unmapped.append(person.id)
            continue
        ancestors = ancestor_ids(department.id, hierarchy_parents(db))
        assignments = []
        for role in sorted(grants):
            candidates = db.scalars(select(DepartmentPosition).where(
                DepartmentPosition.department_id.in_(ancestors), DepartmentPosition.active == True).order_by(DepartmentPosition.id))
            position = next((p for p in candidates if p.department.active and
                             {canonical_role(g.role) for g in p.role_grants} == {role}), None)
            if not position:
                name = role_name(role)
                # 不覆盖任何已有同名岗位及其授权，保留继承链命名约束。
                occupied = {name_key(p.name) for p in db.scalars(select(DepartmentPosition))
                            if p.department_id in ancestors or department.id in ancestor_ids(p.department_id, hierarchy_parents(db))}
                suffix = 0
                while name_key(name) in occupied:
                    suffix += 1
                    name = f'{role_name(role)}（历史权限迁移{suffix}）'
                position = DepartmentPosition(department=department, name=name, manager_assignable=True,
                    role_grants=[DepartmentPositionRole(role=role)])
                db.add(position); db.flush()
            if not any(g.position_id == position.id and g.department_id == department.id for g in person.position_grants):
                person.position_grants.append(DepartmentUserPosition(position=position, department_id=department.id))
            assignments.append({'role': role, 'position_id': position.id})
        migrated.append({'user_id': person.id, 'department_id': department.id, 'original_grants': original, 'positions': assignments})
        remaining = [r for r in original if canonical_role(r) not in business]
        if not any(canonical_role(r) in SYSTEM_ROLES for r in remaining):
            remaining.insert(0, 'applicant')
        person.role_grants = [UserRole(role=r) for r in remaining]
        person.role = remaining[0]
        person.organization_revision += 1
        db.flush()
    value = json.dumps({'unmapped_users': unmapped, 'migrated': migrated}, ensure_ascii=False)
    if setting:
        setting.value = value
    else:
        db.add(SystemSetting(key=marker, value=value, is_secret=False))
    db.flush()


def unavailable_workflow_nodes(db):
    """Count eligible current/future handlers, preserving completed decisions."""
    from .models import WorkflowNode, WorkflowInstance
    from . import flow_engine as flow
    invalid = set()
    nodes = db.scalars(select(WorkflowNode).join(WorkflowInstance).where(
        WorkflowInstance.status.in_(['pending', 'in_progress']), WorkflowNode.status.in_(['pending', 'waiting'])))
    for node in nodes:
        config = json.loads(node.config)
        tasks = list(db.scalars(select(Approval).where(Approval.node_run_id == node.id))) if node.status == 'pending' else []
        approved = sum(t.action == 'approve' for t in tasks)
        snapshots = [(t.assignee_id, json.loads(t.assignee_snapshot or '{}')) for t in tasks if t.action == 'pending'] if tasks else [
            (p['id'], p) for p in config.get('assignees', [])]
        eligible = sum(flow.snapshot_eligible(db.get(User, uid), snap, json.loads(node.assignment_policy) if node.assignment_policy else None) for uid, snap in snapshots)
        total = len(tasks) if tasks else len(snapshots)
        required = total if node.mode == 'all' else 1 if node.mode == 'any' else int(config.get('required_count') or 0)
        if approved + eligible < required or not total:
            invalid.add(node.id)
    return invalid


def protect_pending_tasks(db):
    baseline = db.info.get('organization_invalid_nodes_before')
    if baseline is None:
        return
    db.flush()
    db.expire_all()
    if unavailable_workflow_nodes(db) - baseline:
        fail('此调整会使在途审批或执行节点缺少合格处理人，请先转交待办或完成相关审批后再调整', 409)

def find_department(db, name):
    if not name:
        return None
    alias = db.get(DepartmentAlias, str(name))
    if alias:
        return db.get(Department, alias.department_id)
    return db.scalar(select(Department).where(Department.name == str(name)))


def membership_ids(person):
    if isinstance(person, User):
        return set(member_departments(person))
    return {person.department_id} if person.department_id is not None else set()


def membership_filter(model, department_ids):
    ids = set(department_ids)
    primary = model.department_id.in_(ids)
    return or_(primary, model.department_memberships.any(DepartmentMembership.department_id.in_(ids))) if model is User else primary


def same_department(left, right):
    if membership_ids(left) or membership_ids(right):
        return bool(membership_ids(left) & membership_ids(right))
    return bool(left.department.strip()) and left.department == right.department


def department_scope(model, person):
    ids = managed_department_ids(person) or ({person.department_id} if person.department_id is not None else set())
    # 业务可见范围继续按单据主归属；成员目录另用 membership_filter。
    return model.department_id.in_(ids) if ids else model.department == person.department


def snapshot_department_matches(person, snapshot):
    if snapshot.get('department_id') is not None:
        return snapshot['department_id'] in membership_ids(person)
    if not snapshot.get('department'):
        return True
    db = object_session(person)
    department = find_department(db, snapshot['department']) if db else None
    if department:
        return department.id in membership_ids(person)
    return person.department_id is None and person.department == snapshot['department']


def require_department(db, department_id):
    if type(department_id) is not int or department_id <= 0:
        fail('请选择已有的所属部门', 422)
    department = db.get(Department, department_id)
    if not department:
        fail('所选部门不存在，请刷新组织目录', 404)
    if not department.active:
        fail('所选部门已停用，请选择启用部门')
    return department


def write_lock(db, actor, target_ids=(), *, track_pending=True):
    # 本函数必须是写事务入口。结束鉴权阶段的只读事务，避免 MySQL REPEATABLE READ
    # 沿用加锁前的授权快照；加锁后再读取任命与岗位，才能看到刚提交的撤权。
    if db.new or db.dirty or db.deleted:
        raise RuntimeError('组织写锁必须在修改数据之前获取')
    actor_id = actor.id
    db.rollback()
    for key in list(db.info):
        if key.startswith('organization_'):
            db.info.pop(key, None)
    # 部门行采用固定顺序加锁；所有组织及人员授权写入口共用，防止跨副本检查后被改任命。
    list(db.scalars(select(Department).order_by(Department.id).with_for_update().execution_options(populate_existing=True)))
    people = list(db.scalars(select(User).where(User.id.in_(set(target_ids) | {actor_id}))
                             .order_by(User.id).with_for_update().execution_options(populate_existing=True)))
    if not actor.active:
        fail('账号已停用，请重新登录', 403)
    if track_pending:
        db.info['organization_invalid_nodes_before'] = unavailable_workflow_nodes(db)
    return {p.id: p for p in people}


def admin_only(actor):
    if not has_role(actor, 'admin'):
        fail('只有系统管理员可以维护组织架构', 403)


def may_manage_member(actor, person, department_id=None):
    return bool(has_role(actor, 'admin') or (
        person.active and bool(managed_department_ids(actor) & membership_ids(person) &
        ({department_id} if department_id is not None else managed_department_ids(actor)))
        and person.id != actor.id and not has_role(person, 'admin', *MANAGER_ROLE_SCOPE)
        and not is_department_manager(person)))


def manageable_position(actor, position, department_id=None):
    if has_role(actor, 'admin'):
        return True
    roles = {canonical_role(r.role) for r in position.role_grants}
    if not position.active or not position.department.active or not position.manager_assignable or not roles <= DELEGATABLE_ROLES:
        return False
    return is_department_manager(actor, department_id if department_id is not None else position.department_id)


def organization_info(person):
    db = object_session(person)
    departments = member_departments(person)
    positions = [g for g in person.position_grants if g.department_id in departments]
    sources = {}
    for role in direct_role_ids(person):
        sources.setdefault(canonical_role(role), []).append('直接授权')
    for assignment in positions:
        position = assignment.position
        department = departments[assignment.department_id]
        if position.active and position.department.active and department.active:
            for grant in position.role_grants:
                origin = f'（来自{position.department.name}）' if position.department_id != department.id else ''
                sources.setdefault(canonical_role(grant.role), []).append(f'{department.name} / {position.name}{origin}')
    for key in managed_department_ids(person):
        sources.setdefault('department_head', []).append(f'{departments[key].name} / 部门负责人任命')
    effective = set(effective_role_ids(person))
    return {'department_id': person.department_id, 'department_path': department_path(db, person.organization_department),
            'department_ids': sorted(departments),
            'departments': [{'id': d.id, 'name': d.name, 'path': department_path(db, d), 'active': d.active,
                             'primary': d.id == person.department_id, 'is_manager': is_department_manager(person, d.id)}
                            for d in sorted(departments.values(), key=lambda d: d.id)],
            'managed_department_ids': sorted(managed_department_ids(person)),
            'organization_revision': person.organization_revision, 'direct_roles': direct_role_ids(person),
            'positions': [{'id': g.position_id, 'name': g.position.name,
                           'active': g.position.active and g.position.department.active,
                           'department_id': g.department_id, 'department_name': departments[g.department_id].name,
                           'source_department_id': g.position.department_id,
                           'source_department_name': g.position.department.name,
                           'inherited': g.department_id != g.position.department_id} for g in positions],
            'role_sources': {r: values for r, values in sources.items() if r in effective},
            'organization_writes_enabled': writes_enabled(),
            'can_manage_people': can_manage_people(person), 'is_department_manager': is_department_manager(person)}


def department_path(db, department):
    cache = db.info.setdefault('organization_paths', {}) if db else {}
    if department and department.id in cache:
        return cache[department.id]
    original_id = department.id if department else None
    names, seen = [], set()
    while department and department.id not in seen:
        names.append(department.name); seen.add(department.id)
        department = db.get(Department, department.parent_id) if db and department.parent_id else None
    result = ' / '.join(reversed(names))
    if original_id:
        cache[original_id] = result
    return result


def assign_organization(db, actor, person, department_id, position_ids, revision=None, set_primary=True):
    ensure_writable()
    if revision is not None and revision != person.organization_revision:
        fail('成员组织信息已被修改，请刷新后重试')
    if not may_manage_member(actor, person, department_id):
        fail('无权调整该成员的组织岗位', 403)
    department = require_department(db, department_id)
    if len(position_ids) != len(set(position_ids)):
        fail('岗位不能重复', 422)
    positions = list(db.scalars(select(DepartmentPosition).where(DepartmentPosition.id.in_(position_ids)))) if position_ids else []
    if len(positions) != len(position_ids):
        fail('所选岗位不存在，请刷新组织目录', 404)
    previous_ids = {g.position_id for g in person.position_grants if g.department_id == department.id}
    available_departments = ancestor_ids(department.id, hierarchy_parents(db))
    if any(p.department_id not in available_departments or
           ((not p.active or not p.department.active) and p.id not in previous_ids) for p in positions):
        fail('只能选择本部门或上级部门的启用岗位')
    old = [g for g in person.position_grants if g.department_id == department.id]
    if not has_role(actor, 'admin'):
        if department.id not in membership_ids(person) or not is_department_manager(actor, department.id) or (set_primary and department.id != person.department_id):
            fail('负责人不能调整成员所属部门', 403)
        old_ids = {g.position_id for g in old}
        changed = old_ids ^ set(position_ids)
        all_positions = {p.id: p for p in positions} | {g.position_id: g.position for g in old}
        if any(not manageable_position(actor, all_positions[pid], department.id) for pid in changed):
            fail('只能调整本部门允许委派且职责匹配的岗位', 403)
    # 仅替换当前部门明确提交的岗位；其他部门角色来源与任命不随主部门变动。
    if person.department_id is not None and not any(m.department_id == person.department_id for m in person.department_memberships):
        person.department_memberships.append(DepartmentMembership(department_id=person.department_id))
    if not any(m.department_id == department.id for m in person.department_memberships):
        person.department_memberships.append(DepartmentMembership(department=department))
    existing = {g.position_id: g for g in old}
    person.position_grants = [g for g in person.position_grants if g.department_id != department.id] + [
        existing.get(p.id) or DepartmentUserPosition(position=p, department_id=department.id) for p in positions]
    if set_primary or person.department_id is None:
        person.department_id, person.department = department.id, department.name
        person.organization_department = department
    person.organization_revision = (person.organization_revision or 1) + 1
    db.flush()


def role_filter(role):
    """SQL equivalent of effective grants for role-filtered member lists."""
    from .models import UserRole
    aliases = role_aliases(role)
    if canonical_role(role) in RETIRED_HEAD_ROLES:
        return False
    direct = or_(User.role_grants.any(UserRole.role.in_(aliases)),
                 and_(~User.role_grants.any(), User.role.in_(aliases)))
    if canonical_role(role) == 'department_head':
        direct = False
        appointed = and_(User.active == True, or_(
            User.organization_department.has(and_(Department.active == True, Department.managers.any(DepartmentManager.user_id == User.id))),
            User.department_memberships.any(DepartmentMembership.department.has(and_(
                Department.active == True, Department.managers.any(DepartmentManager.user_id == User.id))))))
    else:
        appointed = False
    member_link = select(DepartmentMembership.user_id).where(DepartmentMembership.user_id == User.id,
        DepartmentMembership.department_id == DepartmentUserPosition.department_id).correlate(User, DepartmentUserPosition).exists()
    position = User.position_grants.any(and_(
        DepartmentUserPosition.department.has(Department.active == True),
        or_(DepartmentUserPosition.department_id == User.department_id, member_link),
        DepartmentUserPosition.position.has(and_(
            DepartmentPosition.active == True, DepartmentPosition.department.has(Department.active == True),
            DepartmentPosition.role_grants.any(DepartmentPositionRole.role.in_(aliases))))))
    return or_(direct, position, appointed)


def pending_tasks(db, person_ids=None, department=None):
    query = select(Approval).where(Approval.action == 'pending')
    if person_ids is not None:
        query = query.where(Approval.assignee_id.in_(person_ids))
    rows = list(db.scalars(query))
    if department:
        rows = [t for t in rows if (lambda s: s.get('department_id') == department.id or
            bool(s.get('department') and find_department(db, s['department']) and find_department(db, s['department']).id == department.id))
            (json.loads(t.assignee_snapshot or '{}'))]
    return len(rows)


def member_snapshot(person):
    return {'department': person.department, 'departments': sorted(membership_ids(person)),
            'members': sorted(f'{g.department_id}:{g.position.department_id}:{g.position_id}:{g.position.name}' for g in person.position_grants)}


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DepartmentIn(StrictInput):
    name: str = Field(min_length=1, max_length=100)
    parent_id: int | None = None
    active: bool = True
    sort_order: int = 0
    description: str = Field(default='', max_length=500)
    revision: int | None = None


class ManagersIn(StrictInput):
    user_ids: list[int]
    revision: int


class PositionIn(StrictInput):
    name: str = Field(min_length=1, max_length=100)
    roles: list[str] = []
    active: bool = True
    manager_assignable: bool = True
    revision: int | None = None


class MembershipIn(StrictInput):
    department_id: int
    position_ids: list[int]
    revision: int
    set_primary: bool = True


def position_out(db, position, actor, directory=None, department_id=None):
    department_id = department_id if department_id is not None else position.department_id
    inherited = department_id != position.department_id
    if directory is None:
        subtree = descendant_ids(db, department_id)
        query = select(DepartmentUserPosition.user_id, DepartmentUserPosition.department_id).where(
            DepartmentUserPosition.position_id == position.id, DepartmentUserPosition.department_id.in_(subtree))
        assignments = list(db.execute(query))
        user_ids = {row.user_id for row in assignments}
        direct_user_ids = {row.user_id for row in assignments if row.department_id == department_id}
        reference_departments = {row.department_id for row in assignments}
        pending_count = pending_tasks(db, user_ids)
        project_count = db.scalar(select(func.count()).select_from(ProjectMember).where(ProjectMember.user_id.in_(user_ids)))
        can_assign = manageable_position(actor, position, department_id)
    else:
        subtree = directory['subtrees'][department_id]
        user_ids = directory['subtree_position_users'].get((position.id, department_id), set())
        direct_user_ids = directory['scoped_position_users'].get((position.id, department_id), set())
        reference_departments = directory['subtree_position_departments'].get((position.id, department_id), set())
        pending_count = sum(directory['pending_by_user'].get(user_id, 0) for user_id in user_ids)
        project_count = sum(directory['projects_by_user'].get(user_id, 0) for user_id in user_ids)
        roles = {canonical_role(grant.role) for grant in position.role_grants}
        can_assign = has_role(actor, 'admin') or (
            position.active and position.department.active and position.manager_assignable and roles <= DELEGATABLE_ROLES
            and department_id in directory['managed_department_ids'])
    return {'id': position.id, 'department_id': position.department_id, 'name': position.name,
            'assignment_department_id': department_id, 'source_department_id': position.department_id,
            'source_department_name': position.department.name, 'source_department_active': position.department.active,
            'inherited': inherited, 'member_count_scope': 'department_subtree', 'has_descendants': len(subtree) > 1,
            'direct_member_count': len(direct_user_ids),
            'referenced_department_count': len(reference_departments),
            'roles': [r.role for r in position.role_grants if canonical_role(r.role) not in RETIRED_HEAD_ROLES], 'active': position.active,
            'manager_assignable': position.manager_assignable, 'revision': position.revision,
            'member_count': len(user_ids), 'pending_tasks': pending_count,
            'project_assignments': project_count, 'can_assign': can_assign}


def descendant_ids(db, department_id):
    rows = db.info.get('organization_hierarchy')
    if rows is None:
        rows = list(db.execute(select(Department.id, Department.parent_id)))
        db.info['organization_hierarchy'] = rows
    result = {department_id}
    while True:
        expanded = result | {key for key, parent in rows if parent in result}
        if expanded == result:
            return result
        result = expanded


def department_member_counts(db):
    if 'organization_member_counts' not in db.info:
        rows = list(db.execute(select(DepartmentMembership.department_id, DepartmentMembership.user_id)))
        rows += list(db.execute(select(User.department_id, User.id).where(User.department_id.is_not(None))))
        direct = {}
        for key, uid in rows:
            direct.setdefault(key, set()).add(uid)
        ids = list(db.scalars(select(Department.id)))
        db.info['organization_member_counts'] = {key: len(set().union(*(direct.get(child, set()) for child in descendant_ids(db, key)))) for key in ids}
    return db.info['organization_member_counts']


def department_directory_stats(db, departments, actor):
    """Load shared organization summaries once for the department directory."""
    if not departments:
        return {'members': {}, 'active_users': {}, 'managers': {}, 'positions': {}, 'position_users': {},
                'scoped_position_users': {}, 'position_departments': {},
                'subtrees': {}, 'subtree_position_users': {}, 'subtree_position_departments': {},
                'pending_by_department': {}, 'pending_by_user': {}, 'projects_by_user': {},
                'member_counts': {}, 'paths': {}, 'managed_department_ids': set()}

    all_department_rows = list(db.execute(select(Department.id, Department.name, Department.parent_id, Department.active)))
    department_names = {row.id: row.name for row in all_department_rows}
    parents = {row.id: row.parent_id for row in all_department_rows}
    active_department_ids = {row.id for row in all_department_rows if row.active}
    paths = {}
    for row in all_department_rows:
        names, seen, current = [], set(), row.id
        while current is not None and current not in seen:
            seen.add(current)
            parent = next((item.parent_id for item in all_department_rows if item.id == current), None)
            names.append(department_names[current])
            current = parent
        paths[row.id] = ' / '.join(reversed(names))

    users = list(db.execute(select(User.id, User.display_name, User.active, User.department_id)))
    user_names = {row.id: row.display_name for row in users}
    active_users = {row.id for row in users if row.active}
    direct_members = defaultdict(set)
    for row in users:
        if row.department_id is not None:
            direct_members[row.department_id].add(row.id)
    membership_rows = list(db.execute(select(DepartmentMembership.department_id, DepartmentMembership.user_id)))
    for department_id, user_id in membership_rows:
        direct_members[department_id].add(user_id)
    members = {department.id: direct_members.get(department.id, set()) for department in departments}

    all_ids = set(department_names)
    hierarchy = [(row.id, row.parent_id) for row in all_department_rows]
    member_counts, subtrees = {}, {}
    for department_id in all_ids:
        subtree = {department_id}
        while True:
            expanded = subtree | {child for child, parent in hierarchy if parent in subtree}
            if expanded == subtree:
                break
            subtree = expanded
        member_counts[department_id] = len(set().union(*(direct_members.get(child, set()) for child in subtree)))
        subtrees[department_id] = subtree

    requested_ids = {department.id for department in departments}
    managers = defaultdict(set)
    for department_id, user_id in db.execute(
            select(DepartmentManager.department_id, DepartmentManager.user_id)
            .where(DepartmentManager.department_id.in_(requested_ids))):
        managers[department_id].add(user_id)

    positions = defaultdict(list)
    ancestors = {key: ancestor_ids(key, parents) for key in requested_ids}
    source_ids = set().union(*ancestors.values())
    position_rows = list(db.scalars(select(DepartmentPosition)
        .where(DepartmentPosition.department_id.in_(source_ids))
        .options(selectinload(DepartmentPosition.role_grants))
        .order_by(DepartmentPosition.department_id, DepartmentPosition.id)))
    position_ids = {position.id for position in position_rows}
    position_users = defaultdict(set)
    scoped_position_users, position_departments = defaultdict(set), defaultdict(set)
    subtree_position_users, subtree_position_departments = defaultdict(set), defaultdict(set)
    for user_id, position_id, department_id in db.execute(select(DepartmentUserPosition.user_id,
                                           DepartmentUserPosition.position_id, DepartmentUserPosition.department_id)
                                           .where(DepartmentUserPosition.position_id.in_(position_ids))):
        position_users[position_id].add(user_id)
        scoped_position_users[position_id, department_id].add(user_id)
        position_departments[position_id].add(department_id)
        # 按任职部门向祖先汇总集合；同一人在多个下级引用同一岗位只计一次。
        # 这里只改变统计，负责人可分配岗位和可管理人员的部门范围保持原规则。
        for ancestor_id in ancestor_ids(department_id, parents) & requested_ids:
            subtree_position_users[position_id, ancestor_id].add(user_id)
            subtree_position_departments[position_id, ancestor_id].add(department_id)
    for department_id in requested_ids:
        positions[department_id] = sorted((position for position in position_rows
            if position.department_id in ancestors[department_id]),
            key=lambda position: (position.department_id != department_id, position.department_id, position.id))

    pending_by_department, pending_by_user = defaultdict(int), defaultdict(int)
    pending_rows = list(db.execute(select(Approval.assignee_id, Approval.assignee_snapshot)
                                   .where(Approval.action == 'pending')))
    aliases = {name: department_id for name, department_id in db.execute(
        select(DepartmentAlias.name, DepartmentAlias.department_id))}
    name_ids = {name: department_id for department_id, name in department_names.items()}
    normalized_ids = {name_key(name): department_id for name, department_id in {**aliases, **name_ids}.items()}
    for assignee_id, snapshot_text in pending_rows:
        if assignee_id is not None:
            pending_by_user[assignee_id] += 1
        snapshot = json.loads(snapshot_text or '{}')
        referenced_departments = set()
        department_id = snapshot.get('department_id')
        if department_id is not None:
            referenced_departments.add(department_id)
        label = snapshot.get('department')
        if label:
            resolved_id = aliases.get(label, name_ids.get(label))
            if resolved_id is None:
                resolved_id = normalized_ids.get(name_key(label))
            if resolved_id is not None:
                referenced_departments.add(resolved_id)
        for department_id in referenced_departments & requested_ids:
            pending_by_department[department_id] += 1

    position_user_ids = set().union(*position_users.values()) if position_users else set()
    projects_by_user = defaultdict(int)
    if position_user_ids:
        for user_id, count in db.execute(select(ProjectMember.user_id, func.count())
                .where(ProjectMember.user_id.in_(position_user_ids)).group_by(ProjectMember.user_id)):
            projects_by_user[user_id] = count

    manager_rows = {department_id: ids for department_id, ids in managers.items()}
    actor_department_ids = membership_ids(actor)
    managed = {department_id for department_id in actor_department_ids & active_department_ids
               if actor.id in manager_rows.get(department_id, set())}
    return {'members': members, 'user_names': user_names, 'active_users': active_users, 'managers': managers,
            'positions': positions, 'position_users': position_users,
            'scoped_position_users': scoped_position_users, 'position_departments': position_departments,
            'subtrees': subtrees, 'subtree_position_users': subtree_position_users,
            'subtree_position_departments': subtree_position_departments,
            'pending_by_department': pending_by_department, 'pending_by_user': pending_by_user,
            'projects_by_user': projects_by_user, 'member_counts': member_counts, 'paths': paths,
            'managed_department_ids': managed}


def department_out(db, department, actor, directory=None):
    if directory is None:
        people = list(db.scalars(select(User).where(membership_filter(User, {department.id})).order_by(User.id)))
        head_ids = {m.user_id for m in department.managers}
        heads = [{'id': p.id, 'name': p.display_name, 'roles': ['department_head'], 'role_names': ['部门负责人']}
                 for p in people if p.active and p.id in head_ids]
        positions = list(db.scalars(select(DepartmentPosition).where(DepartmentPosition.department_id.in_(
            ancestor_ids(department.id, hierarchy_parents(db)))).order_by(DepartmentPosition.id)))
        member_count = department_member_counts(db).get(department.id, len(people))
        pending_count = pending_tasks(db, department=department)
        position_output = [position_out(db, position, actor, department_id=department.id) for position in positions]
        path = department_path(db, department)
        direct_member_count = len(people)
    else:
        people_ids = directory['members'].get(department.id, set())
        head_ids = directory['managers'].get(department.id, set())
        heads = [{'id': user_id, 'name': directory['user_names'][user_id], 'roles': ['department_head'], 'role_names': ['部门负责人']}
                 for user_id in sorted(people_ids & head_ids & directory['active_users'])]
        positions = directory['positions'].get(department.id, [])
        member_count = directory['member_counts'].get(department.id, len(people_ids))
        pending_count = directory['pending_by_department'].get(department.id, 0)
        position_output = [position_out(db, position, actor, directory, department.id) for position in positions]
        path = directory['paths'].get(department.id, department.name)
        direct_member_count = len(people_ids)
    return {'id': department.id, 'name': department.name, 'parent_id': department.parent_id,
            'path': path, 'active': department.active,
            'sort_order': department.sort_order, 'description': department.description,
            'revision': department.revision, 'approval_mode': department.approval_mode,
            'member_count': member_count, 'direct_member_count': direct_member_count, 'heads': heads, 'head_ids': sorted(head_ids),
            'head_candidates': [], 'pending_tasks': pending_count, 'positions': position_output}


def department_workflow_references(db, department):
    names = {department.name, f'department:{department.id}'}
    names.update(db.scalars(select(DepartmentAlias.name).where(DepartmentAlias.department_id == department.id)))
    def references(definition):
        if isinstance(definition, dict):
            if definition.get('field') == 'department':
                value = definition.get('value')
                values = value if isinstance(value, list) else [value]
                if any(isinstance(item, str) and item in names for item in values):
                    return True
            return any(references(value) for value in definition.values())
        return isinstance(definition, list) and any(references(item) for item in definition)
    drafts = sum(references(json.loads(t.draft)) for t in db.scalars(select(WorkflowTemplate)))
    latest = select(WorkflowVersion.kind, func.max(WorkflowVersion.version).label('version')).group_by(WorkflowVersion.kind).subquery()
    versions = db.scalars(select(WorkflowVersion).join(latest, and_(
        WorkflowVersion.kind == latest.c.kind, WorkflowVersion.version == latest.c.version)))
    published = sum(references(json.loads(v.definition)) for v in versions)
    return {'审批流程草稿引用': drafts, '现行已发布流程引用': published}


def deletion_impact(db, kind, item):
    count = lambda model, *where: db.scalar(select(func.count()).select_from(model).where(*where))
    if kind == 'department':
        from .models import ResourceRequest, ChangeRequest, DeliveryProject, LicenseRequest, WikiSpaceDepartment
        application_count = sum(count(model, model.application_department_id == item.id) for model in
                                (ResourceRequest, ChangeRequest, DeliveryProject, LicenseRequest))
        counts = {
            '直属成员（含兼职及停用成员）': count(User, membership_filter(User, {item.id})),
            '子部门': count(Department, Department.parent_id == item.id),
            '关联项目': count(Project, Project.department_id == item.id),
            '知识库空间授权引用': count(WikiSpaceDepartment, WikiSpaceDepartment.department_id == item.id),
            '申请部门历史引用': application_count,
            '部门岗位': count(DepartmentPosition, DepartmentPosition.department_id == item.id),
            '负责人任命': count(DepartmentManager, DepartmentManager.department_id == item.id),
            '未完成部门审批': pending_tasks(db, department=item),
            **department_workflow_references(db, item),
        }
    else:
        counts = {'岗位成员（含停用成员）': db.scalar(select(func.count(func.distinct(DepartmentUserPosition.user_id)))
                  .where(DepartmentUserPosition.position_id == item.id))}
    return {'id': item.id, 'name': item.name, 'revision': item.revision,
            'can_delete': not any(counts.values()),
            'blockers': [{'label': label, 'count': value} for label, value in counts.items() if value]}


def register_routes(app, current_user):
    @app.get('/api/organization/options')
    def options(db: Session = Depends(get_db), actor=Depends(current_user)):
        departments = list(db.scalars(select(Department).where(Department.active == True).order_by(Department.sort_order, Department.id)))
        parents = hierarchy_parents(db)
        positions = list(db.scalars(select(DepartmentPosition).where(DepartmentPosition.active == True,
            DepartmentPosition.department.has(Department.active == True)).order_by(DepartmentPosition.id)))
        return [{'id': d.id, 'name': d.name, 'parent_id': d.parent_id, 'path': department_path(db, d),
                 'positions': [{'id': p.id, 'name': p.name, 'inherited': p.department_id != d.id,
                                'source_department_id': p.department_id, 'source_department_name': p.department.name}
                               for p in positions if p.department_id in ancestor_ids(d.id, parents)]} for d in departments]

    @app.get('/api/departments')
    def departments(db: Session = Depends(get_db), actor=Depends(current_user)):
        if not can_manage_people(actor):
            fail('当前角色无权查看部门管理', 403)
        query = select(Department).order_by(Department.sort_order, Department.id)
        if not has_role(actor, 'admin'):
            query = query.where(Department.id.in_(managed_department_ids(actor)))
        rows = list(db.scalars(query))
        directory = department_directory_stats(db, rows, actor)
        return [department_out(db, department, actor, directory) for department in rows]

    def save_department(db, actor, data, department=None):
        write_lock(db, actor); admin_only(actor)
        ensure_writable()
        if department and data.revision != department.revision:
            fail('部门已被修改，请刷新后重试')
        name = data.name.strip()
        if not name:
            fail('请填写部门名称', 422)
        conflict = find_department(db, name)
        if not conflict:
            other_alias = next((a for a in db.scalars(select(DepartmentAlias)) if name_key(a.name) == name_key(name)), None)
            if other_alias:
                conflict = db.get(Department, other_alias.department_id)
        if conflict and (not department or conflict.id != department.id):
            fail('部门名称或历史名称已存在')
        parent, seen = data.parent_id, set()
        while parent is not None:
            if parent in seen or department and parent == department.id:
                fail('部门层级不能形成循环')
            seen.add(parent)
            item = db.get(Department, parent)
            if not item:
                fail('上级部门不存在', 404)
            if data.active and not item.active:
                fail('启用部门不能放在停用部门下')
            parent = item.parent_id
        if department and department.parent_id != data.parent_id:
            validate_department_move(db, department, data.parent_id)
        if department and department.active and not data.active:
            blocks = {
                '启用成员': db.scalar(select(func.count()).select_from(User).where(membership_filter(User, {department.id}), User.active == True)),
                '启用项目': db.scalar(select(func.count()).select_from(Project).where(Project.department_id == department.id, Project.active == True)),
                '启用子部门': db.scalar(select(func.count()).select_from(Department).where(Department.parent_id == department.id, Department.active == True)),
                '未完成部门审批': pending_tasks(db, department=department),
            }
            if any(blocks.values()):
                fail('请先处理后再停用部门：' + '、'.join(f'{k} {v} 项' for k, v in blocks.items() if v))
        before = {'name': department.name, 'active': department.active, 'description': department.description,
                  'parent_id': department.parent_id} if department else {}
        created = department is None
        if created:
            department = Department(name=name)
            db.add(department); db.flush()
        old_name = department.name
        for label in {old_name, name}:
            if not db.get(DepartmentAlias, label):
                db.add(DepartmentAlias(name=label, department_id=department.id))
        department.name, department.parent_id, department.active = name, data.parent_id, data.active
        department.description, department.sort_order = data.description, data.sort_order
        department.revision += 1
        for key in ('organization_hierarchy', 'organization_paths', 'organization_member_counts'):
            db.info.pop(key, None)
        if old_name != name:
            db.info.pop('organization_paths', None)
            for model in (User, Project):
                for row in db.scalars(select(model).where(model.department_id == department.id)):
                    row.department = name
        audit.record(db, actor, 'create' if created else 'update', 'department', department, before=before)
        protect_pending_tasks(db); db.commit()
        return department_out(db, department, actor)

    @app.post('/api/departments')
    def create_department(data: DepartmentIn, db: Session = Depends(get_db), actor=Depends(current_user)):
        return save_department(db, actor, data)

    @app.patch('/api/departments/{department_id}')
    def update_department(department_id: int, data: DepartmentIn, db: Session = Depends(get_db), actor=Depends(current_user)):
        department = db.get(Department, department_id)
        if not department:
            fail('部门不存在', 404)
        return save_department(db, actor, data, department)

    @app.get('/api/departments/{department_id}/deletion-impact')
    def department_deletion_impact(department_id: int, db: Session = Depends(get_db), actor=Depends(current_user)):
        admin_only(actor)
        department = db.get(Department, department_id)
        if not department:
            fail('部门不存在', 404)
        return deletion_impact(db, 'department', department)

    @app.delete('/api/departments/{department_id}')
    def delete_department(department_id: int, revision: int, db: Session = Depends(get_db), actor=Depends(current_user)):
        write_lock(db, actor); admin_only(actor); ensure_writable()
        department = db.get(Department, department_id)
        if not department:
            fail('部门不存在', 404)
        if revision != department.revision:
            fail('部门已被修改，请重新检查后删除')
        impact = deletion_impact(db, 'department', department)
        if not impact['can_delete']:
            fail('请先处理关联后再删除部门：' + '、'.join(f"{b['label']} {b['count']} 项" for b in impact['blockers']))
        audit.record(db, actor, 'delete', 'department', department, before=audit.snapshot(db, 'department', department), after={})
        db.execute(delete(DepartmentAlias).where(DepartmentAlias.department_id == department_id))
        db.delete(department)
        protect_pending_tasks(db); db.commit()
        return {'message': '部门已删除', 'id': department_id}

    @app.put('/api/departments/{department_id}/managers')
    def managers(department_id: int, data: ManagersIn, db: Session = Depends(get_db), actor=Depends(current_user)):
        write_lock(db, actor, data.user_ids); admin_only(actor)
        ensure_writable()
        department = require_department(db, department_id)
        if data.revision != department.revision:
            fail('部门已被修改，请刷新后重试')
        ids = set(data.user_ids)
        people = list(db.scalars(select(User).where(User.id.in_(ids))))
        if len(people) != len(ids) or any(not p.active or department.id not in membership_ids(p) for p in people):
            fail('负责人必须是本部门已启用的成员')
        before = audit.snapshot(db, 'department', department)
        existing = {m.user_id: m for m in department.managers}
        department.managers = [existing.get(uid) or DepartmentManager(user_id=uid) for uid in sorted(ids)]
        department.approval_mode = 'designated_managers'; department.revision += 1
        audit.record(db, actor, 'members', 'department', department, before=before)
        protect_pending_tasks(db); db.commit(); db.expire_all()
        return department_out(db, db.get(Department, department_id), actor)

    @app.get('/api/departments/{department_id}/positions')
    def positions(department_id: int, db: Session = Depends(get_db), actor=Depends(current_user)):
        if not has_role(actor, 'admin') and (not is_department_manager(actor, department_id)):
            fail('无权查看该部门岗位', 403)
        department = db.get(Department, department_id)
        if not department:
            fail('部门不存在', 404)
        directory = department_directory_stats(db, [department], actor)
        return [position_out(db, p, actor, directory, department_id) for p in directory['positions'][department_id]]

    def save_position(db, actor, department_id, data, position=None):
        write_lock(db, actor); admin_only(actor); require_department(db, department_id)
        if position:
            db.refresh(position, with_for_update=True)
        ensure_writable()
        if position and data.revision != position.revision:
            fail('岗位已被修改，请刷新后重试')
        name = data.name.strip()
        if not name:
            fail('请填写岗位名称', 422)
        validate_position_name(db, department_id, name, position)
        roles = sorted({canonical_role(r) for r in data.roles})
        if roles:
            fail('部门岗位不再绑定旧业务角色，请在项目中指定岗位人员；部门负责人仍在部门内任命', 422)
        before = {'name': position.name, 'active': position.active, 'roles': [r.role for r in position.role_grants],
                  'manager_assignable': position.manager_assignable} if position else {}
        created = position is None
        if created:
            position = DepartmentPosition(department_id=department_id, name=name)
            db.add(position); db.flush()
        position.name, position.active = name, data.active
        position.manager_assignable = data.manager_assignable and set(roles) <= DELEGATABLE_ROLES
        position.revision += 1
        old = {r.role: r for r in position.role_grants}
        position.role_grants = [old.get(r) or DepartmentPositionRole(role=r) for r in roles]
        audit.record(db, actor, 'create' if created else 'update', 'department_position', position, before=before)
        protect_pending_tasks(db); db.commit(); db.expire_all()
        return position_out(db, db.get(DepartmentPosition, position.id), actor)

    @app.post('/api/departments/{department_id}/positions')
    def create_position(department_id: int, data: PositionIn, db: Session = Depends(get_db), actor=Depends(current_user)):
        return save_position(db, actor, department_id, data)

    @app.patch('/api/department-positions/{position_id}')
    def update_position(position_id: int, data: PositionIn, db: Session = Depends(get_db), actor=Depends(current_user)):
        position = db.get(DepartmentPosition, position_id)
        if not position:
            fail('岗位不存在', 404)
        return save_position(db, actor, position.department_id, data, position)

    @app.get('/api/department-positions/{position_id}/deletion-impact')
    def position_deletion_impact(position_id: int, db: Session = Depends(get_db), actor=Depends(current_user)):
        admin_only(actor)
        position = db.get(DepartmentPosition, position_id)
        if not position:
            fail('岗位不存在', 404)
        return deletion_impact(db, 'position', position)

    @app.delete('/api/department-positions/{position_id}')
    def delete_position(position_id: int, revision: int, db: Session = Depends(get_db), actor=Depends(current_user)):
        write_lock(db, actor); admin_only(actor); ensure_writable()
        position = db.get(DepartmentPosition, position_id)
        if not position:
            fail('岗位不存在', 404)
        db.refresh(position, with_for_update=True)
        if revision != position.revision:
            fail('岗位已被修改，请重新检查后删除')
        impact = deletion_impact(db, 'position', position)
        if not impact['can_delete']:
            fail('请先解除岗位分配后再删除岗位：' + '、'.join(f"{b['label']} {b['count']} 项" for b in impact['blockers']))
        audit.record(db, actor, 'delete', 'department_position', position, before=audit.snapshot(db, 'department_position', position), after={})
        db.delete(position)
        protect_pending_tasks(db); db.commit()
        return {'message': '岗位已删除', 'id': position_id}

    @app.put('/api/users/{user_id}/organization')
    def update_membership(user_id: int, data: MembershipIn, db: Session = Depends(get_db), actor=Depends(current_user)):
        people = write_lock(db, actor, [user_id])
        person = people.get(user_id)
        if not person:
            fail('成员不存在', 404)
        before = member_snapshot(person)
        assign_organization(db, actor, person, data.department_id, data.position_ids, data.revision, data.set_primary)
        audit.record(db, actor, 'members', 'user', person, before=before, after=member_snapshot(person))
        protect_pending_tasks(db); db.commit(); db.expire_all()
        from .main import user_out
        return user_out(db.get(User, user_id))

    @app.delete('/api/users/{user_id}/departments/{department_id}')
    def remove_membership(user_id: int, department_id: int, revision: int,
                          db: Session = Depends(get_db), actor=Depends(current_user)):
        person = write_lock(db, actor, [user_id]).get(user_id)
        admin_only(actor); ensure_writable()
        if not person:
            fail('成员不存在', 404)
        if person.organization_revision != revision:
            fail('成员组织信息已被修改，请刷新后重试')
        if department_id not in membership_ids(person):
            fail('成员不属于该部门', 404)
        before = member_snapshot(person)
        person.position_grants = [g for g in person.position_grants if g.department_id != department_id]
        person.department_memberships = [m for m in person.department_memberships if m.department_id != department_id]
        if person.department_id == department_id:
            # 旧单部门字段只保留仍有效的关联，防止启动回填恢复已明确移出的部门。
            remaining = sorted(membership_ids(person) - {department_id})
            department = db.get(Department, remaining[0]) if remaining else None
            person.department_id = department.id if department else None
            person.department = department.name if department else ''
            person.organization_department = department
        for row in db.scalars(select(DepartmentManager).where(DepartmentManager.user_id == person.id, DepartmentManager.department_id == department_id)):
            db.delete(row)
        person.organization_revision += 1
        audit.record(db, actor, 'members', 'user', person, before=before, after=member_snapshot(person))
        protect_pending_tasks(db); db.commit(); db.expire_all()
        from .main import user_out
        return user_out(db.get(User, user_id))

    @app.get('/api/users/{user_id}/organization-impact')
    def membership_impact(user_id: int, db: Session = Depends(get_db), actor=Depends(current_user)):
        person = db.get(User, user_id)
        if not person:
            fail('成员不存在', 404)
        if not may_manage_member(actor, person):
            fail('无权查看该成员的组织调整影响', 403)
        return {'pending_tasks': pending_tasks(db, [user_id]),
                'project_assignments': db.scalar(select(func.count()).select_from(ProjectMember).where(ProjectMember.user_id == user_id)),
                'is_department_manager': is_department_manager(person)}
