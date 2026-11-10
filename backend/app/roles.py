"""Role catalogue and scoped responsibility rules.

Role ids are data, so the catalogue keeps a small compatibility layer for
older installations. Authorization always uses ``canonical_role`` while
responses can continue to expose an existing grant until the optional
v1.0.1 data migration is applied.
"""

ROLES = {
    # 基础与系统（保持原有角色）
    'admin': '管理员', 'applicant': '申请人',
    'license_manager': 'License 管理者', 'knowledge_admin': '知识库管理员',
    # 审批与执行
    'department_head': '部门负责人', 'ops_head': '运维负责人',
    'manager': '管理者', 'operator': '运维人员', 'tester': '测试人员',
    'commercial_head': '商业化负责人', 'project_manager_head': '项目经理负责人',
    'technical_head': '技术负责人', 'agent_development_head': '智能体开发负责人',
    # 项目与业务
    'sales_presales': '销售/售前', 'project_manager': '项目经理',
    'agent_developer': '智能体开发', 'product_developer': '产品开发',
}

ROLE_GROUPS = {
    'admin': '基础与系统', 'applicant': '基础与系统',
    'license_manager': '基础与系统', 'knowledge_admin': '基础与系统',
    'department_head': '审批与执行', 'ops_head': '审批与执行',
    'manager': '审批与执行', 'operator': '审批与执行', 'tester': '审批与执行',
    'commercial_head': '审批与执行', 'project_manager_head': '审批与执行',
    'technical_head': '审批与执行', 'agent_development_head': '审批与执行',
    'sales_presales': '项目与业务', 'project_manager': '项目与业务',
    'agent_developer': '项目与业务', 'product_developer': '项目与业务',
}

# Existing ids are accepted during transition and omitted from /roles so new
# assignments use the new business vocabulary.
ROLE_ALIASES = {
    'leader': 'manager', 'project_owner': 'project_manager',
    'project_director': 'project_manager_head', 'sales_head': 'commercial_head',
    'sales_director': 'commercial_head', 'development_head': 'technical_head',
}

MANAGER_ROLE_SCOPE = {
    'commercial_head': {'sales_presales'},
    'project_manager_head': {'project_manager'},
    'technical_head': {'product_developer'},
    'agent_development_head': {'agent_developer'},
}
DEPARTMENT_LEAD_ROLES = set(MANAGER_ROLE_SCOPE)
# 仅保留名称与别名用于历史审批；这些旧职能角色不再授予当前组织管理能力。
RETIRED_HEAD_ROLES = set(MANAGER_ROLE_SCOPE)
DEPARTMENT_MEMBER_ROLES = {'applicant', 'ops_head', 'manager', 'operator', 'tester', 'sales_presales', 'project_manager', 'agent_developer', 'product_developer'}
SYSTEM_ROLES = {'admin', 'applicant', 'license_manager', 'knowledge_admin'}
# Project participation remains independent of catalogue grouping. This also
# preserves existing applicant, approver and operator project assignments.
PROJECT_ROLES = {'sales', 'presales', 'project_manager', 'applicant', 'technical_lead', 'agent_developer', 'ops_head', 'operator', 'tester'}
PROJECT_MEMBER_ROLES = set(PROJECT_ROLES)
FLOW_ROLES = set(PROJECT_ROLES) | {'department_head'}


def canonical_role(role: str | None) -> str:
    return ROLE_ALIASES.get(role, role) if isinstance(role, str) else ''


def role_aliases(role: str) -> set[str]:
    canonical = canonical_role(role)
    return {key for key, value in ROLE_ALIASES.items() if value == canonical} | {canonical}


def direct_role_ids(user):
    """Return stored grants, preserving legacy ids for audit compatibility."""
    values = [x.role for x in user.role_grants] if user.role_grants else [user.role]
    return list(dict.fromkeys(values))


def member_departments(user):
    """All memberships, retaining primary-department compatibility for old rows."""
    departments = {m.department_id: m.department for m in user.department_memberships}
    primary = getattr(user, 'organization_department', None)
    if primary:
        departments[primary.id] = primary
    return departments


def managed_department_ids(user):
    if not user.active:
        return set()
    return {key for key, department in member_departments(user).items() if department.active and
            any(m.user_id == user.id for m in department.managers)}


def is_department_manager(user, department_id=None):
    ids = managed_department_ids(user)
    return bool(ids) if department_id is None else department_id in ids


def role_ids(user):
    """Union independent sources across memberships; moving never revokes a source."""
    values = [r for r in direct_role_ids(user) if canonical_role(r) not in RETIRED_HEAD_ROLES | {'department_head'}]
    departments = member_departments(user)
    for grant in user.position_grants:
        position = grant.position
        department = departments.get(grant.department_id)
        if position.active and position.department.active and department and department.active:
            values.extend(r.role for r in position.role_grants if canonical_role(r.role) not in RETIRED_HEAD_ROLES | {'department_head'})
    if is_department_manager(user):
        values.append('department_head')
    return list(dict.fromkeys(values))


def can_manage_people(user):
    return has_role(user, 'admin') or is_department_manager(user)


def effective_role_ids(user):
    return list(dict.fromkeys(canonical_role(role) for role in role_ids(user)))


def stored_role_ids(user):
    """Equivalent database values for SQL scopes, including legacy grants."""
    return set().union(*(role_aliases(role) for role in role_ids(user)))


def has_role(user, *roles):
    expected = {canonical_role(role) for role in roles}
    return bool(set(effective_role_ids(user)) & expected)


def managed_roles(user):
    # 旧职能角色不再直接管理平台角色或项目成员，部门岗位由组织服务校验。
    return set()


def role_name(role: str) -> str:
    return {'sales':'销售','presales':'售前','technical_lead':'技术负责人'}.get(role, ROLES.get(canonical_role(role), role))


def has_department_role(user, role):
    """Legacy task compatibility only; new routing uses explicit appointments."""
    stored = {canonical_role(r) for r in direct_role_ids(user)}
    return has_legacy_approval_role(user, role) or (
        canonical_role(role) == 'department_head' and bool(stored & RETIRED_HEAD_ROLES))


def has_legacy_approval_role(user, role):
    stored = {canonical_role(r) for r in direct_role_ids(user)}
    return has_role(user, role) or canonical_role(role) in stored & (RETIRED_HEAD_ROLES | {'department_head'})


def retained_snapshot_roles(user, roles):
    legacy = {canonical_role(r) for r in direct_role_ids(user)} & (RETIRED_HEAD_ROLES | {'department_head'})
    return {canonical_role(role) for role in roles} <= set(effective_role_ids(user)) | legacy


def role_catalog():
    return [{
        'id': role, 'name': name, 'group': ROLE_GROUPS[role],
        'project_role': role in PROJECT_ROLES,
        'project_member_role': role in PROJECT_MEMBER_ROLES,
        'managed_roles': sorted(MANAGER_ROLE_SCOPE.get(role, set())),
        'managed_by': [],
        'department_scoped': role in DEPARTMENT_LEAD_ROLES,
        'assignment_source': 'department' if role == 'department_head' else 'direct' if role in SYSTEM_ROLES else 'position',
    } for role, name in ROLES.items() if role in SYSTEM_ROLES | {'department_head'}]
