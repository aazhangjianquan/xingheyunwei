"""Versioned acyclic workflows. Caller owns the transaction and business-row lock.

Definitions are trees of ordered steps. A branch picks one ordered arm; all arms
join the following step. Only the root may contain the final execution step.
Snapshots are immutable; pending task assignment changes have a separate history.
"""
import json
from fastapi import HTTPException
from sqlalchemy import select, func
from .models import (User, Approval, Project, ProjectMember, WorkflowTemplate,
                     WorkflowVersion, WorkflowInstance, WorkflowNode, now)
from .roles import (ROLES, FLOW_ROLES, PROJECT_ROLES, has_role, role_ids,
                    canonical_role, role_aliases, stored_role_ids, managed_roles,
                    has_department_role, retained_snapshot_roles, role_name)

KINDS = {'resource', 'change', 'delivery', 'license'}
FIELDS = {
    'resource': {'environment', 'resource_type', 'department'},
    'change': {'environment', 'change_type', 'department'},
    'delivery': {'environment', 'cloud_type', 'deployment_mode', 'compute_type', 'accelerator', 'department'},
    'license': {'product', 'customer', 'department'},
}

def dump(value):
    return json.dumps(value, ensure_ascii=False)


def fail(message, code=400):
    raise HTTPException(code, message)


def normalize_definition(definition):
    """Adapt an old definition for editing/resolution; stored history is immutable."""
    result = json.loads(dump(definition))
    def walk(steps):
        if not isinstance(steps, list):
            return
        for node in steps:
            if not isinstance(node, dict):
                continue
            if node.get('name') in {'领导', '领导审批'}:
                node['name'] = node['name'].replace('领导', '管理者')
            source = node.get('assignee')
            if isinstance(source, dict) and isinstance(source.get('role'), str):
                source['role'] = canonical_role(source['role'])
            for arm in node.get('branches', []) if isinstance(node.get('branches', []), list) else []:
                if isinstance(arm, dict):
                    walk(arm.get('steps'))
    if isinstance(result, dict):
        walk(result.get('steps'))
    return result


def owner_id(kind, obj):
    return obj.applicant_id if kind in {'resource', 'license'} else obj.owner_id


