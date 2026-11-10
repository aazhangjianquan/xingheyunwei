"""Human-readable business history, saved in the transaction it describes.

Only explicitly allowed business fields enter the change history. Request bodies,
credential plaintext, signer output and encrypted secrets are never serialized.
"""
import json
import hashlib
from datetime import date, datetime, time, timedelta, timezone

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, defer

from .database import get_db
from .models import AuditLog, Project, ProjectMember, User, WorkflowInstance, WorkflowNode, WikiSpace, WikiSpaceDepartment, WikiSpaceMember, Department
from .roles import ROLES, role_ids, role_name

TYPES = {'resource': '资源申请', 'change': '变更申请', 'delivery': '私有化交付',
         'department': '部门', 'department_position': '部门岗位',
         'license': 'License 申请', 'project': '项目', 'user': '成员',
         'wiki': '知识文章', 'wiki_space': '知识空间', 'workflow': '审批流程', 'settings': '系统配置', 'notification': '通知'}
ACTIONS = {'create': '创建', 'update': '修改', 'delete': '删除', 'restore': '恢复', 'resubmit': '修改并重新提交',
           'submit_review': '补充提交审批', 'approve': '审批通过', 'reject': '驳回',
           'complete': '完成执行', 'generate': '最终审批通过并生成 License',
           'transfer': '转交审批', 'members': '调整成员', 'credentials': '回填交付凭据',
           'enable': '启用', 'disable': '停用', 'publish': '发布', 'draft': '保存草稿',
           'retry': '重试发送', 'configure': '修改配置', 'view_secret': '查看交付凭据',
           'change_password': '修改本人密码', 'upload_attachment': '上传交付附件',
           'download_attachment': '下载交付附件', 'preview_attachment': '预览交付附件'}
LABELS = dict(
    parent_id='上级部门', approval_mode='负责人匹配方式', manager_assignable='允许负责人分配',
    application_department='本次申请部门', project='关联项目', name='名称', project_name='项目名称', customer='客户', product='授权产品',
    tenant_id='租户 ID', expire_time='授权到期时间', account_number='账号数', agent_number='Agent 数',
    purpose='申请用途', environment='环境', resource_type='资源类型', specification='资源规格',
    start_date='开始日期', end_date='结束日期', capacity_basis='容量依据', ports='端口', whitelist='白名单',
    change_type='变更类型', window_start='窗口开始', window_end='窗口结束', ops_owner_id='运维负责人',
    plan='实施方案', problem_description='变更问题说明', rollback_plan='回滚方案', cloud_type='云类型', deployment_mode='部署方式',
    compute_type='计算类型', accelerator='加速卡', network_notes='网络说明', db_whitelist='数据库白名单',
    security_group='安全组', account_validation='账号验证', notes='备注', status='单据状态',
    stage='当前环节', department='所属部门', active='账号 / 项目状态', members='岗位成员',
    username='用户名', display_name='姓名', roles='角色', feishu_open_id='飞书通知身份',
    password='密码', title='标题', category='分类', content='文章内容', comment='审批意见', attachment='附件', attachment_size='附件大小（字节）',
    handler='审批处理人', reason='转交原因', node='审批节点', db_type='数据库类型', host='地址',
    privilege='权限级别', credential='交付凭据', fulfillment='执行回填', revision='草稿修订号',
    version='发布版本', definition='流程定义', site_name='平台名称', public_base_url='平台地址',
    feishu_app_id='飞书 App ID', feishu_app_secret='飞书 App Secret',
    feishu_notifications_enabled='飞书通知', local_login_enabled='账号密码登录',
    feishu_login_enabled='飞书登录', feishu_tenant_key='飞书租户', database='数据库连接',
    feishu_task_login_enabled='飞书任务免密',
    access_scope='开放范围', include_subdepartments='包含下级部门', access_departments='开放部门', access_members='直接授权人员',
    key='空间标识', description='空间说明', owner_id='空间负责人', kind='空间类型',
    resource_catalog='资源类型与规格配置',
    reminder_enabled='到期提醒', reminder_contact='资源提醒运维人员',
)
FIELDS = {
    'department': 'name parent_id active description approval_mode',
    'department_position': 'name active manager_assignable',
    'resource': 'environment resource_type specification start_date end_date purpose capacity_basis ports whitelist status stage reminder_enabled',
    'change': 'name environment change_type window_start window_end ops_owner_id problem_description plan rollback_plan status stage',
    'delivery': 'name customer cloud_type deployment_mode environment compute_type accelerator network_notes db_whitelist security_group account_validation notes status stage',
    'license': 'customer product tenant_id expire_time account_number agent_number purpose status stage reminder_enabled',
    'project': 'name department active', 'user': 'username display_name department active',
    'wiki': 'title category status version',
    'wiki_space': 'key name description category kind owner_id access_scope include_subdepartments',
}
STATUS = {'pending': '待审批', 'in_progress': '处理中', 'completed': '已完成', 'generated': '已生成',
          'rejected': '已驳回', 'awaiting_workflow': '待补充审批'}
