import secrets
import os
import json
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Literal
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from .database import Base, SessionLocal, engine, get_db
from .feishu import send_notification
from .models import Approval, AuditLog, ChangeRequest, DeliveryCredential, DeliveryProject, NotificationLog, ResourceRequest, User, WikiArticle, WikiSpace, WikiArticleVersion, WorkflowAssignment, SystemSetting, now
from .security import ACCOUNT_DISABLED_MESSAGE, decrypt_secret, encrypt_secret, hash_password, make_token, mask_open_id, token_user_id, verify_password, validate_password
from . import settings
from .migrations import upgrade, finish_upgrade, seed_knowledge
from .workflows import FLOWS, assignee, make_approval, seed_assignments, validate_route
from .auth_feishu import register_routes
from .roles import (ROLES, role_ids, effective_role_ids, has_role, canonical_role,
                    role_aliases, role_name, managed_roles, ROLE_ALIASES,
                    MANAGER_ROLE_SCOPE, stored_role_ids)
from .models import UserRole, Project, ProjectMember, WorkflowInstance, WorkflowNode, TaskTransfer, LicenseRequest
from . import flow_engine as flow
from . import audit as operation_audit
from .rate_limit import check_request_rate, client_ip, trusted_proxies
from . import redis_runtime
from . import resource_catalog
from . import reminders
from .notifications import create_notification
from .startup import initialization_lock
from .bootstrap import demo_enabled, ensure_administrator

OBJECTS = {"resource": ResourceRequest, "change": ChangeRequest, "delivery": DeliveryProject, "license": LicenseRequest}


def seed(db: Session):
    if not demo_enabled():
        ensure_administrator(db)
        return
    if db.scalar(select(func.count()).select_from(User)):
        return
    password = "Demo@2026"
    for username, name, role, department in [
        ("admin", "系统管理员", "admin", "平台管理"),
        ("applicant", "陈一诺", "applicant", "产品研发部"),
        ("dept", "李明远", "department_head", "产品研发部"),
        ("opslead", "周承宇", "ops_head", "运维部"),
        ("leader", "王志远", "manager", "管理层"),
        ("operator", "林知行", "operator", "运维部"),
        ("applicant2", "赵若溪", "applicant", "市场部"),
        ("dept2", "孙思齐", "department_head", "市场部"),
        ("operator2", "许墨", "operator", "运维部"),
    ]:
        db.add(User(username=username, display_name=name, role=role, department=department, password_hash=hash_password(password)))
    db.flush()
    applicant = db.scalar(select(User).where(User.username == "applicant"))
    operator = db.scalar(select(User).where(User.username == "operator"))
    db.add_all([
        ResourceRequest(code="RES-2026-001", project_name="星河数据分析平台", applicant_id=applicant.id, environment="生产", resource_type="ECS", specification="4 核 8 GB · 100 GB SSD · 2 台", start_date="2026-10-28", end_date="2027-10-28", purpose="部署分析服务与任务调度组件", capacity_basis="日均 50 万条事件，双节点保障可用性", ports="443, 8080", whitelist="10.20.0.0/16", status="pending", stage="department_head"),
        ResourceRequest(code="RES-2026-002", project_name="客户画像服务", applicant_id=applicant.id, environment="测试", resource_type="Redis", specification="4 GB 主从版", start_date="2026-10-24", end_date="2027-04-24", purpose="缓存客户画像查询结果", capacity_basis="约 100 万键，平均 2 KB", status="in_progress", stage="operator"),
        ChangeRequest(code="CHG-2026-001", name="生产环境网关证书更新", change_type="配置变更", window_start="2026-10-27T22:00", window_end="2026-10-27T23:00", owner_id=applicant.id, ops_owner_id=operator.id, problem_description="更新即将到期的生产网关证书，避免 HTTPS 服务中断。", status="pending", stage="ops_head"),
        DeliveryProject(code="DEL-2026-001", name="华东客户私有化部署", customer="华东科技有限公司", owner_id=applicant.id, cloud_type="专有云", deployment_mode="云平台", environment="POC", compute_type="虚拟机", accelerator="NVIDIA", network_notes="业务网与数据网隔离", db_whitelist="10.16.2.0/24", security_group="443, 3306", account_validation="待运维验证", status="pending", stage="ops_head"),
        WikiArticle(title="生产环境变更操作规范", category="运维规范", content="# 生产环境变更操作规范\n\n1. 提前提交变更申请并明确维护窗口。\n2. 记录执行方案与回滚方案。\n3. 实施完成后填写结果并通知相关人员。", author_id=operator.id),
        WikiArticle(title="数据库账号交付检查清单", category="交付方案", content="# 数据库账号交付检查清单\n\n- 核对实例地址与端口\n- 验证白名单与最小权限\n- 通过平台密文回填密码", author_id=operator.id),
    ])
    db.commit()
    seed_assignments(db)
    for kind, model in OBJECTS.items():
        for item in db.scalars(select(model).where(model.status.in_(["pending", "in_progress"]))).all():
            owner = db.get(User, item.applicant_id if kind == "resource" else item.owner_id)
            make_approval(db, kind, item, owner)
    db.commit()


def initialize_database():
    upgrade(engine)
    Base.metadata.create_all(bind=engine)
    finish_upgrade(engine)
    with SessionLocal() as db:
        seed(db)
        seed_assignments(db)
        for approval in db.scalars(select(Approval).where(Approval.action == "pending", Approval.assignee_id.is_(None))).all():
            item = db.get(OBJECTS[approval.object_type], approval.object_id)
            owner = db.get(User, item.applicant_id if approval.object_type == "resource" else item.owner_id)
            approval.assignee_id = assignee(db, approval.object_type, approval.stage, owner)
        flow.seed_v3(db, OBJECTS)
        from .flow_api import seed_demo
        if demo_enabled():
            seed_demo(db)
        from .licenses import seed_license_workflow
        seed_license_workflow(db)
        seed_knowledge(db)
        resource_catalog.seed(db)
        for key, default in settings.DEFAULTS.items():
            if db.get(SystemSetting, key) is None:
                db.add(SystemSetting(key=key, value=encrypt_secret(json.dumps(default)) if key in settings.SECRET_KEYS else json.dumps(default, ensure_ascii=False), is_secret=key in settings.SECRET_KEYS))
        settings.apply_password_login_mode(db)
        # Existing site_name is administrator-owned; defaults apply only to new installs.
        db.commit()