def validate(kind, definition):
    if kind not in KINDS or not isinstance(definition, dict):
        fail('流程类型或定义无效')
    steps = definition.get('steps')
    ids = set()
    count = 0
    def walk(items, depth=0):
        nonlocal count
        if not isinstance(items, list) or len(items) > 50 or depth > 6:
            fail('节点列表无效，最多 6 层分支、100 个节点')
        for node in items:
            count += 1
            if count > 100 or not isinstance(node, dict):
                fail('流程过大或节点无效')
            key = node.get('id')
            if not isinstance(key, str) or not key or len(key) > 60 or key in ids:
                fail('每个节点需要唯一标识（不超过 60 字符）')
            ids.add(key)
            if not isinstance(node.get('name'), str) or not node['name'].strip() or len(node['name']) > 150:
                fail('请填写节点名称（不超过 150 字符）')
            kind_ = node.get('type')
            if kind_ == 'branch':
                arms = node.get('branches')
                if not isinstance(arms, list) or not 2 <= len(arms) <= 12 or not all(isinstance(a, dict) for a in arms):
                    fail(f"{node['name']}：至少需要一个条件分支和一个默认分支")
                if sum(bool(a.get('default')) for a in arms) != 1 or not arms[-1].get('default'):
                    fail(f"{node['name']}：必须有且仅有一个默认分支，并放在最后")
                arm_ids = set()
                for arm in arms:
                    if not arm.get('id') or arm['id'] in arm_ids or not arm.get('name'):
                        fail('分支标识需唯一，并填写名称')
                    arm_ids.add(arm['id'])
                    if not arm.get('default'):
                        if arm.get('match') not in {'all', 'any'} or not arm.get('conditions'):
                            fail('条件分支需要设置全部/任一满足及条件')
                        if not isinstance(arm['conditions'], list) or len(arm['conditions']) > 30 or not all(isinstance(c, dict) for c in arm['conditions']):
                            fail('分支条件必须为列表，最多 30 项')
                        for c in arm['conditions']:
                            if c.get('field') not in FIELDS[kind] or c.get('op') not in {'eq', 'in', 'not_in'}:
                                fail('条件字段或操作不受支持')
                            if c['op'] == 'eq' and not isinstance(c.get('value'), str):
                                fail('等于条件需要文本值')
                            if c['op'] != 'eq' and (not isinstance(c.get('value'), list) or not c['value'] or not all(isinstance(v, str) for v in c['value'])):
                                fail('属于/不属于条件需要非空文本列表')
                    walk(arm.get('steps'), depth + 1)
            elif kind_ in {'approval', 'execution', 'license_approval'}:
                if kind == 'license' and kind_ == 'execution':
                    fail('License 流程应以最终审批结束')
                if kind_ == 'license_approval':
                    if kind != 'license' or depth or node is not steps[-1]:
                        fail('License 最终审批必须是整个流程最后一个节点')
                    source = node.get('assignee', {})
                    if not isinstance(source, dict) or source.get('source') != 'fixed' or not isinstance(source.get('user_ids'), list) or not source['user_ids']:
                        fail('License 最终审批至少选择一位固定审批人')
                if kind_ == 'execution' and (depth or node is not steps[-1]):
                    fail('运维执行/回填必须是整个流程最后一个节点')
                if node.get('mode') not in {'all', 'any', 'quorum'}:
                    fail('审批方式必须为全部同意、任意一人或任意 N 人同意')
                source = node.get('assignee', {})
                if not isinstance(source, dict):
                    fail('处理人配置无效')
                if source.get('source') not in {'fixed', 'project_role', 'department_role'}:
                    fail('处理人来源无效')
                if source['source'] == 'fixed':
                    if not isinstance(source.get('user_ids'), list) or not source['user_ids'] or not all(type(i) is int and i > 0 for i in source['user_ids']):
                        fail('固定成员至少选择一人')
                elif source.get('source') == 'project_role' and canonical_role(source.get('role')) not in PROJECT_ROLES:
                    fail('请选择有效项目岗位')
                elif source.get('source') == 'department_role' and canonical_role(source.get('role')) not in FLOW_ROLES:
                    fail('请选择有效部门岗位')
                if kind_ == 'execution' and (source.get('source') != 'fixed' and canonical_role(source.get('role')) != 'operator'):
                    fail('执行节点的岗位必须为运维人员')
                if node.get('mode') == 'quorum':
                    required_count = node.get('required_count')
                    if type(required_count) is not int or required_count < 1:
                        fail('任意 N 人同意必须填写正整数人数')
                    if source.get('source') == 'fixed' and required_count > len(source.get('user_ids', [])):
                        fail('任意 N 人同意的人数不能超过已选择的固定成员数')
                elif 'required_count' in node and node.get('required_count') not in (None, ''):
                    if type(node.get('required_count')) is not int or node.get('required_count') < 1:
                        fail('审批人数阈值必须为正整数')
            else:
                fail('不支持的节点类型')
    final_type = 'license_approval' if kind == 'license' else 'execution'
    if not isinstance(steps, list) or not steps or not isinstance(steps[-1], dict) or steps[-1].get('type') != final_type:
        fail('License 流程必须以最终审批结束' if kind == 'license' else '流程必须以运维执行/回填节点结束')
    walk(steps)
    return {'valid': True, 'node_count': count}


def project_scope(query, user, management=False):
    """Project pickers share the same current membership and role restrictions."""
    if has_role(user, 'admin'):
        return query
    ids = select(ProjectMember.project_id).where(
        ProjectMember.user_id == user.id,
        ProjectMember.role.in_(stored_role_ids(user))
    )
    if management and managed_roles(user) and user.department.strip():
        return query.where((Project.id.in_(ids)) | (Project.department == user.department))
    return query.where(Project.id.in_(ids))