SECRET_FIELDS = {'password', 'feishu_app_secret', 'feishu_open_id', 'feishu_tenant_key', 'credential', 'fulfillment', 'problem_description', 'database', 'notes', 'content'}
BEIJING = timezone(timedelta(hours=8))


def snapshot(db, kind, obj):
    result = {key: getattr(obj, key) for key in FIELDS.get(kind, '').split()}
    if kind == 'wiki': result['_body_digest'] = hashlib.sha256(obj.content.encode()).hexdigest()
    if kind in {'resource', 'change', 'delivery', 'license'}:
        selected = db.get(Department, obj.application_department_id) if obj.application_department_id else None
        result['application_department'] = f'{selected.name} (#{selected.id})' if selected else '历史记录未选择'
        project = db.get(Project, obj.project_id) if obj.project_id else None
        result['project'] = f'{project.name} (#{project.id})' if project else getattr(obj, 'project_name', '')
        node_name = db.scalar(select(WorkflowNode.name).join(WorkflowInstance, WorkflowNode.instance_id == WorkflowInstance.id)
            .where(WorkflowInstance.object_type == kind, WorkflowInstance.object_id == obj.id,
                   WorkflowNode.status.in_(['pending', 'completed', 'rejected']))
            .order_by(WorkflowInstance.id.desc(), WorkflowNode.position.desc()).limit(1))
        if node_name:
            result['stage'] = node_name
    if kind == 'user':
        result.update(roles=sorted({role_name(r) for r in role_ids(obj)}),
                      feishu_open_id='已设置' if obj.feishu_open_id else '未设置')
    if kind == 'department':
        people = db.scalars(select(User).where(User.id.in_([m.user_id for m in obj.managers]))).all()
        result['members'] = sorted(f'{p.display_name}（{p.username}）' for p in people)
    if kind == 'department_position':
        result['roles'] = sorted(r.role for r in obj.role_grants)
    if kind == 'project':
        rows = db.execute(select(ProjectMember.user_id, ProjectMember.role, User.display_name, User.username)
                          .join(User, User.id == ProjectMember.user_id)
                          .where(ProjectMember.project_id == obj.id).order_by(ProjectMember.role, ProjectMember.user_id)).all()
        result['members'] = sorted({f'{role_name(r)}：{name}（{username}，#{uid}）' for uid, r, name, username in rows})
    if result.get('ops_owner_id'):
        person = db.get(User, result['ops_owner_id'])
        result['ops_owner_id'] = f'{person.display_name}（{person.username}）' if person else '未指定'
    if kind == 'wiki_space':
        result['access_departments'] = sorted(f'{name}（#{did}）' for did, name in db.execute(select(Department.id, Department.name).join(WikiSpaceDepartment, WikiSpaceDepartment.department_id == Department.id).where(WikiSpaceDepartment.space_id == obj.id)))
        result['access_members'] = sorted(f"{name}（{username}）：{ {'viewer': '只读', 'editor': '编辑'}.get(role, role)}" for name, username, role in db.execute(select(User.display_name, User.username, WikiSpaceMember.role).join(WikiSpaceMember, WikiSpaceMember.user_id == User.id).where(WikiSpaceMember.space_id == obj.id)))
    if kind == 'wiki_space' and result.get('owner_id'):
        person = db.get(User, result['owner_id'])
        result['owner_id'] = f'{person.display_name}（{person.username}）' if person else '未指定'
    return result


