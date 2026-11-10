"""Space-based knowledge access. Platform roles never bypass these checks."""
from typing import Literal
import secrets
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_db
from .models import (Department, DepartmentMembership, User, WikiArticle, WikiArticleVersion,
                     WikiSpace, WikiSpaceDepartment, WikiSpaceMember, now)

DENIED = '内容不存在或无权访问'


def range_access(user_id):
    """Read grants from current organization rows; never copy department members."""
    direct = select(Department.id).where(Department.active.is_(True), or_(
        Department.id.in_(select(User.department_id).where(User.id == user_id).correlate(None)),
        Department.id.in_(select(DepartmentMembership.department_id).where(DepartmentMembership.user_id == user_id).correlate(None)))).correlate(None)
    ancestry = select(Department.id, Department.parent_id).where(Department.id.in_(direct)).cte(recursive=True)
    # UNION 去重同时阻断历史循环部门；停用的部门不继续向上继承授权。
    ancestry = ancestry.union(select(Department.id, Department.parent_id).join(
        ancestry, Department.id == ancestry.c.parent_id).where(Department.active.is_(True)))
    department_grant = exists(select(1).where(WikiSpaceDepartment.space_id == WikiSpace.id,
        or_(WikiSpaceDepartment.department_id.in_(direct), and_(WikiSpace.include_subdepartments.is_(True),
            WikiSpaceDepartment.department_id.in_(select(ancestry.c.id))))))
    return or_(WikiSpace.access_scope == 'all', and_(WikiSpace.access_scope == 'departments', department_grant))


def accessible_spaces(user_id):
    return and_(exists(select(1).where(User.id == user_id, User.active.is_(True))).correlate(None),
        WikiSpace.deleted_at.is_(None), or_(
        and_(WikiSpace.kind == 'personal', WikiSpace.owner_id == user_id),
        and_(WikiSpace.kind == 'shared', or_(WikiSpace.owner_id == user_id,
            range_access(user_id),
            exists(select(1).where(WikiSpaceMember.space_id == WikiSpace.id, WikiSpaceMember.user_id == user_id))))
    ))


def ensure_personal_space(db, user):
    space = db.scalar(select(WikiSpace).where(WikiSpace.personal_user_id == user.id))
    if space:
        return space
    space = WikiSpace(key='PERSONAL_' + secrets.token_hex(12).upper(), name='我的个人知识库',
        description='仅自己可见的笔记、方案和经验', kind='personal', personal_user_id=user.id,
        owner_id=user.id, created_by=user.id, members_initialized=True)
    # A unique owner index also protects concurrent first visits.
    try:
        with db.begin_nested():
            db.add(space); db.flush()
    except IntegrityError:
        space = db.scalar(select(WikiSpace).where(WikiSpace.personal_user_id == user.id).with_for_update())
        if not space:
            raise
    return space


def get_space(db, space_id, user, *, manage=False, write=False, lock=False):
    query = select(WikiSpace).where(WikiSpace.id == space_id, accessible_spaces(user.id))
    if lock: query = query.with_for_update().execution_options(populate_existing=True)
    space = db.scalar(query)
    if not space: raise HTTPException(404, DENIED)
    if lock: db.info['wiki_locked'] = space.id
    role = space_role(db, space, user)
    if manage and role != 'owner': raise HTTPException(403, '只有空间负责人可以管理空间及成员')
    if write and role not in {'owner', 'editor'}: raise HTTPException(403, '当前为只读成员，请联系空间负责人申请编辑权限')
    return space


def space_role(db, space, user):
    if space.kind == 'personal':
        return 'owner' if space.owner_id == user.id else None
    if space.owner_id == user.id: return 'owner'
    query = select(WikiSpaceMember).where(WikiSpaceMember.space_id == space.id, WikiSpaceMember.user_id == user.id)
    if db.info.get('wiki_locked') == space.id: query = query.with_for_update().execution_options(populate_existing=True)
    member = db.scalar(query)
    if member: return member.role
    return 'viewer' if db.scalar(select(WikiSpace.id).where(WikiSpace.id == space.id, accessible_spaces(user.id))) else None