def project_manage_roles(project, user, members):
    if has_role(user, 'admin'):
        return set(PROJECT_ROLES)
    roles = managed_roles(user) if user.department.strip() and user.department == project.department else set()
    if any(m.user_id == user.id and canonical_role(m.role) == 'project_manager' and has_role(user, m.role) for m in members):
        roles |= set(PROJECT_ROLES) - {'project_manager'}
    return roles


def project_access(db, project_id, user, manage=False):
    project = db.get(Project, project_id)
    if not project:
        fail('项目不存在', 404)
    members = db.scalars(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)).all()
    eligible = [m for m in members if has_role(user, canonical_role(m.role))]
    authorized = bool(project_manage_roles(project, user, members)) if manage else bool(eligible)
    if not has_role(user, 'admin') and not authorized:
        fail('无权管理或申请此项目', 403)
    return project


def resolve(db, kind, definition, data, owner, project_id, check_access=True):
    definition = normalize_definition(definition)
    validate(kind, definition)
    project = project_access(db, project_id, owner) if check_access else db.get(Project, project_id)
    if not project or not project.active:
        fail('关联项目不存在或已停用', 409)
    # Administrators apply across projects without acquiring each project's
    # membership or adopting its staff department in their personal profile.
    # Their business department comes from the chosen project; approval still
    # requires an eligible person other than the applicant.
    department_source = 'project' if has_role(owner, 'admin') else 'applicant'
    department = project.department if department_source == 'project' else owner.department
    context = {k: data.get(k, '') for k in FIELDS[kind]}
    context['department'] = department
    path, branches = [], []
    def walk(steps):
        for node in steps:
            if node['type'] == 'branch':
                chosen = None
                for arm in node['branches']:
                    matches = []
                    for c in arm.get('conditions', []):
                        value = context.get(c['field'], '')
                        matches.append(value == c['value'] if c['op'] == 'eq' else (value in c['value'] if c['op'] == 'in' else value not in c['value']))
                    if chosen is None and (arm.get('default') or (all(matches) if arm.get('match') == 'all' else any(matches))):
                        chosen = arm['id']
                branches.append({'id': node['id'], 'name': node['name'], 'matched': chosen,
                                 'branches': [{'id': a['id'], 'name': a['name'], 'selected': a['id'] == chosen, 'steps': a['steps']} for a in node['branches']]})
                walk(next(a['steps'] for a in node['branches'] if a['id'] == chosen))
                continue
            source = node['assignee']
            if source['source'] == 'fixed':
                people = [db.get(User, uid) for uid in dict.fromkeys(source['user_ids'])]
            elif source['source'] == 'project_role':
                uids = db.scalars(select(ProjectMember.user_id).where(ProjectMember.project_id == project_id, ProjectMember.role.in_(role_aliases(source['role'])))).all()
                people = [db.get(User, uid) for uid in uids]
            else:
                if not department.strip():
                    fail(f"节点「{node['name']}」缺少审批归属部门，请先配置{'项目所属部门' if department_source == 'project' else '申请人所属部门'}", 409)
                people = db.scalars(select(User).where(User.department == department).order_by(User.id)).all()
                if canonical_role(source.get('role')) == 'department_head':
                    # Respect an explicitly assigned department head. Functional
                    # heads provide the fallback for departments without one,
                    # so upgrading does not add approvers to an existing route.
                    explicit = [person for person in people if person.active and person.id != owner.id and has_role(person, 'department_head')]
                    if explicit:
                        people = explicit
            members = []
            for person in people:
                if not person or not person.active or person.id == owner.id:
                    continue
                required = source.get('role') if source['source'] != 'fixed' else ('operator' if node['type'] == 'execution' else None)
                matches = has_department_role(person, required) if source['source'] == 'department_role' else has_role(person, required)
                if required and not matches:
                    continue
                members.append({'id': person.id, 'name': person.display_name, 'required_role': required,
                                'department': department if source['source'] == 'department_role' else None,
                                'roles': role_ids(person)})
            if not members:
                label = role_name(source['role']) if source.get('role') else '固定成员'
                if source['source'] == 'department_role':
                    scope = f'项目「{project.name}」所属部门「{department}」' if department_source == 'project' else f'申请人所属部门「{department}」'
                    fail(f"审批配置不完整：节点「{node['name']}」在{scope}没有合格处理人：{label}。请为该部门配置已启用且具有对应角色的成员；审批人不能是申请人本人", 409)
                if node['type'] == 'license_approval' and any(person and person.id == owner.id for person in people):
                    fail('License 最终审批人不能是申请人本人，请在审批流程中指定另一位已启用的成员并发布', 409)
                fail(f"节点「{node['name']}」没有合格处理人：{label}；请检查项目岗位、部门、角色和账号状态，申请人已排除", 409)
            required_count = node.get('required_count') if node.get('mode') == 'quorum' else None
            if required_count and required_count > len(members):
                fail(f"节点「{node['name']}」需要 {required_count} 人同意，但当前只有 {len(members)} 位合格处理人", 409)
            path.append({**node, 'required_count': required_count, 'assignees': members})
    walk(definition['steps'])
    return {'path': path, 'branches': branches, 'context': context, 'department_source': department_source,
            'project': {'id': project.id, 'name': project.name} if project else None, 'definition': definition}


