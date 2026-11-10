from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base
from .security import decrypt_secret, encrypt_secret, is_encrypted_secret


def now():
    return datetime.now(timezone.utc)


class EncryptedOpenId(TypeDecorator):
    """Encrypt at the database boundary while application code sees plain IDs."""
    impl = String(500)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if not value:
            return value
        return value if is_encrypted_secret(value) else encrypt_secret(value)

    def process_result_value(self, value, dialect):
        if not value:
            return value
        return decrypt_secret(value) if is_encrypted_secret(value) else value


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    department: Mapped[str] = mapped_column(String(100), default="")
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(30), index=True)  # primary grant; see roles.py
    password_hash: Mapped[str] = mapped_column(String(255))
    feishu_open_id: Mapped[str | None] = mapped_column(EncryptedOpenId(), nullable=True, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    role_grants: Mapped[list["UserRole"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    organization_department: Mapped["Department | None"] = relationship(foreign_keys=[department_id], lazy="joined")
    department_memberships: Mapped[list["DepartmentMembership"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    position_grants: Mapped[list["DepartmentUserPosition"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    organization_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class ResourceRequest(Base):
    __tablename__ = "resource_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    application_department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    project_name: Mapped[str] = mapped_column(String(150), index=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    environment: Mapped[str] = mapped_column(String(30))
    resource_type: Mapped[str] = mapped_column(String(80))
    specification: Mapped[str] = mapped_column(Text)
    # JSON object of values captured against the resource type catalog.  The
    # rendered specification above remains the audit/history friendly summary.
    specification_values: Mapped[str] = mapped_column(Text, default="{}", server_default=text("('{}')"))
    start_date: Mapped[str] = mapped_column(String(30))
    end_date: Mapped[str] = mapped_column(String(30))
    purpose: Mapped[str] = mapped_column(Text)
    capacity_basis: Mapped[str] = mapped_column(Text)
    ports: Mapped[str] = mapped_column(Text, default="")
    whitelist: Mapped[str] = mapped_column(Text, default="")
    fulfillment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    stage: Mapped[str] = mapped_column(String(30), default="department_head")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default='1')
    reminder_revision: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    reminder_updated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    reminder_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChangeRequest(Base):
    __tablename__ = "change_requests"
    environment: Mapped[str] = mapped_column(String(30), default="生产")
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    application_department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(180))
    change_type: Mapped[str] = mapped_column(String(50))
    window_start: Mapped[str] = mapped_column(String(40))
    window_end: Mapped[str] = mapped_column(String(40))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ops_owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    plan: Mapped[str] = mapped_column(Text, default="")
    problem_description: Mapped[str] = mapped_column(Text, default="")
    rollback_plan: Mapped[str] = mapped_column(Text, default="")
    implementation: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    stage: Mapped[str] = mapped_column(String(30), default="ops_head")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class DeliveryProject(Base):
    __tablename__ = "delivery_projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    application_department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(180))
    customer: Mapped[str] = mapped_column(String(180))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    cloud_type: Mapped[str] = mapped_column(String(30))
    deployment_mode: Mapped[str] = mapped_column(String(30))
    environment: Mapped[str] = mapped_column(String(30))
    compute_type: Mapped[str] = mapped_column(String(30))
    accelerator: Mapped[str] = mapped_column(String(30), default="无")
    network_notes: Mapped[str] = mapped_column(Text, default="")
    db_whitelist: Mapped[str] = mapped_column(Text, default="")
    security_group: Mapped[str] = mapped_column(Text, default="")
    account_validation: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    stage: Mapped[str] = mapped_column(String(30), default="ops_head")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    credentials: Mapped[list["DeliveryCredential"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class DeliveryCredential(Base):
    __tablename__ = "delivery_credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("delivery_projects.id", ondelete="CASCADE"), index=True)
    db_type: Mapped[str] = mapped_column(String(30))
    host: Mapped[str] = mapped_column(String(180), default="")
    username: Mapped[str] = mapped_column(String(100))
    password_ciphertext: Mapped[str] = mapped_column(Text)
    privilege: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DeliveryAttachment(Base):
    __tablename__ = 'delivery_attachments'
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey('delivery_projects.id', ondelete='CASCADE'), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    storage_backend: Mapped[str] = mapped_column(String(16), default='local', server_default='local')
    storage_bucket: Mapped[str] = mapped_column(String(63), default='', server_default='')
    extension: Mapped[str] = mapped_column(String(10))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChangeAttachment(Base):
    __tablename__ = 'change_attachments'
    id: Mapped[int] = mapped_column(primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey('change_requests.id', ondelete='CASCADE'), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    storage_backend: Mapped[str] = mapped_column(String(16), default='local', server_default='local')
    storage_bucket: Mapped[str] = mapped_column(String(63), default='', server_default='')
    extension: Mapped[str] = mapped_column(String(10))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LicenseRequest(Base):
    __tablename__ = "license_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    application_department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    project_name: Mapped[str] = mapped_column(String(150))
    customer: Mapped[str] = mapped_column(String(180))
    product: Mapped[str] = mapped_column(String(150))
    tenant_id: Mapped[int] = mapped_column(Integer)
    expire_time: Mapped[int] = mapped_column(BigInteger)
    account_number: Mapped[int] = mapped_column(Integer, default=-1)
    agent_number: Mapped[int] = mapped_column(Integer, default=-1)
    purpose: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    stage: Mapped[str] = mapped_column(String(30), default="awaiting_workflow")
    final_approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    license_key_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default='1')
    reminder_revision: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    reminder_updated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    reminder_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    node_run_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_nodes.id"), nullable=True, index=True)
    assignee_snapshot: Mapped[str] = mapped_column(Text, default="{}")
    object_type: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(30), default="pending")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index("ix_approvals_object", "object_type", "object_id"),)


class WikiArticle(Base):
    __tablename__ = "wiki_articles"
    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int | None] = mapped_column(ForeignKey("wiki_spaces.id"), nullable=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("wiki_articles.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(180))
    category: Mapped[str] = mapped_column(String(60))
    content: Mapped[str] = mapped_column(Text)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="published", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class WikiSpace(Base):
    __tablename__ = "wiki_spaces"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(300), default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), default='shared', server_default='shared')
    category: Mapped[str] = mapped_column(String(60), default='', server_default='')
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    personal_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, unique=True)
    access_scope: Mapped[str] = mapped_column(String(20), default='members', server_default='members')
    include_subdepartments: Mapped[bool] = mapped_column(Boolean, default=True, server_default='1')
    members_initialized: Mapped[bool] = mapped_column(Boolean, default=True, server_default='1')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WikiSpaceDepartment(Base):
    __tablename__ = 'wiki_space_departments'
    space_id: Mapped[int] = mapped_column(ForeignKey('wiki_spaces.id', ondelete='CASCADE'), primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey('departments.id'), primary_key=True, index=True)


class WikiSpaceMember(Base):
    __tablename__ = 'wiki_space_members'
    space_id: Mapped[int] = mapped_column(ForeignKey('wiki_spaces.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default='viewer')
    added_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WikiArticleVersion(Base):
    __tablename__ = "wiki_article_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("wiki_articles.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(180))
    category: Mapped[str] = mapped_column(String(60))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="published")
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    record_kind: Mapped[str] = mapped_column(String(20), default="request", server_default="request")
    actor_name: Mapped[str] = mapped_column(String(80), default="", server_default="")
    actor_username: Mapped[str] = mapped_column(String(80), default="", server_default="")
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str] = mapped_column(String(50), default="")
    target_id: Mapped[str] = mapped_column(String(50), default="")
    target_code: Mapped[str] = mapped_column(String(80), default="", server_default="")
    target_name: Mapped[str] = mapped_column(String(180), default="", server_default="")
    summary: Mapped[str] = mapped_column(String(600), default="", server_default="")
    changes: Mapped[str | None] = mapped_column(Text, nullable=True)
    wiki_space_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status_code: Mapped[int] = mapped_column(Integer)
    ip_address: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    __table_args__ = (Index("ix_audit_logs_kind_time", "record_kind", "created_at", "id"),)


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    event: Mapped[str] = mapped_column(String(80))
    object_type: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Existing pending rows are historical; enabling a worker must not mass-send them.
    dispatch_ready: Mapped[bool] = mapped_column(Boolean, default=True, server_default='0')
    __table_args__ = (Index('ix_notifications_dispatch', 'status', 'next_attempt_at', 'id'),)


class ReminderPlan(Base):
    __tablename__ = 'reminder_plans'
    id: Mapped[int] = mapped_column(primary_key=True)
    object_type: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[int] = mapped_column(Integer)
    period: Mapped[str] = mapped_column(String(100))
    instance_id: Mapped[int | None] = mapped_column(ForeignKey('workflow_instances.id'), nullable=True)
    rule: Mapped[str] = mapped_column(String(30))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(20), default='active')
    note: Mapped[str] = mapped_column(String(180), default='')
    __table_args__ = (
        UniqueConstraint('object_type', 'object_id', 'period', 'rule', name='uq_reminder_plan'),
        Index('ix_reminder_plans_due', 'state', 'scheduled_at', 'id'),
        Index('ix_reminder_plans_object', 'object_type', 'object_id'),
    )


class ReminderDelivery(Base):
    __tablename__ = 'reminder_deliveries'
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('reminder_plans.id'), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    notification_id: Mapped[int] = mapped_column(ForeignKey('notification_logs.id'), unique=True)
    __table_args__ = (UniqueConstraint('plan_id', 'recipient_id', name='uq_reminder_delivery'),)


class ResourceReminderContact(Base):
    __tablename__ = 'resource_reminder_contacts'
    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey('resource_requests.id'), index=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey('workflow_instances.id'))
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    source_task_id: Mapped[int | None] = mapped_column(ForeignKey('approvals.id'), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default='execution')
    reason: Mapped[str] = mapped_column(String(500), default='')
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    __table_args__ = (UniqueConstraint('resource_id', 'instance_id', 'user_id', name='uq_resource_reminder_contact'),)