@asynccontextmanager
async def lifespan(app: FastAPI):
    trusted_proxies(os.getenv('TRUSTED_PROXY_CIDRS', ''))
    def initialize():
        with initialization_lock(engine):
            initialize_database()
    await run_in_threadpool(initialize)
    if redis_runtime.configured():
        await run_in_threadpool(redis_runtime.client().ping)
    yield


app = FastAPI(title="星河项目协作平台 API", version="0.1.0", lifespan=lifespan)


def write_request_audit(actor_id, token, method, path, status, ip_address):
    actor_id = actor_id or (token_user_id(token) if token else None)
    parts = path.strip("/").split("/")
    with SessionLocal() as db:
        person = db.execute(select(User.id, User.display_name, User.username).where(User.id == actor_id)).first() if actor_id else None
        db.add(AuditLog(record_kind='request', actor_id=person.id if person else None,
            actor_name=person.display_name if person else '', actor_username=person.username if person else '',
            method=method, path=path[:255], action=f"{method} {path}"[:80], target_type=parts[1] if len(parts) > 1 else "", target_id=parts[2] if len(parts) > 2 else "", status_code=status, ip_address=ip_address[:80]))
        db.commit()


@app.middleware("http")
async def audit_all_requests(request: Request, call_next):
    started, status, response = perf_counter(), 500, None
    ip = client_ip(request)
    request.state.client_ip = ip
    try:
        # Requests go straight to authentication and business authorization.
        # Frequency counters and their alerts are no longer part of this path.
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        handled = perf_counter()
        if request.url.path.startswith("/api/"):
            if request.url.path != "/api/health":
                # Await durable audit storage before responding, while freeing
                # the event loop to serve other pages and concurrent requests.
                await run_in_threadpool(write_request_audit,
                    getattr(request.state, "actor_id", None),
                    request.headers.get("authorization", "").removeprefix("Bearer "),
                    request.method, request.url.path, status, ip)
            if response is not None:
                response.headers["Cache-Control"] = "no-store"
                response.headers["Server-Timing"] = f"app;dur={(handled-started)*1000:.2f}, audit;dur={(perf_counter()-handled)*1000:.2f}"


# CORS also wraps authentication and business error responses.
app.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in os.getenv('CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080').split(',') if origin.strip()], allow_credentials=True, allow_methods=["*"], allow_headers=["*"], expose_headers=['Retry-After'])


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    uid = token_user_id(token)
    user = db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(401, "登录已失效，请重新登录")
    if not user.active:
        raise HTTPException(401, ACCOUNT_DISABLED_MESSAGE)
    request.state.actor_id = user.id
    db.info['audit_context'] = {'actor_name': user.display_name, 'actor_username': user.username,
        'method': request.method, 'path': request.url.path, 'ip_address': getattr(request.state, 'client_ip', '')}
    return user


def require_role(user: User, *roles: str):
    if not has_role(user, "admin", *roles):
        raise HTTPException(403, "当前角色无权执行此操作")


def user_out(user: User):
    grants = role_ids(user)
    return {"id": user.id, "username": user.username, "display_name": user.display_name,
            "department": user.department, "role": user.role, "roles": grants,
            "canonical_roles": effective_role_ids(user),
            "role_name": " / ".join(role_name(r) for r in grants),
            "feishu_open_id": mask_open_id(user.feishu_open_id),
            "feishu_open_id_set": bool(user.feishu_open_id), "active": user.active}


def valid_role_grants(grants):
    values = list(dict.fromkeys(canonical_role(r) for r in grants))
    if not values or any(r not in ROLES for r in values):
        raise HTTPException(400, '至少选择一个有效角色')
    return values


def can_manage_user_roles(actor: User, target: User | None = None):
    if has_role(actor, 'admin'):
        return True
    if not target or not target.active or has_role(target, 'admin') or actor.id == target.id or not actor.department.strip() or actor.department != target.department:
        return False
    return bool(managed_roles(actor))


def validate_role_department(grants, department):
    if set(grants) & set(MANAGER_ROLE_SCOPE) and not department.strip():
        raise HTTPException(400, '请先填写所属部门，负责人权限按所属部门生效')


def open_id_taken(db: Session, value: str, exclude_id: int | None = None):
    return any(u.id != exclude_id and u.feishu_open_id == value
               for u in db.scalars(select(User).where(User.feishu_open_id.is_not(None))).all())


def serialize(obj, **extra):
    data = {column.name: getattr(obj, column.name) for column in obj.__table__.columns if column.name not in {"password_hash", "password_ciphertext", "license_key_ciphertext", "license_key_hash"}}
    for key, value in data.items():
        if isinstance(value, datetime):
            data[key] = iso(value)
        elif key == "specification_values":
            try:
                data[key] = json.loads(value or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                data[key] = {}
    return {**data, **extra}


def iso(value: datetime):
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def visible(query, model, user: User):
    query = query.where(model.deleted_at.is_(None))
    if has_role(user, 'admin', 'ops_head', 'manager', 'operator'):
        return query
    owner = 'applicant_id' if model is ResourceRequest else 'owner_id'
    kind = next(k for k, m in OBJECTS.items() if m is model)
    project_ids = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id, ProjectMember.role.in_(stored_role_ids(user)))
    assigned = select(Approval.object_id).where(Approval.object_type == kind, or_(Approval.assignee_id == user.id, Approval.actor_id == user.id))
    conditions = [getattr(model, owner) == user.id, model.project_id.in_(project_ids), model.id.in_(assigned)]
    if has_role(user, 'department_head'):
        conditions.append(getattr(model, owner).in_(select(User.id).where(User.department == user.department)))
    return query.where(or_(*conditions))


def get_visible(db: Session, model, item_id: int, user: User):
    item = db.scalar(visible(select(model).where(model.id == item_id), model, user))
    if not item:
        raise HTTPException(404, "记录不存在或无权查看")
    return item


def notify(db: Session, tasks: BackgroundTasks, recipient_ids: list[int], event: str, object_type: str, obj, text: str, commit=True):
    ids = []
    for uid in set(recipient_ids):
        log = create_notification(db, uid, event, object_type, obj, text)
        ids.append(log.id)
    if commit:
        db.commit()
    for log_id in ids:
        tasks.add_task(send_notification, log_id)