def latest(db, kind):
    version = db.scalar(select(WorkflowVersion).where(WorkflowVersion.kind == kind).order_by(WorkflowVersion.version.desc()))
    if not version:
        fail('此业务尚未发布审批流程', 409)
    return version


def activate(db, kind, obj, node):
    node.status = 'pending'
    config = json.loads(node.config)
    obj.stage = 'operator' if node.node_type == 'execution' else config['id'][:30]
    if not config['assignees']:
        fail('旧版流程缺少后续处理人，请管理员检查升级前审批配置', 409)
    for person in config['assignees']:
        db.add(Approval(object_type=kind, object_id=obj.id, stage=obj.stage, node_run_id=node.id,
                        assignee_id=person['id'], assignee_snapshot=dump(person)))
    db.flush()


def start(db, kind, obj, owner):
    version = latest(db, kind)
    # Secrets must never be embedded in workflow snapshots or approval history.
    data = {c.name: getattr(obj, c.name) for c in obj.__table__.columns
            if c.name not in {'license_key_ciphertext', 'license_key_hash'}}
    snapshot = resolve(db, kind, json.loads(version.definition), data, owner, getattr(obj, 'project_id', None))
    round_ = (db.scalar(select(func.max(WorkflowInstance.round)).where(WorkflowInstance.object_type == kind, WorkflowInstance.object_id == obj.id)) or 0) + 1
    instance = WorkflowInstance(object_type=kind, object_id=obj.id, round=round_, version_id=version.id, snapshot=dump(snapshot))
    db.add(instance)
    db.flush()
    nodes = []
    for i, spec in enumerate(snapshot['path']):
        node = WorkflowNode(instance_id=instance.id, position=i, name=spec['name'], node_type=spec['type'], mode=spec['mode'], config=dump(spec))
        db.add(node)
        nodes.append(node)
    db.flush()
    obj.status = 'pending'
    activate(db, kind, obj, nodes[0])
    return instance


def eligible(user, task):
    if not user or not user.active or task.assignee_id != user.id:
        return False
    snap = json.loads(task.assignee_snapshot or '{}')
    # Fixed assignees also retain their grant at submission; removing an original
    # grant blocks stale tasks. Role-based assignees require the exact role.
    matches = has_department_role(user, snap['required_role']) if snap.get('department') and snap.get('required_role') else has_role(user, snap.get('required_role'))
    if snap.get('required_role') and not matches:
        return False
    if not snap.get('required_role') and snap.get('roles') and not retained_snapshot_roles(user, snap['roles']):
        return False
    return not snap.get('department') or user.department == snap['department']