class PersonalReminder(Base):
    __tablename__ = 'personal_reminders'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    content: Mapped[str] = mapped_column(Text)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(20), default='draft')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index('ix_personal_reminders_owner', 'owner_id', 'deleted_at', 'id'),
                      Index('ix_personal_reminders_due', 'state', 'remind_at', 'id'))


class PersonalReminderRecipient(Base):
    __tablename__ = 'personal_reminder_recipients'
    reminder_id: Mapped[int] = mapped_column(ForeignKey('personal_reminders.id'), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    notification_id: Mapped[int | None] = mapped_column(ForeignKey('notification_logs.id'), nullable=True, unique=True)


class PersonalReminderAttachment(Base):
    __tablename__ = 'personal_reminder_attachments'
    id: Mapped[int] = mapped_column(primary_key=True)
    reminder_id: Mapped[int] = mapped_column(ForeignKey('personal_reminders.id'), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    storage_backend: Mapped[str] = mapped_column(String(16), default='local', server_default='local')
    storage_bucket: Mapped[str] = mapped_column(String(63), default='', server_default='')
    extension: Mapped[str] = mapped_column(String(10))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RequestRateLimit(Base):
    __tablename__ = "request_rate_limits"
    subject: Mapped[str] = mapped_column(String(80), primary_key=True)
    attempts: Mapped[str] = mapped_column(Text, default="[]")
    alert_after_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_ms: Mapped[int] = mapped_column(BigInteger, index=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class WorkflowAssignment(Base):
    __tablename__ = "workflow_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    object_type: Mapped[str] = mapped_column(String(30))
    stage: Mapped[str] = mapped_column(String(30))
    department: Mapped[str] = mapped_column(String(100), default="")
    assignee_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (UniqueConstraint("object_type", "stage", "department", name="uq_workflow_route"),)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(20), default="login")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginTicket(Base):
    __tablename__ = "login_tickets"
    ticket_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)



class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(500), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    approval_mode: Mapped[str] = mapped_column(String(30), default="designated_managers")
    managers: Mapped[list["DepartmentManager"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class DepartmentAlias(Base):
    __tablename__ = "department_aliases"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)


class DepartmentMembership(Base):
    __tablename__ = "department_memberships"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), primary_key=True, index=True)
    department: Mapped["Department"] = relationship(lazy="joined")