def serialized(item):
    result = {}
    for col in item.__table__.columns:
        value = getattr(item, col.name)
        if isinstance(value, datetime): value = (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
        result[col.name] = value
    return result


def space_output(db, space, user):
    role = space_role(db, space, user)
    owner = db.get(User, space.owner_id)
    departments = db.execute(select(Department.id, Department.name, Department.active).join(WikiSpaceDepartment,
        WikiSpaceDepartment.department_id == Department.id).where(WikiSpaceDepartment.space_id == space.id).order_by(Department.id)).all()
    members = db.execute(select(User.id, User.display_name, User.username, User.active, WikiSpaceMember.role)
        .join(WikiSpaceMember, WikiSpaceMember.user_id == User.id).where(WikiSpaceMember.space_id == space.id)
        .order_by(User.id)).all() if role == 'owner' else []
    return {**serialized(space), 'owner_name': owner.display_name if owner else '未指定',
        'department_ids': [row.id for row in departments],
        'access_departments': [dict(row._mapping) for row in departments],
        **({'access_members': [dict(row._mapping) for row in members]} if role == 'owner' else {}),
        'my_role': role, 'can_manage': role == 'owner', 'can_write': role in {'owner', 'editor'},
        'article_count': db.scalar(select(func.count()).select_from(WikiArticle).where(
            WikiArticle.space_id == space.id, WikiArticle.status != 'archived')) or 0}


def get_article(db, item_id, user, *, edit=False, lock=False):
    item = db.get(WikiArticle, item_id)
    if not item: raise HTTPException(404, DENIED)
    space = get_space(db, item.space_id, user, write=edit, lock=lock)
    if edit and space_role(db, space, user) != 'owner' and item.author_id != user.id:
        raise HTTPException(403, '编辑成员只能修改本人创建的文档')
    return item, space


def article_output(db, item, space, user):
    author = db.get(User, item.author_id)
    role = space_role(db, space, user)
    return {**serialized(item), 'author_name': author.display_name if author else '原成员',
            'space_name': space.name, 'can_edit': role == 'owner' or (role == 'editor' and item.author_id == user.id)}


class SpaceIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    key: str = Field(min_length=2, max_length=40, pattern=r'^[A-Za-z0-9_-]+$')
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=300)
    category: str = Field(default='', max_length=60)
    kind: Literal['personal', 'shared'] | None = None
    owner_id: int | None = Field(default=None, gt=0)
    access_scope: Literal['members', 'departments', 'all'] | None = None
    include_subdepartments: bool | None = None
    department_ids: list[int] | None = Field(default=None, max_length=500)
    member_ids: list[int] | None = Field(default=None, max_length=2000)

    @field_validator('department_ids', 'member_ids')
    @classmethod
    def positive_ids(cls, values):
        if values is not None and any(value <= 0 for value in values): raise ValueError('请选择有效对象')
        return sorted(set(values)) if values is not None else None

    @field_validator('key')
    @classmethod
    def normalize_key(cls, value): return value.upper()


class MemberRoleIn(BaseModel):
    role: Literal['viewer', 'editor'] = 'viewer'


class SpaceDeleteIn(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=120)


class MemberIn(MemberRoleIn):
    user_id: int = Field(gt=0)