def act(db, kind, obj, task_id, user, action, comment, fulfillment, plan='', rollback_plan=''):
    # All writers (including reassignment) lock the business row first, then task.
    task = db.scalar(select(Approval).where(Approval.id == task_id).with_for_update().execution_options(populate_existing=True))
    if not task or task.object_type != kind or task.object_id != obj.id:
        fail('审批任务不存在', 404)
    if task.action != 'pending' or obj.status not in {'pending', 'in_progress'}:
        fail('任务已处理或已关闭，请刷新', 409)
    if owner_id(kind, obj) == user.id or not eligible(user, task):
        fail('无权处理此任务；角色撤销或账号停用后需管理员转交', 403)
    node = db.scalar(select(WorkflowNode).where(WorkflowNode.id == task.node_run_id).with_for_update().execution_options(populate_existing=True))
    if not node or node.status != 'pending':
        fail('节点状态已变化', 409)
    instance = db.get(WorkflowInstance, node.instance_id)
    if node.node_type == 'execution' and action == 'approve' and not fulfillment.strip():
        fail('请填写运维执行/交付结果')
    if kind == 'change' and node.node_type == 'execution' and action == 'approve' and (not plan.strip() or not rollback_plan.strip()):
        fail('请在执行回填阶段填写实施方案和回滚方案')
    task.action, task.actor_id, task.comment, task.acted_at = action, user.id, comment, now()
    db.flush()
    siblings = db.scalars(select(Approval).where(Approval.node_run_id == node.id).with_for_update().execution_options(populate_existing=True)).all()
    if action == 'reject':
        node.status = 'rejected'
        obj.status = instance.status = 'rejected'
        for other in siblings:
            if other.action == 'pending':
                other.action = 'closed'
        for future in db.scalars(select(WorkflowNode).where(WorkflowNode.instance_id == instance.id, WorkflowNode.status == 'waiting').with_for_update()).all():
            future.status = 'closed'
    approved = sum(t.action == 'approve' for t in siblings)
    required = len(siblings) if node.mode == 'all' else 1 if node.mode == 'any' else int(json.loads(node.config).get('required_count') or 0)
    if action != 'reject' and node.mode == 'quorum' and required > len(siblings):
        fail('当前审批节点的同意人数阈值超过实际处理人数，请联系管理员修正流程', 409)
    if action != 'reject' and approved >= required:
        node.status = 'completed'
        for other in siblings:
            if other.action == 'pending':
                other.action = 'closed'
        if node.node_type == 'execution':
            if kind == 'resource': obj.fulfillment = fulfillment
            elif kind == 'change':
                obj.plan, obj.rollback_plan, obj.implementation = plan.strip(), rollback_plan.strip(), fulfillment.strip()
            else: obj.notes = (obj.notes + '\n' + fulfillment).strip()
            obj.status = instance.status = 'completed'
        elif node.node_type == 'license_approval':
            from .licenses import finalize_license
            finalize_license(obj, user)
            instance.status = 'completed'
        else:
            next_node = db.scalar(select(WorkflowNode).where(WorkflowNode.instance_id == instance.id, WorkflowNode.position > node.position).order_by(WorkflowNode.position).with_for_update())
            obj.status = instance.status = 'in_progress'
            activate(db, kind, obj, next_node)
    return task


