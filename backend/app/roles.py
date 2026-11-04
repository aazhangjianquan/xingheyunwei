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
SYSTEM_ROLES = {'admin', 'applicant', 'license_manager', 'knowledge_admin'}
# Project participation remains independent of catalogue grouping. This also
# preserves existing applicant, approver and operator project assignments.
PROJECT_ROLES = set(ROLES) - {'admin', 'license_manager', 'knowledge_admin'}
PROJECT_MEMBER_ROLES = set(PROJECT_ROLES)
FLOW_ROLES = set(PROJECT_ROLES)


def canonical_role(role: str | None) -> str:
    return ROLE_ALIASES.get(role, role) if isinstance(role, str) else ''


def role_aliases(role: str) -> set[str]:
    canonical = canonical_role(role)
    return {key for key, value in ROLE_ALIASES.items() if value == canonical} | {canonical}


def role_ids(user):
    """Return stored grants, preserving legacy ids for audit compatibility."""
    values = [x.role for x in user.role_grants] if user.role_grants else [user.role]
    return list(dict.fromkeys(values))


def effective_role_ids(user):
    return list(dict.fromkeys(canonical_role(role) for role in role_ids(user)))


def stored_role_ids(user):
    """Equivalent database values for SQL scopes, including legacy grants."""
    return set().union(*(role_aliases(role) for role in role_ids(user)))


def has_role(user, *roles):
    expected = {canonical_role(role) for role in roles}
    return bool(set(effective_role_ids(user)) & expected)


def managed_roles(user):
    result = set()
    for role in effective_role_ids(user):
        result.update(MANAGER_ROLE_SCOPE.get(role, set()))
    return result


def role_name(role: str) -> str:
    return ROLES.get(canonical_role(role), role)


def has_department_role(user, role):
    # A functional head is a department head for department approval routing,
    # without receiving global administration or another function's grants.
    return has_role(user, role) or (
        canonical_role(role) == 'department_head' and has_role(user, *DEPARTMENT_LEAD_ROLES)
    )


def retained_snapshot_roles(user, roles):
    return {canonical_role(role) for role in roles} <= set(effective_role_ids(user))


def role_catalog():
    managers = {}
    for manager, managed in MANAGER_ROLE_SCOPE.items():
        for target in managed:
            managers.setdefault(target, []).append(manager)
    return [{
        'id': role, 'name': name, 'group': ROLE_GROUPS[role],
        'project_role': role in PROJECT_ROLES,
        'project_member_role': role in PROJECT_MEMBER_ROLES,
        'managed_roles': sorted(MANAGER_ROLE_SCOPE.get(role, set())),
        'managed_by': sorted(managers.get(role, [])),
        'department_scoped': role in DEPARTMENT_LEAD_ROLES,
    } for role, name in ROLES.items()]