def safe_value(field, value):
    if field == 'access_scope': return {'all': '所有人', 'departments': '指定部门', 'members': '指定人员'}.get(value, value)
    if field == 'include_subdepartments': return '包含' if value else '不包含'
    if field == 'kind': return {'personal': '个人知识库', 'shared': '共享知识库'}.get(value, value)
    if field in SECRET_FIELDS:
        # Callers pass a marker; no raw value is retained, even accidentally.
        return '未设置' if value in (None, '', False, '未设置') else '已设置 / 已更新'
    if field == 'active':
        return '启用' if value else '停用'
    if field in {'status', 'stage'}:
        return STATUS.get(value, role_name(value))
    if field == 'expire_time' and value:
        return datetime.fromtimestamp(value / 1000, BEIJING).strftime('%Y-%m-%d %H:%M:%S（北京时间）')
    if isinstance(value, bool):
        return '开启' if value else '关闭'
    if isinstance(value, list):
        value = '\n'.join(str(v) for v in value)
    if value is None:
        return None
    value = str(value)
    return value if len(value) <= 8000 else value[:8000] + '…（内容过长，已截断）'


def encode_changes(changes):
    """Fit MySQL TEXT's byte limit even when many fields contain long Unicode."""
    payload = json.dumps(changes, ensure_ascii=False)
    while len(payload.encode('utf-8')) > 60000:
        candidates = [(len(str(change[side])), i, side) for i, change in enumerate(changes)
                      for side in ('before', 'after') if change[side] is not None]
        length, index, side = max(candidates)
        changes[index][side] = changes[index][side][:length // 2] + '…（内容过长，已截断）'
        payload = json.dumps(changes, ensure_ascii=False)
    return payload


def record(db, actor, action, kind, obj=None, *, before=None, after=None,
           target_id='', target_code='', target_name='', extra=None, status_code=200):
    """Caller commits; rolling back the business operation rolls back this entry."""
    before = before or {}
    after = snapshot(db, kind, obj) if after is None and obj is not None else (after or {})
    changes = []
    for field in dict.fromkeys([*before, *after]):
        if field in LABELS and before.get(field) != after.get(field):
            changes.append({'field': field, 'label': LABELS[field],
                            'before': safe_value(field, before.get(field)) if field in before else None,
                            'after': safe_value(field, after.get(field)) if field in after else None})
    for field, values in (extra or {}).items():
        if field in LABELS:
            changes.append({'field': field, 'label': LABELS[field],
                            'before': safe_value(field, values[0]) if values[0] is not None else None,
                            'after': safe_value(field, values[1]) if values[1] is not None else None})
    if kind == 'wiki' and before.get('_body_digest') != after.get('_body_digest'):
        changes.append({'field':'content', 'label':'文章内容', 'before':'已保存' if before else None,
                        'after':'正文已更新' if after else None})
    if action in {'update', 'members', 'configure', 'draft'} and not changes:
        return None  # Saving an unchanged form is not a modification.
    if obj is not None:
        target_id = str(obj.id)
        target_code = getattr(obj, 'code', '') or (obj.username if kind == 'user' else '')
        target_name = getattr(obj, 'name', None) or getattr(obj, 'project_name', None) or getattr(obj, 'title', None) or getattr(obj, 'display_name', '')
    target_code, target_name = str(target_code)[:80], str(target_name)[:180]
    identity = ' · '.join(p for p in (target_code, target_name) if p) or f'#{target_id}'
    context = db.info.get('audit_context', {})
    actor_name = context.get('actor_name', actor.display_name)
    subject = f'{TYPES[kind]}「{identity}」'
    verbs = {'create': '创建了', 'update': '修改了', 'delete': '删除了', 'resubmit': '修改并重新提交了',
             'submit_review': '补充提交了', 'reject': '驳回了', 'enable': '启用了', 'disable': '停用了',
             'publish': '发布了', 'draft': '保存了', 'configure': '修改了'}
    if action in verbs:
        description = f'{actor_name} {verbs[action]}{subject}'
    elif action == 'approve':
        description = f'{actor_name} 通过了{subject}的审批'
    elif action == 'generate':
        description = f'{actor_name} 完成了{subject}的最终审批，并生成 License'
    else:
        description = f'{actor_name} 对{subject}执行了「{ACTIONS[action]}」操作'
    entry = AuditLog(record_kind='business', actor_id=actor.id,
                     actor_name=context.get('actor_name', actor.display_name),
                     actor_username=context.get('actor_username', actor.username),
                     method=context.get('method', ''), path=context.get('path', '')[:255],
                     ip_address=context.get('ip_address', '')[:80], status_code=status_code,
                     action=action, target_type=kind, target_id=str(target_id)[:50],
                     target_code=target_code, target_name=target_name, summary=description[:600],
                     changes=encode_changes(changes))
    if kind in {'wiki', 'wiki_space'} and obj is not None:
        entry.wiki_space_id = obj.id if kind == 'wiki_space' else obj.space_id
    db.add(entry)
    return entry


def output(row, person=None, include_changes=False):
    name = row.actor_name or (person.display_name if person else '未登录 / 未知用户')
    username = row.actor_username or (person.username if person else '')
    result = {key: getattr(row, key) for key in ('id', 'record_kind', 'actor_id', 'method', 'path', 'action',
              'target_type', 'target_id', 'target_code', 'target_name', 'status_code', 'ip_address')}
    result.update(actor_name=name, actor_username=username,
                  action_label=ACTIONS.get(row.action, '访问接口' if row.method == 'GET' else '接口操作'),
                  target_label=TYPES.get(row.target_type, '接口访问'),
                  summary=row.summary or f'{name} {"访问" if row.method == "GET" else "调用"}了接口 {row.path}',
                  result='success' if row.status_code < 400 else 'failure',
                  created_at=(row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)).isoformat())
    if include_changes:
        result['changes'] = json.loads(row.changes or '[]')
    return result