class DepartmentManager(Base):
    __tablename__ = "department_managers"
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)


class DepartmentPosition(Base):
    __tablename__ = "department_positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    manager_assignable: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    department: Mapped["Department"] = relationship(lazy="joined")
    role_grants: Mapped[list["DepartmentPositionRole"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    __table_args__ = (UniqueConstraint("department_id", "name", name="uq_department_position_name"),)


class DepartmentPositionRole(Base):
    __tablename__ = "department_position_roles"
    position_id: Mapped[int] = mapped_column(ForeignKey("department_positions.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(30), primary_key=True)


class DepartmentUserPosition(Base):
    __tablename__ = "department_user_positions"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("department_positions.id"), primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), primary_key=True, index=True)
    position: Mapped["DepartmentPosition"] = relationship(lazy="joined")
    department: Mapped["Department"] = relationship(lazy="joined")


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(30), primary_key=True)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    department: Mapped[str] = mapped_column(String(100))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProjectMember(Base):
    __tablename__ = "project_members"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(30), primary_key=True)


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"
    kind: Mapped[str] = mapped_column(String(30), primary_key=True)
    draft: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[str] = mapped_column(Text)
    published_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("kind", "version", name="uq_flow_version"),)


class WorkflowInstance(Base):
    __tablename__ = "workflow_instances"
    id: Mapped[int] = mapped_column(primary_key=True)
    object_type: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[int] = mapped_column(Integer)
    round: Mapped[int] = mapped_column(Integer)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_versions.id"), nullable=True)
    snapshot: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("object_type", "object_id", "round", name="uq_flow_round"),)


class WorkflowNode(Base):
    __tablename__ = "workflow_nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("workflow_instances.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(150))
    node_type: Mapped[str] = mapped_column(String(30))
    mode: Mapped[str] = mapped_column(String(10))
    config: Mapped[str] = mapped_column(Text)
    assignment_policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="waiting")
    __table_args__ = (UniqueConstraint("instance_id", "position", name="uq_instance_position"),)


class TaskTransfer(Base):
    __tablename__ = "task_transfers"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("approvals.id"), index=True)
    from_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