def workflow_notifications(db, tasks, kind, obj, owner, event='status_changed', new_task_ids=None, acted_task=None):
    db.flush()
    pending = db.scalars(select(Approval).where(Approval.object_type == kind, Approval.object_id == obj.id, Approval.action == 'pending').order_by(Approval.id).with_for_update()).all()
    people_ids = {a.assignee_id for a in pending if a.assignee_id}
    people = {p.id: p for p in db.scalars(select(User).where(User.id.in_(people_ids)))} if people_ids else {}
    node_ids = {a.node_run_id for a in pending if a.node_run_id}
    nodes = {n.id: n for n in db.scalars(select(WorkflowNode).where(WorkflowNode.id.in_(node_ids)))} if node_ids else {}
    labels = {'resource': '资源申请', 'change': '变更申请', 'delivery': '私有化交付', 'license': 'License 授权'}
    title = getattr(obj, 'project_name', getattr(obj, 'name', ''))
    context = f'申请类型：{labels[kind]}\n申请编号：{obj.code}\n项目 / 标题：{title}\n申请人：{owner.display_name}'
    assignees = '、'.join(dict.fromkeys(people[a.assignee_id].display_name if a.assignee_id in people else '待管理员分配' for a in pending))
    stages = '、'.join(dict.fromkeys(nodes[a.node_run_id].name if a.node_run_id in nodes else a.stage for a in pending))
    if obj.status in {'completed', 'generated'}:
        event, headline = 'completed', '流程已完成'
        progress = '审批已通过，License 已生成，请登录平台查看。' if kind == 'license' else '全部审批与执行环节已完成，请登录平台查看结果。'
    elif obj.status == 'rejected':
        event, headline = 'rejected', '申请已驳回'
        progress = '请登录平台查看审批意见，修改后可重新提交。'
        if acted_task and acted_task.comment:
            progress += '\n驳回原因：' + acted_task.comment
    elif event == 'submitted':
        headline, progress = '申请已提交', f'当前环节：{stages}\n下一位处理人：{assignees}'
    elif event == 'approval_reassigned':
        headline, progress = '审批处理人已变更', f'当前环节：{stages}\n下一位处理人：{assignees}'
    else:
        headline = '审批进度已更新'
        progress = f'当前环节：{stages}\n下一位处理人：{assignees}'
    notify(db, tasks, [owner.id], event, kind, obj, f'{headline}\n{context}\n{progress}', commit=False)
    # On countersign, only new tasks should trigger reminders. Remaining
    # reviewers keep their original notification instead of receiving duplicates.
    new_pending = [a for a in pending if new_task_ids is None or a.id in new_task_ids]
    for uid in sorted({a.assignee_id for a in new_pending if a.assignee_id}):
        assigned = [a for a in new_pending if a.assignee_id == uid]
        node_names = '、'.join(dict.fromkeys(nodes[a.node_run_id].name if a.node_run_id in nodes else a.stage for a in assigned))
        execution = any(nodes.get(a.node_run_id) and nodes[a.node_run_id].node_type == 'execution' for a in assigned)
        headline = '您有一项待执行任务' if execution else '您有一项待审批申请'
        text = f'{headline}\n{context}\n待处理环节：{node_names}\n请登录平台查看并处理。'
        notify(db, tasks, [uid], 'approval_required', kind, obj, text, commit=False)
    reminders.sync_plans(db, kind, obj)
    db.commit()


def start_workflow(db, tasks, kind, obj, owner, *, operation='create', before=None, actor=None):
    flow.start(db, kind, obj, owner)
    operation_audit.record(db, actor or owner, operation, kind, obj, before=before,
                           status_code=201 if operation == 'create' and kind == 'license' else 200)
    workflow_notifications(db, tasks, kind, obj, owner, 'submitted')


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == data.username))
    if user and not user.active:
        raise HTTPException(403, ACCOUNT_DISABLED_MESSAGE)
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    request.state.actor_id = user.id
    return {"token": make_token(user.id), "user": user_out(user)}


register_routes(app, current_user, user_out)
from .admin import register_routes as register_admin_routes
register_admin_routes(app, current_user, require_role)


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator('display_name', mode='before')
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)

    @field_validator('new_password')
    @classmethod
    def strong_password(cls, value):
        return validate_password(value)


def lock_current_user(db, user):
    # Reload after acquiring the row lock so concurrent administrator changes
    # cannot be overwritten or bypassed by an earlier authentication read.
    item = db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    if not item or not item.active:
        raise HTTPException(401, ACCOUNT_DISABLED_MESSAGE if item else '登录已失效，请重新登录')
    return item


