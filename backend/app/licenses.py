"""License application visibility and key visibility are independent permissions."""
import hashlib
import secrets
from datetime import datetime, timezone
import time
from typing import Literal

from fastapi import BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import flow_engine as flow
from .database import get_db
from .models import Approval, LicenseRequest, Project, User, WorkflowInstance, WorkflowTemplate, now
from .roles import has_role
from .security import decrypt_secret, encrypt_secret
from .license_tool import generate_key
from . import audit as operation_audit
from . import reminders


class LicenseIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    project_id: int = Field(gt=0)
    project_name: str = Field(default='', max_length=150)
    customer: str = Field(min_length=1, max_length=180)
    product: str = Field(min_length=1, max_length=150)
    tenant_id: int = Field(ge=1, le=2147483647)
    expire_time: int = Field(gt=0, le=253402271999999)
    account_number: int = Field(default=-1, ge=-1, le=2147483647)
    agent_number: int = Field(default=-1, ge=-1, le=2147483647)
    purpose: str = Field(min_length=1, max_length=10000)
    reminder_enabled: bool = True


class LegacyReviewIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: int | None = Field(default=None, gt=0)


def application_project(db, project_id, user):
    if project_id is None:
        raise HTTPException(400, '请先选择关联项目，再提交审批')
    project = flow.project_access(db, project_id, user)
    if not project.active:
        raise HTTPException(409, '关联项目已停用，请选择启用的项目')
    return project


def can_manage(user):
    return has_role(user, 'admin', 'license_manager')


def visible(query, user):
    query = query.where(LicenseRequest.deleted_at.is_(None))
    if can_manage(user):
        return query
    assigned = select(Approval.object_id).where(Approval.object_type == 'license',
        or_(Approval.assignee_id == user.id, Approval.actor_id == user.id))
    return query.where(or_(LicenseRequest.applicant_id == user.id,
                          LicenseRequest.final_approver_id == user.id,
                          LicenseRequest.id.in_(assigned)))


def get_visible(db, item_id, user):
    item = db.scalar(visible(select(LicenseRequest).where(LicenseRequest.id == item_id), user))
    if item is None:
        raise HTTPException(404, '申请不存在或无权查看')
    return item


def can_view_key(item, user):
    # No administrator/manager bypass. Legacy keys remain sealed until reviewed.
    return bool(item.status == 'generated' and item.final_approver_id
                and user.id in {item.applicant_id, item.final_approver_id})