def seed_v3(db, objects):
    """Idempotent grant backfill and default version publication; never infer projects."""
    from .models import UserRole
    from .workflows import FLOWS
    for user in db.scalars(select(User)).all():
        if not user.role_grants:
            user.role_grants = [UserRole(role=user.role)]
    db.flush()
    for kind, stages in FLOWS.items():
        if db.get(WorkflowTemplate, kind):
            continue
        steps = []
        for stage in stages:
            people = [p.id for p in db.scalars(select(User).order_by(User.id)).all() if p.active and has_role(p, stage)]
            from .models import WorkflowAssignment
            route = db.scalar(select(WorkflowAssignment).where(WorkflowAssignment.object_type == kind, WorkflowAssignment.stage == stage, WorkflowAssignment.department == ''))
            if route: people = [route.assignee_id]
            source = {'source': 'department_role', 'role': stage} if stage == 'department_head' else {'source': 'fixed', 'user_ids': people[:1]}
            steps.append({'id': stage, 'name': ROLES[stage] if stage != 'operator' else '运维执行/回填', 'type': 'execution' if stage == 'operator' else 'approval', 'mode': 'all', 'assignee': source})
        definition = dump({'steps': steps})
        db.add(WorkflowTemplate(kind=kind, draft=definition))
        db.add(WorkflowVersion(kind=kind, version=1, definition=definition))
    db.flush()
    # Each legacy record gets a historical instance. Existing tasks and actions
    # keep IDs/actors/timestamps; approved stages are never executed again.
    for kind, model in objects.items():
        if kind not in FLOWS:
            continue
        for obj in db.scalars(select(model)).all():
            if db.scalar(select(WorkflowInstance.id).where(WorkflowInstance.object_type == kind, WorkflowInstance.object_id == obj.id)):
                continue
            history = db.scalars(select(Approval).where(Approval.object_type == kind, Approval.object_id == obj.id).order_by(Approval.id)).all()
            inst = WorkflowInstance(object_type=kind, object_id=obj.id, round=1, snapshot=dump({'legacy': True, 'branches': [], 'path': []}), status=obj.status)
            db.add(inst); db.flush()
            stages = FLOWS[kind]
            current_stage = next((stage for stage in stages if stage == obj.stage),
                                 next((stage for stage in stages if canonical_role(stage) == canonical_role(obj.stage)), obj.stage))
            owner = db.get(User, owner_id(kind, obj))
            legacy_path = []
            for i, stage in enumerate(stages):
                tasks = [t for t in history if t.stage == stage or canonical_role(t.stage) == canonical_role(stage)]
                if not tasks and (obj.status in {'completed', 'rejected'} or i < stages.index(current_stage)):
                    # Missing legacy history stays explicitly marked as migrated.
                    status = 'closed' if obj.status == 'rejected' and i > stages.index(current_stage) else 'legacy_unrecorded'
                elif any(t.action == 'reject' for t in tasks): status = 'rejected'
                elif any(t.action == 'approve' for t in tasks): status = 'completed'
                elif any(t.action == 'pending' for t in tasks): status = 'pending'
                else: status = 'closed' if obj.status == 'rejected' else 'waiting'
                person = next((db.get(User, t.assignee_id) for t in tasks if t.assignee_id), None)
                if not person:
                    from .workflows import assignee
                    try: person = db.get(User, assignee(db, kind, stage, owner))
                    except HTTPException: person = None
                spec = {'id': stage, 'name': ROLES[stage], 'type': 'execution' if stage == 'operator' else 'approval', 'mode': 'all', 'assignee': {'source': 'fixed', 'user_ids': [person.id] if person else []},
                        'assignees': [{'id': person.id, 'name': person.display_name, 'required_role': stage, 'roles': role_ids(person), 'department': owner.department if stage == 'department_head' else None}] if person else []}
                legacy_path.append(spec)
                node = WorkflowNode(instance_id=inst.id, position=i, name=spec['name'], node_type=spec['type'], mode='all', config=dump(spec), status=status)
                db.add(node); db.flush()
                for t in tasks:
                    t.node_run_id = node.id
                    t.assignee_snapshot = dump(spec['assignees'][0] if spec['assignees'] else {})
            inst.snapshot = dump({'legacy': True, 'branches': [], 'path': legacy_path})
            db.flush()
    db.commit()