@app.patch('/api/me')
def update_profile(data: ProfileUpdate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = lock_current_user(db, user)
    before = operation_audit.snapshot(db, 'user', item)
    item.display_name = data.display_name
    operation_audit.record(db, user, 'update', 'user', item, before=before)
    db.commit()
    return user_out(item)


@app.post('/api/me/password')
def change_password(data: PasswordChange, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = lock_current_user(db, user)
    if not verify_password(data.current_password, item.password_hash):
        raise HTTPException(400, '当前密码不正确，请重新输入')
    if data.current_password == data.new_password:
        raise HTTPException(400, '新密码不能与当前密码相同')
    before = operation_audit.snapshot(db, 'user', item)
    item.password_hash = hash_password(data.new_password)
    operation_audit.record(db, user, 'change_password', 'user', item, before=before,
                           extra={'password': (None, '已修改')})
    db.commit()
    return {'message': '密码已修改，下次登录请使用新密码'}


@app.get("/api/health")
def health():
    if redis_runtime.configured():
        from redis.exceptions import RedisError
        try:
            redis_runtime.client().ping()
        except RedisError:
            return JSONResponse(status_code=503, content={'status': 'unavailable', 'redis': 'unavailable'})
    return {"status": "ok"}


def page_result(db, query, page, page_size, render):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    total_pages = max(1, (total + page_size - 1) // page_size)
    current = min(page, total_pages)
    rows = db.scalars(query.offset((current - 1) * page_size).limit(page_size)).all()
    return {'items': [render(row) for row in rows], 'total': total, 'page': current,
            'page_size': page_size, 'total_pages': total_pages}


@app.get("/api/users")
def list_users(page: int | None = Query(None, ge=1), page_size: int = Query(10),
               q: str = Query('', max_length=100), status: Literal['all', 'enabled', 'disabled'] = 'all',
               role: str = Query('all', max_length=60), department: str = Query('', max_length=100),
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    if page_size not in {10, 20, 50}: raise HTTPException(422, '每页条数仅支持 10、20、50')
    query = select(User).order_by(User.id)
    if not has_role(user, "admin"):
        if page is None or not managed_roles(user):
            query = query.where(User.active == True)
        # Scoped department leaders only need the people they can administer;
        # keeping this filter server-side prevents cross-department disclosure.
        if page is not None and managed_roles(user):
            query = query.where(User.department == user.department)
    if status != 'all': query = query.where(User.active == (status == 'enabled'))
    if department.strip(): query = query.where(User.department == department.strip())
    if role != 'all':
        if role not in ROLES and role not in ROLE_ALIASES: raise HTTPException(422, '无效的角色筛选')
        aliases = role_aliases(role)
        query = query.where(or_(*[User.role_grants.any(UserRole.role == value) for value in aliases],
                               ~User.role_grants.any() & User.role.in_(aliases)))
    if q.strip():
        fields = [User.display_name, User.department] + ([User.username] if has_role(user, 'admin') or page is not None and managed_roles(user) else [])
        query = query.where(or_(*(field.icontains(q.strip(), autoescape=True) for field in fields)))
    def render(person):
        data = user_out(person)
        data['can_manage_roles'] = can_manage_user_roles(user, person)
        data['managed_roles'] = sorted(managed_roles(user)) if data['can_manage_roles'] else []
        return data if has_role(user, 'admin') else {k: v for k, v in data.items() if k not in {'feishu_open_id', 'feishu_open_id_set'} and (k != 'username' or page is not None and managed_roles(user))}
    # Unpaged responses are retained for project/workflow member selectors.
    if page is not None: return page_result(db, query, page, page_size, render)
    return [render(person) for person in db.scalars(query)]


@app.get('/api/departments')
def list_departments(db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not has_role(user, 'admin') and not managed_roles(user):
        raise HTTPException(403, '当前角色无权查看部门管理')
    query = select(User).where(User.department != '')
    if not has_role(user, 'admin'):
        query = query.where(User.department == user.department)
    departments = {}
    for person in db.scalars(query.order_by(User.department, User.id)):
        item = departments.setdefault(person.department, {'name': person.department, 'member_count': 0, 'heads': []})
        item['member_count'] += 1
        heads = set(effective_role_ids(person)) & (set(MANAGER_ROLE_SCOPE) | {'department_head'})
        if person.active and heads:
            item['heads'].append({'id': person.id, 'name': person.display_name,
                                  'roles': sorted(heads), 'role_names': [role_name(r) for r in sorted(heads)]})
    if has_role(user, 'admin'):
        for name in db.scalars(select(Project.department).distinct()):
            if name:
                departments.setdefault(name, {'name': name, 'member_count': 0, 'heads': []})
    return sorted(departments.values(), key=lambda item: item['name'])


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    display_name: str = Field(min_length=1, max_length=80)
    department: str = Field(default='', max_length=100)
    role: str = "applicant"
    roles: list[str] | None = None
    password: str = Field(min_length=12, max_length=128)
    feishu_open_id: str | None = None

    @field_validator('password')
    @classmethod
    def strong_password(cls, value):
        return validate_password(value)


@app.post("/api/users")
def create_user(data: UserIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_role(user, "admin")
    grants = valid_role_grants(data.roles if data.roles is not None else [data.role])
    validate_role_department(grants, data.department)
    if db.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(400, "角色无效或用户名已存在")
    open_id = data.feishu_open_id.strip() if data.feishu_open_id else None
    if open_id and '••••' in open_id:
        raise HTTPException(400, '请填写完整的飞书 open_id，不能使用页面上的密文')
    if open_id and open_id_taken(db, open_id):
        raise HTTPException(409, "飞书身份已绑定其他成员")
    item = User(username=data.username, display_name=data.display_name, department=data.department.strip(), role=grants[0], password_hash=hash_password(data.password), feishu_open_id=open_id)
    item.role_grants = [UserRole(role=r) for r in grants]
    db.add(item)
    db.flush()
    from .knowledge import ensure_personal_space
    ensure_personal_space(db, item)
    operation_audit.record(db, user, 'create', 'user', item, extra={'password': (None, '已设置')})
    db.commit()
    db.refresh(item)
    return user_out(item)


class UserUpdate(BaseModel):
    display_name: str | None = None
    department: str | None = Field(default=None, max_length=100)
    role: str | None = None
    roles: list[str] | None = None
    feishu_open_id: str | None = None
    active: bool | None = None
    password: str | None = None

    @field_validator('password')
    @classmethod
    def strong_password(cls, value):
        if value is None or len(value) > 128:
            raise ValueError('密码需为 12–128 位')
        return validate_password(value)


@app.patch("/api/users/{item_id}")
def update_user(item_id: int, data: UserUpdate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = db.scalar(select(User).where(User.id == item_id).with_for_update())
    if not item:
        raise HTTPException(404, "用户不存在")
    scoped_editor = not has_role(user, 'admin') and can_manage_user_roles(user, item)
    if not has_role(user, 'admin') and not scoped_editor:
        raise HTTPException(403, '当前角色无权管理该成员')
    if scoped_editor:
        changed = set(data.model_dump(exclude_unset=True)) - {'roles', 'role'}
        if changed:
            raise HTTPException(403, '部门负责人只能调整职责范围内的成员角色')
        if data.roles is None and data.role is None:
            raise HTTPException(400, '负责人只能修改成员角色')
    before = operation_audit.snapshot(db, 'user', item)
    grants = data.roles if data.roles is not None else ([data.role] if data.role is not None else role_ids(item))
    grants = valid_role_grants(grants)
    if data.department is not None or data.roles is not None or data.role is not None:
        validate_role_department(grants, data.department if data.department is not None else item.department)
    if scoped_editor:
        allowed = managed_roles(user)
        current_effective = set(canonical_role(r) for r in role_ids(item))
        requested_effective = set(canonical_role(r) for r in grants)
        if not (requested_effective - current_effective) <= allowed or (current_effective - requested_effective) - allowed:
            raise HTTPException(403, '只能调整负责人职责范围内的岗位，其他角色请由系统管理员维护')
        # Keep grants outside the manager scope even if a stale client omitted
        # them from the submitted multi-select.
        grants = list(dict.fromkeys([canonical_role(r) for r in role_ids(item) if canonical_role(r) not in allowed] + grants))
    if has_role(item, 'admin') and ('admin' not in grants or data.active is False):
        admins = [u for u in db.scalars(select(User).where(User.active == True)).all() if has_role(u, 'admin')]
        if len(admins) <= 1:
            raise HTTPException(400, '至少保留一位启用的管理员')
    item.role_grants = [UserRole(role=r) for r in grants]
    item.role = grants[0]
    for key, value in data.model_dump(exclude_unset=True).items():
        if key in {"roles", "role"}:
            continue
        if key == "feishu_open_id":
            value = value.strip() if value else None
            if value and '••••' in value:
                raise HTTPException(400, '请填写完整的飞书 open_id，不能使用页面上的密文')
            if value and open_id_taken(db, value, item.id):
                raise HTTPException(409, "飞书身份已绑定其他成员")
        if key == "role" and value not in ROLES and value not in ROLE_ALIASES:
            raise HTTPException(400, "角色无效")
        if key == "password":
            item.password_hash = hash_password(value)
        elif key == 'department':
            if value is None:
                raise HTTPException(400, '所属部门不能为 null')
            item.department = value.strip()
        else:
            setattr(item, key, value)
    action = 'disable' if before['active'] and not item.active else 'enable' if not before['active'] and item.active else 'update'
    operation_audit.record(db, user, action, 'user', item, before=before,
                           extra={'password': (None, '已重置')} if data.password is not None else None)
    db.commit()
    return user_out(item)


class ResourceIn(BaseModel):
    project_id: int
    project_name: str = ""
    environment: Literal["POC", "测试", "生产"]
    resource_type: str
    specification: str = Field(default="", max_length=10000)
    specification_values: dict[str, Any] = Field(default_factory=dict)
    start_date: str
    end_date: str
    purpose: str = Field(min_length=1)
    capacity_basis: str = Field(min_length=1)
    ports: str = ""
    whitelist: str = ""
    reminder_enabled: bool = True

    @field_validator('start_date', 'end_date')
    @classmethod
    def valid_date(cls, value):
        from datetime import date
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError('日期格式须为 YYYY-MM-DD')
        if parsed.isoformat() != value:
            raise ValueError('日期格式须为 YYYY-MM-DD')
        return value


@app.get("/api/resource-catalog")
def resource_catalog_options(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return resource_catalog.catalog(db)


class ListQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: Literal[10, 20, 50] = 10
    status: Literal['all', 'pending', 'in_progress', 'completed', 'rejected'] = 'all'
    q: str = Field(default='', max_length=100)
    reminder: Literal['all', 'enabled', 'disabled'] = 'all'


def list_query(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10),
    status: Literal['all', 'pending', 'in_progress', 'completed', 'rejected'] = Query(default='all'),
    q: str = Query(default='', max_length=100),
    reminder: Literal['all', 'enabled', 'disabled'] = Query(default='all'),
):
    if page_size not in {10, 20, 50}:
        raise HTTPException(422, '每页条数仅支持 10、20、50')
    return ListQuery(page=page, page_size=page_size, status=status, q=q, reminder=reminder)


def paginated_records(db: Session, model, user: User, options: ListQuery):
    """Count/search only visible rows, then fetch the requested slice in SQL.

    Status badges follow the search but remain independent of the selected tab
    and page. A page removed by a workflow update is clamped to the last page.
    """
    owner_column = model.applicant_id if model is ResourceRequest else model.owner_id
    base = visible(select(model), model, user)
    if model is ResourceRequest and options.reminder != 'all':
        base = base.where(ResourceRequest.reminder_enabled.is_(options.reminder == 'enabled'))
    term = options.q.strip()
    if term:
        fields = {
            ResourceRequest: [ResourceRequest.project_name, ResourceRequest.resource_type, ResourceRequest.environment],
            ChangeRequest: [ChangeRequest.name, ChangeRequest.change_type, ChangeRequest.environment],
            DeliveryProject: [DeliveryProject.name, DeliveryProject.customer, DeliveryProject.cloud_type, DeliveryProject.environment, DeliveryProject.deployment_mode, DeliveryProject.compute_type, DeliveryProject.accelerator],
        }[model]
        # contains(autoescape=True) treats user-entered %/_ as literal characters.
        matches_owner = owner_column.in_(select(User.id).where(User.display_name.icontains(term, autoescape=True)))
        base = base.where(or_(model.code.icontains(term, autoescape=True), matches_owner,
                              *(field.icontains(term, autoescape=True) for field in fields)))
    grouped = db.execute(base.with_only_columns(model.status, func.count()).group_by(model.status)).all()
    counts = {key: 0 for key in ['all', 'pending', 'in_progress', 'completed', 'rejected']}
    for status, count in grouped:
        counts[status] = count
        counts['all'] += count
    total = counts['all'] if options.status == 'all' else counts[options.status]
    total_pages = max(1, (total + options.page_size - 1) // options.page_size)
    current_page = min(options.page, total_pages)
    query = base if options.status == 'all' else base.where(model.status == options.status)
    rows = db.scalars(query.order_by(model.id.desc()).offset((current_page - 1) * options.page_size).limit(options.page_size)).all()
    owner_ids = {getattr(x, 'applicant_id', None) or x.owner_id for x in rows}
    owners = {p.id: p.display_name for p in db.scalars(select(User).where(User.id.in_(owner_ids))).all()} if rows else {}
    items = []
    for row in rows:
        if model is ResourceRequest:
            items.append(serialize(row, applicant_name=owners[row.applicant_id]))
        elif model is DeliveryProject:
            items.append(serialize(row, owner_name=owners[row.owner_id], credential_count=len(row.credentials)))
        else:
            items.append(serialize(row, owner_name=owners[row.owner_id]))
    return {'items': items, 'total': total, 'page': current_page, 'page_size': options.page_size,
            'total_pages': total_pages, 'counts': counts}


@app.get("/api/resources")
def resources(options: ListQuery = Depends(list_query), db: Session = Depends(get_db), user: User = Depends(current_user)):
    return paginated_records(db, ResourceRequest, user, options)


@app.post("/api/resources")
def create_resource(data: ResourceIn, tasks: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if data.end_date < data.start_date:
        raise HTTPException(400, "资源类型或使用期限无效")
    specification, specification_values = resource_catalog.validate_values(db, data.resource_type, data.specification_values, data.specification)
    data.project_name = flow.project_access(db, data.project_id, user).name
    values = data.model_dump(exclude={"specification_values", "specification"})
    item = ResourceRequest(**values, specification=specification, specification_values=json.dumps(resource_catalog.specification_snapshot(db, data.resource_type, specification_values), ensure_ascii=False), applicant_id=user.id, code="RES-" + secrets.token_hex(12))
    db.add(item)
    db.flush()
    item.code = f"RES-{now().year}-{item.id:04d}"
    start_workflow(db, tasks, "resource", item, user)
    return serialize(item)


@app.get("/api/resources/{item_id}")
def resource_detail(item_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = get_visible(db, ResourceRequest, item_id, user)
    return detail(db, "resource", item, user=user)


class ChangeIn(BaseModel):
    project_id: int
    environment: Literal["POC", "测试", "生产"] = "生产"
    name: str = Field(min_length=1)
    change_type: str = Field(min_length=1)
    window_start: str
    window_end: str
    ops_owner_id: int | None = None
    problem_description: str = Field(min_length=1)

    @field_validator("problem_description")
    @classmethod
    def require_problem_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请填写变更问题说明")
        return value


@app.get("/api/changes")
def changes(options: ListQuery = Depends(list_query), db: Session = Depends(get_db), user: User = Depends(current_user)):
    return paginated_records(db, ChangeRequest, user, options)


@app.post("/api/changes")
def create_change(data: ChangeIn, tasks: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if data.window_end <= data.window_start:
        raise HTTPException(400, "维护窗口结束时间必须晚于开始时间")
    if data.ops_owner_id:
        ops = db.get(User, data.ops_owner_id)
        if not ops or not ops.active or not has_role(ops, "operator", "ops_head"):
            raise HTTPException(400, "运维负责人无效")
    payload = data.model_dump()
    payload.update(plan="", rollback_plan="", implementation="")
    item = ChangeRequest(**payload, owner_id=user.id, code="CHG-" + secrets.token_hex(12))
    db.add(item)
    db.flush()
    item.code = f"CHG-{now().year}-{item.id:04d}"
    start_workflow(db, tasks, "change", item, user)
    return serialize(item)


@app.get("/api/changes/{item_id}")
def change_detail(item_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return detail(db, "change", get_visible(db, ChangeRequest, item_id, user), user=user)


class DeliveryIn(BaseModel):
    project_id: int
    name: str = Field(min_length=1)
    customer: str = Field(min_length=1)
    cloud_type: Literal["专有云", "公有云"]
    deployment_mode: Literal["云平台", "虚拟机"]
    environment: Literal["POC", "正式"]
    compute_type: Literal["云主机", "虚拟机", "GPU"]
    accelerator: Literal["无", "NVIDIA", "NPU"] = "无"
    network_notes: str = ""
    db_whitelist: str = ""
    security_group: str = ""
    account_validation: str = ""
    notes: str = ""


@app.get("/api/deliveries")
def deliveries(options: ListQuery = Depends(list_query), db: Session = Depends(get_db), user: User = Depends(current_user)):
    return paginated_records(db, DeliveryProject, user, options)


@app.post("/api/deliveries")
def create_delivery(data: DeliveryIn, tasks: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = DeliveryProject(**data.model_dump(), owner_id=user.id, code="DEL-" + secrets.token_hex(12))
    db.add(item)
    db.flush()
    item.code = f"DEL-{now().year}-{item.id:04d}"
    start_workflow(db, tasks, "delivery", item, user)
    return serialize(item)


@app.get("/api/deliveries/{item_id}")
def delivery_detail(item_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = get_visible(db, DeliveryProject, item_id, user)
    return detail(db, "delivery", item, user=user, credentials=[serialize(x, password_set=True) for x in item.credentials])


@app.post("/api/deliveries/{item_id}/credentials", deprecated=True, responses={410: {'description': '数据库账号回填已取消'}})
def add_credential(item_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    get_visible(db, DeliveryProject, item_id, user)
    raise HTTPException(410, "私有化交付已取消数据库账号回填，请通过项目附件补充交付文档")


@app.get("/api/deliveries/{item_id}/credentials/{credential_id}/reveal")
def reveal_credential(item_id: int, credential_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project = db.get(DeliveryProject, item_id)
    if not project or (not has_role(user, "admin", "operator") and project.owner_id != user.id):
        raise HTTPException(403, "无权查看此凭据")
    credential = db.get(DeliveryCredential, credential_id)
    if not credential or credential.project_id != item_id:
        raise HTTPException(404, "凭据不存在")
    password = decrypt_secret(credential.password_ciphertext)
    operation_audit.record(db, user, 'view_secret', 'delivery', project, after={})
    db.commit()
    return {"password": password}


def detail(db: Session, kind: str, item, user=None, **extra):
    owner = db.get(User, flow.owner_id(kind, item))
    history = db.scalars(select(Approval).where(Approval.object_type == kind, Approval.object_id == item.id).order_by(Approval.id)).all()
    approvals = [serialize(a, actor_name=db.get(User, a.actor_id).display_name if a.actor_id else '', assignee_name=db.get(User, a.assignee_id).display_name if a.assignee_id else '未分配', eligible=flow.eligible(db.get(User, a.assignee_id), a) if a.assignee_id else False) for a in history]
    rounds = []
    for inst in db.scalars(select(WorkflowInstance).where(WorkflowInstance.object_type == kind, WorkflowInstance.object_id == item.id).order_by(WorkflowInstance.round)).all():
        nodes = db.scalars(select(WorkflowNode).where(WorkflowNode.instance_id == inst.id).order_by(WorkflowNode.position)).all()
        rounds.append(serialize(inst, snapshot=json.loads(inst.snapshot), nodes=[serialize(n, config=json.loads(n.config), tasks=[a for a in approvals if a['node_run_id'] == n.id]) for n in nodes]))
    transfers = db.scalars(select(TaskTransfer).where(TaskTransfer.task_id.in_([a.id for a in history]))).all() if history else []
    current_node = next((n for n in rounds[-1]['nodes'] if n['status'] == 'pending'), None) if rounds else None
    my_tasks = [a for a in approvals if user and a['assignee_id'] == user.id and a['action'] == 'pending']
    snapshot = rounds[-1]['snapshot'] if rounds else {}
    owner_id = flow.owner_id(kind, item)
    can_delete = bool(user and (user.id == owner_id or has_role(user, 'admin') or (kind == 'license' and has_role(user, 'license_manager'))))
    if kind in {'resource', 'license'}:
        extra['reminder'] = reminders.info(db, kind, item, user)
    return serialize(item, current_node=current_node, my_tasks=my_tasks, can_delete=can_delete,
        approval_round=rounds[-1]['round'] if rounds else None,
        owner_name=owner.display_name, owner_department=owner.department,
        approval_department=snapshot.get('context', {}).get('department'), approval_department_source=snapshot.get('department_source'),
        current_assignee_id=next((a.assignee_id for a in history if a.action == 'pending'), None), approvals=approvals, rounds=rounds, transfers=[serialize(t) for t in transfers], **extra)


class ActionIn(BaseModel):
    task_id: int
    action: Literal['approve', 'reject']
    comment: str = ''
    fulfillment: str = ''
    implementation_plan: str = ''
    rollback_plan: str = ''


@app.post('/api/{kind}/{item_id}/action')
def workflow_action(kind: str, item_id: int, data: ActionIn, tasks: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if kind not in OBJECTS: raise HTTPException(404, '流程类型不存在')
    item = db.scalar(select(OBJECTS[kind]).where(OBJECTS[kind].id == item_id, OBJECTS[kind].deleted_at.is_(None)).with_for_update())
    if not item: raise HTTPException(404, '申请单不存在')
    user = db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    grants = db.scalars(select(UserRole).where(UserRole.user_id == user.id).with_for_update().execution_options(populate_existing=True)).all()
    set_committed_value(user, 'role_grants', grants)
    before = operation_audit.snapshot(db, kind, item)
    previous_tasks = set(db.scalars(select(Approval.id).where(Approval.object_type == kind, Approval.object_id == item.id, Approval.action == 'pending')))
    try:
        acted_task = flow.act(db, kind, item, data.task_id, user, data.action, data.comment, data.fulfillment, data.implementation_plan, data.rollback_plan)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '审批结果保存冲突，请刷新后重试')
    except Exception:
        # Signer failure also rolls back the final approval and task state.
        db.rollback()
        raise
    current_tasks = set(db.scalars(select(Approval.id).where(Approval.object_type == kind, Approval.object_id == item.id, Approval.action == 'pending')))
    node = db.get(WorkflowNode, acted_task.node_run_id) if acted_task.node_run_id else None
    action = 'reject' if data.action == 'reject' else 'generate' if kind == 'license' and item.status == 'generated' else 'complete' if node and node.node_type == 'execution' else 'approve'
    extras = {'node': (None, node.name if node else acted_task.stage)}
    if data.comment: extras['comment'] = (None, data.comment)
    if data.fulfillment: extras['fulfillment'] = (None, '已回填')
    operation_audit.record(db, user, action, kind, item, before=before, extra=extras)
    workflow_notifications(db, tasks, kind, item, db.get(User, flow.owner_id(kind, item)), new_task_ids=current_tasks - previous_tasks, acted_task=acted_task)
    if kind == 'license':
        from .licenses import license_detail_out
        return license_detail_out(db, item, user)
    return detail(db, kind, item, user=user)


@app.delete('/api/{kind}/{item_id}')
def delete_request(kind: str, item_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if kind in {'project', 'projects'}:
        raise HTTPException(405, '项目是统一协作目录，不支持删除；请停用项目')
    kind = {'resources': 'resource', 'changes': 'change', 'deliveries': 'delivery', 'licenses': 'license'}.get(kind, kind)
    if kind not in OBJECTS:
        raise HTTPException(404, '单据类型不存在')
    model = OBJECTS[kind]
    item = db.scalar(select(model).where(model.id == item_id, model.deleted_at.is_(None)).with_for_update())
    if not item:
        raise HTTPException(404, '记录不存在或已删除')
    owner_id = flow.owner_id(kind, item)
    if user.id != owner_id and not has_role(user, 'admin') and not (kind == 'license' and has_role(user, 'license_manager')):
        raise HTTPException(403, '只有申请人或有管理权限的成员可以删除此单据')
    before = operation_audit.snapshot(db, kind, item)
    item.deleted_at = now()
    operation_audit.record(db, user, 'delete', kind, item, before=before, after={})
    db.commit()
    return {'ok': True, 'deleted_at': iso(item.deleted_at)}


@app.delete('/api/projects/{item_id}')
def delete_project(item_id: int, user: User = Depends(current_user)):
    raise HTTPException(405, '项目是统一协作目录，不支持删除；请停用项目')


@app.get('/api/tasks')
def tasks(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(current_user)):
    # The approval inbox is strictly personal, including for system administrators.
    active_object = or_(*((Approval.object_type == kind) & Approval.object_id.in_(select(model.id).where(model.deleted_at.is_(None))) for kind, model in OBJECTS.items()))
    base = select(Approval).where(Approval.action == 'pending', Approval.assignee_id == user.id, active_object).order_by(Approval.id.desc())
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    query = base.offset((page - 1) * page_size).limit(page_size)
    result = []
    for a in db.scalars(query).all():
        item = db.get(OBJECTS[a.object_type], a.object_id)
        if not item or item.deleted_at is not None:
            continue
        node = db.get(WorkflowNode, a.node_run_id) if a.node_run_id else None
        result.append({'id': a.id, 'kind': a.object_type, 'object_id': item.id, 'code': item.code,
                       'title': getattr(item, 'project_name', getattr(item, 'name', '')), 'stage': node.name if node else a.stage,
                       'owner_name': db.get(User, flow.owner_id(a.object_type, item)).display_name,
                       'assignee_name': db.get(User, a.assignee_id).display_name if a.assignee_id else '未分配',
                       'can_delete': user.id == flow.owner_id(a.object_type, item) or has_role(user, 'admin') or (a.object_type == 'license' and has_role(user, 'license_manager')),
                       'eligible': flow.eligible(db.get(User, a.assignee_id), a), 'created_at': iso(a.created_at)})
    return {'items': result, 'total': total, 'page': page, 'page_size': page_size, 'total_pages': total_pages}


def resubmit(kind, item_id, data, tasks, db, user):
    item = db.scalar(select(OBJECTS[kind]).where(OBJECTS[kind].id == item_id, OBJECTS[kind].deleted_at.is_(None)).with_for_update())
    if not item or flow.owner_id(kind, item) != user.id: raise HTTPException(403, '只有申请人可以修改重提')
    if item.status != 'rejected': raise HTTPException(409, '只有已驳回单据可以修改重提')
    before = operation_audit.snapshot(db, kind, item)
    resource_specification = None
    resource_specification_values = None
    if kind == 'resource':
        if data.end_date < data.start_date: raise HTTPException(400, '资源类型或使用期限无效')
        resource_specification, resource_specification_values = resource_catalog.validate_values(db, data.resource_type, data.specification_values, data.specification)
        data.project_name = flow.project_access(db, data.project_id, user).name
    if kind == 'change' and data.window_end <= data.window_start: raise HTTPException(400, '维护窗口无效')
    payload = data.model_dump()
    if kind == 'resource':
        enabled = payload.pop('reminder_enabled')
        if 'reminder_enabled' in data.model_fields_set:
            reminders.set_enabled(item, enabled, user)
    if kind == 'resource':
        payload.pop('specification_values', None)
        payload['specification'] = resource_specification
        payload['specification_values'] = json.dumps(resource_catalog.specification_snapshot(db, data.resource_type, resource_specification_values), ensure_ascii=False)
    if kind == 'change':
        # Execution plans belong to the execution stage; a resubmission begins a fresh approval cycle.
        payload.update(plan='', rollback_plan='', implementation='')
    for key, value in payload.items():
        if hasattr(item, key):
            setattr(item, key, value)
    start_workflow(db, tasks, kind, item, user, operation='resubmit', before=before)
    return detail(db, kind, item, user=user)


@app.post('/api/resource/{item_id}/resubmit')
def resubmit_resource(item_id: int, data: ResourceIn, tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(current_user)):
    return resubmit('resource', item_id, data, tasks, db, user)


@app.post('/api/change/{item_id}/resubmit')
def resubmit_change(item_id: int, data: ChangeIn, tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(current_user)):
    return resubmit('change', item_id, data, tasks, db, user)


@app.post('/api/delivery/{item_id}/resubmit')
def resubmit_delivery(item_id: int, data: DeliveryIn, tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(current_user)):
    return resubmit('delivery', item_id, data, tasks, db, user)


from .knowledge import register_routes as register_knowledge_routes
register_knowledge_routes(app, current_user)


@app.get("/api/notifications")
def notifications(page: int | None = Query(None, ge=1), page_size: int = Query(10),
                  q: str = Query('', max_length=100), status: Literal['all', 'sent', 'failed', 'unconfigured', 'pending', 'cancelled', 'skipped'] = 'all',
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    if page_size not in {10, 20, 50}: raise HTTPException(422, '每页条数仅支持 10、20、50')
    query = select(NotificationLog).order_by(NotificationLog.id.desc())
    if not has_role(user, "admin"):
        query = query.where(NotificationLog.recipient_id == user.id)
    if status != 'all': query = query.where(NotificationLog.status == status)
    if q.strip():
        query = query.where(or_(NotificationLog.message.icontains(q.strip(), autoescape=True),
            NotificationLog.recipient_id.in_(select(User.id).where(User.display_name.icontains(q.strip(), autoescape=True)))))
    def render(item):
        recipient = db.get(User, item.recipient_id)
        return serialize(item, recipient_name=recipient.display_name if recipient else '原成员')
    if page is not None: return page_result(db, query, page, page_size, render)
    return [render(item) for item in db.scalars(query.limit(100))]


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(current_user)):
    from .licenses import visible as visible_licenses
    resources = db.scalars(visible(select(ResourceRequest), ResourceRequest, user)).all()
    changes = db.scalars(visible(select(ChangeRequest), ChangeRequest, user)).all()
    deliveries = db.scalars(visible(select(DeliveryProject), DeliveryProject, user)).all()
    license_counts = dict(db.execute(visible_licenses(select(LicenseRequest.status, func.count()).group_by(LicenseRequest.status), user)).all())
    pending = sum(x.status in {"pending", "in_progress"} for x in resources + changes + deliveries) + sum(license_counts.get(status, 0) for status in ('pending', 'in_progress'))
    configured_types = resource_catalog.catalog(db)
    types = {item["key"]: sum(x.resource_type == item["key"] for x in resources) for item in configured_types}
    # Keep historical types visible in dashboard counts after an administrator
    # archives a catalog entry.
    types.update({kind: sum(x.resource_type == kind for x in resources) for kind in sorted({x.resource_type for x in resources} - set(types))})
    license_recent = db.execute(visible_licenses(select(LicenseRequest.id, LicenseRequest.code, LicenseRequest.project_name, LicenseRequest.status, LicenseRequest.created_at), user).order_by(LicenseRequest.created_at.desc(), LicenseRequest.id.desc()).limit(6)).all()
    recent = [{"kind": kind, "id": x.id, "code": x.code, "title": getattr(x, "project_name", getattr(x, "name", "")), "status": x.status, "created_at": iso(x.created_at)} for kind, rows in [("resource", resources), ("change", changes), ("delivery", deliveries), ("license", license_recent)] for x in rows]
    recent = sorted(recent, key=lambda x: x['created_at'], reverse=True)[:6]
    return {"resource_count": len(resources), "change_count": len(changes), "delivery_count": len(deliveries), "license_count": sum(license_counts.values()), "pending_count": pending, "completed_count": sum(x.status == "completed" for x in resources + changes + deliveries) + license_counts.get('generated', 0), "resource_types": types, "recent": recent}


from .flow_api import register_routes as register_flow_routes
register_flow_routes(app, current_user, require_role)

from .licenses import register_routes as register_license_routes
register_license_routes(app, current_user)

operation_audit.register_routes(app, current_user, require_role)
from .attachments import register_routes as register_attachment_routes
register_attachment_routes(app, current_user, get_visible)

reminders.register_routes(app, current_user)