def license_out(item, names, user, include_key=False):
    fields = ('id', 'code', 'applicant_id', 'project_id', 'project_name', 'customer', 'product',
              'tenant_id', 'expire_time', 'account_number', 'agent_number', 'purpose', 'status',
              'stage', 'final_approver_id', 'generated_by', 'generated_at', 'created_at', 'updated_at',
              'reminder_enabled', 'reminder_revision')
    result = {name: getattr(item, name) for name in fields}
    result = {k: (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
              if isinstance(v, datetime) else v for k, v in result.items()}
    result.update(applicant_name=names.get(item.applicant_id, ''),
                  generated_by_name=names.get(item.generated_by, ''),
                  final_approver_name=names.get(item.final_approver_id, ''),
                  has_license_key=item.status == 'generated' and bool(item.license_key_ciphertext),
                  can_view_key=can_view_key(item, user),
                  can_delete=bool(user.id == item.applicant_id or can_manage(user)))
    if include_key:
        result['license_key'] = decrypt_secret(item.license_key_ciphertext) if can_view_key(item, user) and item.license_key_ciphertext else None
    return result


def names_for(db, rows):
    ids = {uid for row in rows for uid in (row.applicant_id, row.generated_by, row.final_approver_id) if uid}
    return dict(db.execute(select(User.id, User.display_name).where(User.id.in_(ids))).all()) if ids else {}


def license_detail_out(db, item, user):
    from .main import detail
    history = detail(db, 'license', item, user=user)
    return {**history, **license_out(item, names_for(db, [item]), user, include_key=True)}


def finalize_license(item, user):
    """Called only by the final approval node inside the locked approval transaction."""
    if item.expire_time <= int(time.time() * 1000):
        raise HTTPException(400, '申请的授权到期时间已过，请驳回后修改重提')
    if not item.license_key_ciphertext:
        key = generate_key(item.tenant_id, item.expire_time, item.account_number, item.agent_number)
        item.license_key_ciphertext = encrypt_secret(key)
        item.license_key_hash = hashlib.sha256(key.encode()).hexdigest()
        item.generated_by, item.generated_at = user.id, now()
    item.final_approver_id = user.id
    item.status, item.stage, item.updated_at = 'generated', 'completed', now()


def seed_license_workflow(db):
    """Create an unpublished draft; administrators explicitly choose the final reviewer."""
    if not db.get(WorkflowTemplate, 'license'):
        db.add(WorkflowTemplate(kind='license', draft=flow.dump({'steps': [
            {'id': 'license_department', 'name': '部门审核', 'type': 'approval', 'mode': 'all',
             'assignee': {'source': 'department_role', 'role': 'department_head'}},
            {'id': 'license_final', 'name': 'License 最终审批', 'type': 'license_approval', 'mode': 'all',
             'assignee': {'source': 'fixed', 'user_ids': []}},
        ]})))
    # Preserve old encrypted results, but never treat their generators as approvers.
    historical = db.scalars(select(LicenseRequest).where(~LicenseRequest.id.in_(
        select(WorkflowInstance.object_id).where(WorkflowInstance.object_type == 'license')))).all()
    for item in historical:
        item.status, item.stage, item.final_approver_id = 'pending', 'awaiting_workflow', None
    db.commit()


def check_expiration(data):
    if data.expire_time <= int(time.time() * 1000):
        raise HTTPException(400, '授权到期时间必须晚于当前时间')


def register_routes(app, current_user):
    @app.get('/api/licenses')
    def licenses(
        page: int = Query(default=1, ge=1), page_size: int = Query(default=10),
        status: Literal['all', 'pending', 'in_progress', 'generated', 'rejected'] = Query(default='all'),
        q: str = Query(default='', max_length=100),
        reminder: Literal['all', 'enabled', 'disabled'] = Query(default='all'),
        db: Session = Depends(get_db), user=Depends(current_user),
    ):
        if page_size not in {10, 20, 50}:
            raise HTTPException(422, '每页条数仅支持 10、20、50')
        base = visible(select(LicenseRequest), user)
        if reminder != 'all':
            base = base.where(LicenseRequest.reminder_enabled.is_(reminder == 'enabled'))
        if term := q.strip():
            matches_owner = LicenseRequest.applicant_id.in_(select(User.id).where(User.display_name.contains(term, autoescape=True)))
            base = base.where(or_(matches_owner, *(field.contains(term, autoescape=True) for field in (
                LicenseRequest.code, LicenseRequest.project_name, LicenseRequest.customer, LicenseRequest.product))))
        subquery = base.subquery()
        grouped = db.execute(select(subquery.c.status, func.count()).group_by(subquery.c.status)).all()
        counts = {key: 0 for key in ['all', 'pending', 'in_progress', 'generated', 'rejected']}
        for state, count in grouped:
            counts[state] = count
            counts['all'] += count
        total = counts[status]
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        query = base if status == 'all' else base.where(LicenseRequest.status == status)
        rows = db.scalars(query.order_by(LicenseRequest.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
        names = names_for(db, rows)
        return {'items': [license_out(row, names, user) for row in rows], 'counts': counts,
                'total': total, 'page': page, 'page_size': page_size, 'total_pages': total_pages,
                'can_manage': can_manage(user)}

    @app.post('/api/licenses', status_code=201)
    def apply_license(data: LicenseIn, tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(current_user)):
        from .main import start_workflow
        check_expiration(data)
        project = application_project(db, data.project_id, user)
        item = LicenseRequest(**{**data.model_dump(), 'project_name': project.name}, applicant_id=user.id, code='LIC-' + secrets.token_hex(12))
        db.add(item); db.flush()
        item.code = f'LIC-{now().year}-{item.id:04d}'
        start_workflow(db, tasks, 'license', item, user)
        return license_detail_out(db, item, user)

    @app.get('/api/licenses/{item_id}')
    def license_detail(item_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        return license_detail_out(db, get_visible(db, item_id, user), user)

    @app.get('/api/licenses/{item_id}/projects')
    def legacy_projects(item_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        item = get_visible(db, item_id, user)
        if item.applicant_id != user.id and not can_manage(user):
            raise HTTPException(403, '只有申请者或 License 管理者可以补充关联项目')
        if item.stage != 'awaiting_workflow':
            raise HTTPException(409, '此申请已进入审批流程')
        owner = db.get(User, item.applicant_id)
        if not owner or not owner.active:
            raise HTTPException(409, '申请者账号已停用，请先联系管理员处理')
        rows = db.scalars(flow.project_scope(select(Project).where(Project.active.is_(True)).order_by(Project.id.desc()), owner))
        return [{'id': p.id, 'name': p.name, 'department': p.department, 'active': p.active} for p in rows]

    @app.post('/api/licenses/{item_id}/submit-review')
    def submit_legacy(item_id: int, data: LegacyReviewIn, tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(current_user)):
        from .main import start_workflow
        item = db.scalar(select(LicenseRequest).where(LicenseRequest.id == item_id, LicenseRequest.deleted_at.is_(None)).with_for_update())
        if not item or (item.applicant_id != user.id and not can_manage(user)):
            raise HTTPException(403, '无权提交此申请')
        if item.stage != 'awaiting_workflow' or db.scalar(select(WorkflowInstance.id).where(
                WorkflowInstance.object_type == 'license', WorkflowInstance.object_id == item.id)):
            raise HTTPException(409, '此申请已进入审批流程')
        before = operation_audit.snapshot(db, 'license', item)
        check_expiration(item)
        owner = db.get(User, item.applicant_id)
        if not owner or not owner.active:
            raise HTTPException(409, '申请者账号已停用，请先联系管理员处理')
        project = application_project(db, data.project_id or item.project_id, owner)
        item.project_id, item.project_name = project.id, project.name
        start_workflow(db, tasks, 'license', item, owner, operation='submit_review', before=before, actor=user)
        return license_detail_out(db, item, user)

    @app.post('/api/licenses/{item_id}/resubmit')
    def resubmit_license(item_id: int, data: LicenseIn, tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(current_user)):
        from .main import start_workflow
        item = db.scalar(select(LicenseRequest).where(LicenseRequest.id == item_id, LicenseRequest.deleted_at.is_(None)).with_for_update())
        if not item or item.applicant_id != user.id:
            raise HTTPException(403, '只有申请者可以修改重提')
        if item.status != 'rejected':
            raise HTTPException(409, '只有已驳回申请可以修改重提')
        before = operation_audit.snapshot(db, 'license', item)
        check_expiration(data)
        project = application_project(db, data.project_id, user)
        payload = data.model_dump(exclude={'reminder_enabled'})
        if 'reminder_enabled' in data.model_fields_set:
            reminders.set_enabled(item, data.reminder_enabled, user)
        for field, value in {**payload, 'project_name': project.name}.items():
            setattr(item, field, value)
        # A previously generated legacy key must never survive changed parameters.
        item.license_key_ciphertext = item.license_key_hash = None
        item.generated_by = item.generated_at = item.final_approver_id = None
        start_workflow(db, tasks, 'license', item, user, operation='resubmit', before=before)
        return license_detail_out(db, item, user)

    @app.post('/api/licenses/{item_id}/generate')
    def retired_generate(item_id: int, user=Depends(current_user)):
        raise HTTPException(410, '独立生成功能已关闭，请通过最终审批生成 License')

    @app.delete('/api/licenses/{item_id}')
    def delete_license(item_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        item = db.scalar(select(LicenseRequest).where(LicenseRequest.id == item_id, LicenseRequest.deleted_at.is_(None)).with_for_update())
        if not item:
            raise HTTPException(404, '申请不存在或已删除')
        if user.id != item.applicant_id and not can_manage(user):
            raise HTTPException(403, '只有申请者或 License 管理员可以删除此申请')
        before = operation_audit.snapshot(db, 'license', item)
        item.deleted_at = now()
        operation_audit.record(db, user, 'delete', 'license', item, before=before, after={})
        db.commit()
        return {'ok': True}