class ArticleIn(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    category: str = Field(min_length=1, max_length=60)
    content: str = Field(min_length=1)
    space_id: int | None = Field(default=None, gt=0)
    parent_id: int | None = Field(default=None, gt=0)
    status: Literal['draft', 'published', 'archived'] = 'published'
    version: int | None = Field(default=None, gt=0)
    revision: int | None = Field(default=None, gt=0)
    save_mode: Literal['manual', 'auto'] = 'manual'

    @field_validator('title', 'category', mode='before')
    @classmethod
    def trim_metadata(cls, value): return value.strip() if isinstance(value, str) else value

    @field_validator('content')
    @classmethod
    def require_content(cls, value):
        if not value.strip(): raise ValueError('正文内容不能为空')
        # Markdown indentation and trailing line breaks belong to the document.
        return value


def validate_parent(db, space, parent_id, item_id=None):
    seen = {item_id} if item_id else set()
    while parent_id:
        if parent_id in seen: raise HTTPException(400, '父级文档不能形成循环目录')
        seen.add(parent_id)
        parent = db.get(WikiArticle, parent_id)
        if not parent or parent.space_id != space.id: raise HTTPException(400, '父级文档必须属于同一知识空间')
        parent_id = parent.parent_id


def save_access(db, space, data, actor):
    """Validate the whole selection before changing any authorization rows."""
    scope = data.access_scope or space.access_scope
    department_ids = data.department_ids
    member_ids = data.member_ids
    if space.kind == 'personal':
        if scope != 'members' or department_ids or member_ids:
            raise HTTPException(400, '个人知识库仅自己可见，不能设置开放范围')
        return
    old_departments = set(db.scalars(select(WikiSpaceDepartment.department_id).where(WikiSpaceDepartment.space_id == space.id)))
    selected_departments = set(department_ids) if department_ids is not None else old_departments
    if scope == 'departments' and not selected_departments:
        raise HTTPException(400, '请至少选择一个开放部门')
    valid_departments = {d.id: d for d in db.scalars(select(Department).where(Department.id.in_(selected_departments)))}
    if any(key not in valid_departments or (not valid_departments[key].active and key not in old_departments)
           for key in selected_departments):
        raise HTTPException(400, '请选择存在且已启用的部门')
    old_members = {m.user_id: m for m in db.scalars(select(WikiSpaceMember).where(WikiSpaceMember.space_id == space.id))}
    selected_members = set(member_ids) - {space.owner_id} if member_ids is not None else set(old_members)
    people = {p.id: p for p in db.scalars(select(User).where(User.id.in_(selected_members)))}
    if any(key not in people or (not people[key].active and key not in old_members) for key in selected_members):
        raise HTTPException(400, '请选择存在且已启用的人员')
    space.access_scope = scope
    if data.include_subdepartments is not None: space.include_subdepartments = data.include_subdepartments
    for key in old_departments - selected_departments:
        db.execute(delete(WikiSpaceDepartment).where(WikiSpaceDepartment.space_id == space.id, WikiSpaceDepartment.department_id == key))
    for key in selected_departments - old_departments:
        db.add(WikiSpaceDepartment(space_id=space.id, department_id=key))
    for key in set(old_members) - selected_members: db.delete(old_members[key])
    for key in selected_members - set(old_members):
        db.add(WikiSpaceMember(space_id=space.id, user_id=key, role='viewer', added_by=actor.id))
    db.flush()


def register_routes(app, current_user):
    from . import audit

    @app.get('/api/wiki/access-options')
    def access_options(q: str = Query('', max_length=100), db: Session = Depends(get_db), user=Depends(current_user)):
        people = select(User).where(User.active.is_(True), User.id != user.id)
        if q.strip(): people = people.where(or_(*(field.contains(q.strip(), autoescape=True)
            for field in (User.display_name, User.username, User.department))))
        departments = db.scalars(select(Department).where(Department.active.is_(True)).order_by(Department.sort_order, Department.id)).all()
        from .organization import department_path
        return {'departments': [{'id': d.id, 'name': d.name, 'parent_id': d.parent_id, 'path': department_path(db, d)} for d in departments],
                'people': [{'id': p.id, 'display_name': p.display_name, 'username': p.username, 'active': p.active, 'department': p.department}
                           for p in db.scalars(people.order_by(User.display_name, User.id).limit(50))]}

    @app.get('/api/wiki/spaces')
    def spaces(db: Session = Depends(get_db), user=Depends(current_user)):
        ensure_personal_space(db, user); db.commit()
        return [space_output(db, space, user) for space in db.scalars(select(WikiSpace)
            .where(accessible_spaces(user.id)).order_by(WikiSpace.created_at, WikiSpace.id))]

    @app.post('/api/wiki/spaces')
    def create_space(data: SpaceIn, db: Session = Depends(get_db), user=Depends(current_user)):
        if data.key.startswith('PERSONAL_'): raise HTTPException(400, '该空间标识前缀为个人库保留')
        if data.kind == 'personal': raise HTTPException(400, '每位用户只有一个个人知识库，请在个人知识库中修改名称、说明或分类')
        if data.owner_id and data.owner_id != user.id: raise HTTPException(400, '创建人自动成为空间负责人，创建后可转让给已加入成员')
        space = WikiSpace(key=data.key, name=data.name, description=data.description, category=data.category,
                          owner_id=user.id, created_by=user.id, kind=data.kind or 'shared', members_initialized=True)
        db.add(space)
        try: db.flush()
        except IntegrityError:
            db.rollback(); raise HTTPException(409, '空间标识已存在')
        save_access(db, space, data, user)
        audit.record(db, user, 'create', 'wiki_space', space)
        db.commit()
        return space_output(db, space, user)

    @app.get('/api/wiki/spaces/trash')
    def trashed_spaces(db: Session = Depends(get_db), user=Depends(current_user)):
        # Deleted libraries are visible only to their owner, including to admins.
        return [space_output(db, space, user) for space in db.scalars(select(WikiSpace).where(
            WikiSpace.owner_id == user.id, WikiSpace.deleted_at.is_not(None)).order_by(WikiSpace.deleted_at.desc(), WikiSpace.id.desc()))]

    @app.delete('/api/wiki/spaces/{space_id}')
    def delete_space(space_id: int, data: SpaceDeleteIn, db: Session = Depends(get_db), user=Depends(current_user)):
        space = get_space(db, space_id, user, manage=True, lock=True)
        if space.kind == 'personal': raise HTTPException(400, '个人知识库是每位用户的固定私有空间，不支持删除')
        if data.confirm_name != space.name: raise HTTPException(400, '知识库名称不一致，请输入完整名称确认删除')
        audit.record(db, user, 'delete', 'wiki_space', space, before=audit.snapshot(db, 'wiki_space', space), after={})
        space.deleted_at = now()
        db.commit()
        return {'ok': True}

    @app.post('/api/wiki/spaces/{space_id}/restore')
    def restore_space(space_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        space = db.scalar(select(WikiSpace).where(WikiSpace.id == space_id, WikiSpace.owner_id == user.id,
            WikiSpace.deleted_at.is_not(None)).with_for_update())
        if not space: raise HTTPException(404, DENIED)
        space.deleted_at = None
        audit.record(db, user, 'restore', 'wiki_space', space)
        db.commit()
        return space_output(db, space, user)

    @app.put('/api/wiki/spaces/{space_id}')
    def update_space(space_id: int, data: SpaceIn, db: Session = Depends(get_db), user=Depends(current_user)):
        space = get_space(db, space_id, user, manage=True, lock=True)
        if data.key != space.key: raise HTTPException(400, '空间标识创建后不可修改')
        if data.kind is not None and data.kind != space.kind:
            raise HTTPException(400, '个人知识库与共享知识库不能互相转换，请修改共享空间的开放范围')
        before = audit.snapshot(db, 'wiki_space', space)
        save_access(db, space, data, user)
        owner_id = data.owner_id or space.owner_id
        if owner_id != space.owner_id:
            if space.kind == 'personal': raise HTTPException(400, '个人知识库不能转让或添加成员')
            owner = db.get(User, owner_id)
            if not owner or not owner.active or not db.get(WikiSpaceMember, (space.id, owner_id)):
                raise HTTPException(400, '新负责人必须是已加入空间且启用的成员')
            old_member = db.get(WikiSpaceMember, (space.id, space.owner_id))
            if old_member: old_member.role = 'editor'
            else: db.add(WikiSpaceMember(space_id=space.id, user_id=space.owner_id, role='editor', added_by=user.id))
            db.delete(db.get(WikiSpaceMember, (space.id, owner_id)))
            space.owner_id = owner_id
        space.name, space.description, space.category = data.name, data.description, data.category
        audit.record(db, user, 'update', 'wiki_space', space, before=before)
        db.commit()
        return space_output(db, space, user)

    def managed_shared(db, space_id, user):
        space = get_space(db, space_id, user, manage=True, lock=True)
        if space.kind != 'shared': raise HTTPException(400, '个人知识库不能转让或添加成员')
        return space

    def member_output(db, space, member):
        person = db.get(User, member.user_id)
        return {'user_id': person.id, 'display_name': person.display_name, 'username': person.username,
                'department': person.department, 'active': person.active, 'role': member.role}

    @app.get('/api/wiki/spaces/{space_id}/members')
    def members(space_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        space = managed_shared(db, space_id, user)
        return [member_output(db, space, member) for member in db.scalars(select(WikiSpaceMember)
            .where(WikiSpaceMember.space_id == space.id).order_by(WikiSpaceMember.created_at, WikiSpaceMember.user_id))]

    @app.get('/api/wiki/spaces/{space_id}/candidates')
    def candidates(space_id: int, q: str = Query('', max_length=100), db: Session = Depends(get_db), user=Depends(current_user)):
        space = managed_shared(db, space_id, user)
        query = select(User).where(User.active == True, User.id != space.owner_id,
            ~exists(select(1).where(WikiSpaceMember.space_id == space.id, WikiSpaceMember.user_id == User.id)))
        if q.strip(): query = query.where(or_(*(field.contains(q.strip(), autoescape=True)
            for field in [User.display_name, User.username, User.department])))
        return [{'id': person.id, 'display_name': person.display_name, 'username': person.username,
                 'department': person.department} for person in db.scalars(query.order_by(User.id).limit(50))]

    def log_member(db, user, space, person, old, new):
        label = lambda role: {'viewer':'只读', 'editor':'编辑', None:'未加入'}[role]
        audit.record(db, user, 'members', 'wiki_space', space, before={}, after={},
            extra={'members': (f'{person.display_name}（{person.username}）：{label(old)}',
                               f'{person.display_name}（{person.username}）：{label(new)}')})

    @app.post('/api/wiki/spaces/{space_id}/members')
    def add_member(space_id: int, data: MemberIn, db: Session = Depends(get_db), user=Depends(current_user)):
        space = managed_shared(db, space_id, user)
        person = db.get(User, data.user_id)
        if not person or not person.active: raise HTTPException(400, '请选择已启用成员')
        if person.id == space.owner_id or db.get(WikiSpaceMember, (space.id, person.id)):
            raise HTTPException(409, '该成员已在空间中')
        member = WikiSpaceMember(space_id=space.id, user_id=person.id, role=data.role, added_by=user.id)
        db.add(member); log_member(db, user, space, person, None, data.role); db.commit()
        return member_output(db, space, member)

    @app.patch('/api/wiki/spaces/{space_id}/members/{user_id}')
    def change_member(space_id: int, user_id: int, data: MemberRoleIn, db: Session = Depends(get_db), user=Depends(current_user)):
        space = managed_shared(db, space_id, user)
        member = db.get(WikiSpaceMember, (space.id, user_id))
        if not member: raise HTTPException(404, '空间成员不存在')
        if member.role != data.role:
            log_member(db, user, space, db.get(User, user_id), member.role, data.role)
            member.role = data.role
        db.commit(); return member_output(db, space, member)

    @app.delete('/api/wiki/spaces/{space_id}/members/{user_id}')
    def remove_member(space_id: int, user_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        space = managed_shared(db, space_id, user)
        member = db.get(WikiSpaceMember, (space.id, user_id))
        if not member: raise HTTPException(404, '空间成员不存在')
        log_member(db, user, space, db.get(User, user_id), member.role, None)
        db.delete(member); db.flush()
        remaining = space_role(db, space, db.get(User, user_id))
        db.commit(); return {'ok': True, 'still_has_access': bool(remaining)}

    @app.get('/api/wiki')
    def articles(space_id: int | None = None, parent_id: int | None = None, scope: Literal['personal', 'shared'] | None = None,
                 status: Literal['all', 'draft', 'published', 'archived'] = 'all', q: str = Query('', max_length=200),
                 db: Session = Depends(get_db), user=Depends(current_user)):
        space = get_space(db, space_id, user) if space_id else None
        if not space and scope != 'shared':
            space = ensure_personal_space(db, user); db.commit()
        query = select(WikiArticle, WikiSpace).join(WikiSpace, WikiSpace.id == WikiArticle.space_id).where(accessible_spaces(user.id))
        if space: query = query.where(WikiSpace.id == space.id)
        if scope: query = query.where(WikiSpace.kind == scope)
        if parent_id:
            parent, parent_space = get_article(db, parent_id, user)
            if space and parent_space.id != space.id: raise HTTPException(400, '父级文档必须属于同一知识空间')
            query = query.where(WikiArticle.parent_id == parent.id)
        if status != 'all': query = query.where(WikiArticle.status == status)
        if q.strip(): query = query.where(or_(*(field.contains(q.strip(), autoescape=True) for field in [WikiArticle.title, WikiArticle.content])))
        return [article_output(db, item, parent_space, user) for item, parent_space in db.execute(query.order_by(WikiArticle.updated_at.desc(), WikiArticle.id.desc()))]

    @app.post('/api/wiki')
    def create_article(data: ArticleIn, db: Session = Depends(get_db), user=Depends(current_user)):
        if data.save_mode != 'manual': raise HTTPException(400, '新文档请点击保存文档后创建')
        target_id = data.space_id or ensure_personal_space(db, user).id
        space = get_space(db, target_id, user, write=True, lock=True)
        validate_parent(db, space, data.parent_id)
        item = WikiArticle(**{**data.model_dump(exclude={'version', 'revision', 'save_mode'}), 'space_id': space.id}, author_id=user.id, version=1, revision=1)
        db.add(item); db.flush()
        db.add(WikiArticleVersion(article_id=item.id, version=1, title=item.title, category=item.category,
                                 content=item.content, status=item.status, author_id=user.id))
        audit.record(db, user, 'create', 'wiki', item)
        db.commit(); return article_output(db, item, space, user)

    @app.get('/api/wiki/{item_id}')
    def article(item_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        item, space = get_article(db, item_id, user)
        return article_output(db, item, space, user)

    @app.put('/api/wiki/{item_id}')
    def update_article(item_id: int, data: ArticleIn, db: Session = Depends(get_db), user=Depends(current_user)):
        item, space = get_article(db, item_id, user, edit=True, lock=True)
        # Lock serializes concurrent editors as well as permission revocation.
        item = db.scalar(select(WikiArticle).where(WikiArticle.id == item_id).with_for_update().execution_options(populate_existing=True))
        if data.space_id and data.space_id != item.space_id: raise HTTPException(400, '文档创建后不能更换所属空间')
        validate_parent(db, space, data.parent_id, item.id)
        before = audit.snapshot(db, 'wiki', item)
        payload = {**data.model_dump(exclude={'version', 'revision', 'save_mode'}), 'space_id': space.id}
        changed = any(getattr(item, key) != value for key, value in payload.items())
        manual = data.save_mode == 'manual'
        # A separate revision protects every autosave, even when the visible
        # document version has not changed. Old clients must reload after upgrade.
        stale = data.revision is None or data.revision != item.revision or data.version != item.version
        # Exact retries never add a second snapshot. A new click uses the current
        # revision/version and does create one, including after a clean autosave.
        retried = not changed and data.revision is not None and (
            (not manual) or (data.version == item.version - 1 and data.revision == item.revision - 1))
        if stale and not retried:
            raise HTTPException(409, '文档已被其他窗口或成员更新，自动保存已暂停。请保留当前草稿后读取最新版本。')
        if not stale and (changed or manual):
            for key, value in payload.items(): setattr(item, key, value)
            item.revision += 1; item.updated_at = now()
            if manual:
                item.version += 1
                db.add(WikiArticleVersion(article_id=item.id, version=item.version, title=item.title, category=item.category,
                                         content=item.content, status=item.status, author_id=user.id))
                audit.record(db, user, 'update', 'wiki', item, before=before)
        db.commit(); return article_output(db, item, space, user)

    @app.get('/api/wiki/{item_id}/versions')
    def versions(item_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
        item, _ = get_article(db, item_id, user)
        return [{**serialized(version), 'author_name': db.get(User, version.author_id).display_name}
                for version in db.scalars(select(WikiArticleVersion).where(WikiArticleVersion.article_id == item.id)
                    .order_by(WikiArticleVersion.version.desc()))]