def register_routes(app, current_user, require_role):
    def knowledge_visibility(user):
        from .knowledge import accessible_spaces
        return or_(AuditLog.target_type.not_in(['wiki', 'wiki_space']),
            AuditLog.wiki_space_id.in_(select(WikiSpace.id).where(accessible_spaces(user.id))))

    def admin(user=Depends(current_user)):
        require_role(user, 'admin')
        return user

    @app.get('/api/audit')
    def list_audit(record_kind: str = Query('business', pattern='^(business|request)$'),
                   q: str = Query('', max_length=100), actor: str = Query('', max_length=80),
                   target_type: str = '', action: str = '', result: str = Query('all', pattern='^(all|success|failure)$'),
                   from_date: date | None = None, to_date: date | None = None,
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   db: Session = Depends(get_db), user=Depends(admin)):
        if from_date and to_date and from_date > to_date:
            raise HTTPException(400, '开始日期不能晚于结束日期')
        if to_date == date.max:
            raise HTTPException(400, '结束日期超出可查询范围')
        query = select(AuditLog, User).outerjoin(User, User.id == AuditLog.actor_id).where(
            AuditLog.record_kind == record_kind, knowledge_visibility(user))
        actor_name = func.coalesce(func.nullif(AuditLog.actor_name, ''), User.display_name)
        actor_username = func.coalesce(func.nullif(AuditLog.actor_username, ''), User.username)
        if actor.strip():
            query = query.where(or_(*(field.contains(actor.strip(), autoescape=True) for field in
                                    (actor_name, actor_username))))
        if q.strip():
            query = query.where(or_(*(field.contains(q.strip(), autoescape=True) for field in
                                    (AuditLog.target_code, AuditLog.target_name, AuditLog.target_id, AuditLog.summary,
                                     actor_name, actor_username, AuditLog.path))))
        if target_type:
            query = query.where(AuditLog.target_type == target_type)
        if action:
            query = query.where(AuditLog.action == action)
        if result != 'all':
            query = query.where(AuditLog.status_code < 400 if result == 'success' else AuditLog.status_code >= 400)
        if from_date:
            query = query.where(AuditLog.created_at >= datetime.combine(from_date, time.min, BEIJING).astimezone(timezone.utc))
        if to_date:
            query = query.where(AuditLog.created_at < datetime.combine(to_date + timedelta(days=1), time.min, BEIJING).astimezone(timezone.utc))
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        rows = db.execute(query.options(defer(AuditLog.changes)).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                          .offset((page - 1) * page_size).limit(page_size)).all()
        return {'items': [output(row, person) for row, person in rows], 'total': total, 'page': page,
                'page_size': page_size, 'total_pages': total_pages,
                'types': [{'value': key, 'label': label} for key, label in TYPES.items()],
                'actions': [{'value': key, 'label': label} for key, label in ACTIONS.items()]}

    @app.get('/api/audit/{item_id}')
    def audit_detail(item_id: int, db: Session = Depends(get_db), user=Depends(admin)):
        row = db.scalar(select(AuditLog).where(AuditLog.id == item_id, knowledge_visibility(user)))
        if not row:
            raise HTTPException(404, '操作记录不存在')
        return output(row, db.get(User, row.actor_id) if row.actor_id else None, include_changes=True)
