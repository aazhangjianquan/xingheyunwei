"""Small additive upgrade for the first template. Full SQL is in database/."""
from sqlalchemy import inspect, text
from .security import encrypt_secret, is_encrypted_secret


def upgrade(engine):
    upgrade_position_assignments(engine)
    inspector = inspect(engine)
    if "approvals" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("approvals")}
        if "assignee_id" not in columns:
            with engine.begin() as conn:
                ddl = "ALTER TABLE approvals ADD COLUMN assignee_id INTEGER NULL REFERENCES users(id)"
                if engine.dialect.name == "mysql":
                    ddl = "ALTER TABLE approvals ADD COLUMN assignee_id INTEGER NULL, ADD CONSTRAINT fk_approval_assignee FOREIGN KEY (assignee_id) REFERENCES users(id)"
                conn.execute(text(ddl))
                conn.execute(text("CREATE INDEX ix_approvals_assignee_id ON approvals (assignee_id)"))
    if "users" in inspector.get_table_names():
        if engine.dialect.name == 'mysql':
            column = next((c for c in inspector.get_columns('users') if c['name'] == 'feishu_open_id'), None)
            if column and '500' not in str(column['type']):
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE users MODIFY COLUMN feishu_open_id VARCHAR(500) NULL"))
        unique_sets = {tuple(c["column_names"]) for c in inspector.get_unique_constraints("users")}
        unique_sets |= {tuple(i["column_names"]) for i in inspector.get_indexes("users") if i.get("unique")}
        if ("feishu_open_id",) not in unique_sets:
            with engine.begin() as conn:
                conn.execute(text("UPDATE users SET feishu_open_id = NULL WHERE feishu_open_id = ''"))
                conn.execute(text("CREATE UNIQUE INDEX uq_users_feishu_open_id ON users(feishu_open_id)"))
        with engine.begin() as conn:
            rows = conn.execute(text("SELECT id, feishu_open_id FROM users WHERE feishu_open_id IS NOT NULL AND feishu_open_id <> ''")).mappings().all()
            for row in rows:
                value = row['feishu_open_id']
                if not is_encrypted_secret(value):
                    conn.execute(text("UPDATE users SET feishu_open_id=:value WHERE id=:id"), {'value': encrypt_secret(value), 'id': row['id']})

    # Nullable links preserve legacy records without guessing project ownership.
    additions = {
        'users': {'department_id': 'INTEGER NULL', 'organization_revision': 'INTEGER NOT NULL DEFAULT 1'},
        'projects': {'department_id': 'INTEGER NULL'},
        'workflow_nodes': {'assignment_policy': 'TEXT NULL'},
        'notification_logs': {'attempt_count': 'INTEGER NOT NULL DEFAULT 0', 'next_attempt_at': 'DATETIME NULL',
                              'dispatch_ready': 'BOOLEAN NOT NULL DEFAULT 0'},
        "audit_logs": {
            "record_kind": "VARCHAR(20) NOT NULL DEFAULT 'request'",
            "actor_name": "VARCHAR(80) NOT NULL DEFAULT ''",
            "actor_username": "VARCHAR(80) NOT NULL DEFAULT ''",
            "target_code": "VARCHAR(80) NOT NULL DEFAULT ''",
            "target_name": "VARCHAR(180) NOT NULL DEFAULT ''",
            "summary": "VARCHAR(600) NOT NULL DEFAULT ''",
            "changes": "TEXT NULL",
            "wiki_space_id": "INTEGER NULL",
        },
        "resource_requests": {"project_id": "INTEGER NULL", "specification_values": "TEXT NULL", "deleted_at": "DATETIME NULL"},
        "change_requests": {"project_id": "INTEGER NULL", "environment": "VARCHAR(30) NOT NULL DEFAULT '生产'", "deleted_at": "DATETIME NULL", "problem_description": "TEXT NULL", "plan": "TEXT NOT NULL DEFAULT ('')", "rollback_plan": "TEXT NOT NULL DEFAULT ('')", "implementation": "TEXT NOT NULL DEFAULT ('')"},
        "delivery_projects": {"project_id": "INTEGER NULL", "deleted_at": "DATETIME NULL"},
        "delivery_attachments": {
            "storage_backend": "VARCHAR(16) NOT NULL DEFAULT 'local'",
            "storage_bucket": "VARCHAR(63) NOT NULL DEFAULT ''",
        },
        "approvals": {"node_run_id": "INTEGER NULL", "assignee_snapshot": "TEXT NULL"},
        "license_requests": {"project_id": "INTEGER NULL", "stage": "VARCHAR(30) NOT NULL DEFAULT 'awaiting_workflow'", "final_approver_id": "INTEGER NULL", "deleted_at": "DATETIME NULL"},
        "wiki_articles": {"space_id": "INTEGER NULL", "parent_id": "INTEGER NULL", "status": "VARCHAR(20) NOT NULL DEFAULT 'published'", "version": "INTEGER NOT NULL DEFAULT 1", "revision": "INTEGER NOT NULL DEFAULT 1"},
        "wiki_spaces": {"access_scope": "VARCHAR(20) NOT NULL DEFAULT 'members'",
                        "include_subdepartments": "BOOLEAN NOT NULL DEFAULT 1", "owner_id": "INTEGER NULL", "kind": "VARCHAR(20) NOT NULL DEFAULT 'shared'",
                        "personal_user_id": "INTEGER NULL", "members_initialized": "BOOLEAN NOT NULL DEFAULT 0",
                        "category": "VARCHAR(60) NOT NULL DEFAULT ''", "deleted_at": "DATETIME NULL"},
    }
    for table in ('resource_requests', 'change_requests', 'delivery_projects', 'license_requests'):
        additions[table]['application_department_id'] = 'INTEGER NULL'
    for table in ('resource_requests', 'license_requests'):
        additions[table].update(reminder_enabled='BOOLEAN NOT NULL DEFAULT 1',
            reminder_revision='INTEGER NOT NULL DEFAULT 1', reminder_updated_by='INTEGER NULL',
            reminder_updated_at='DATETIME NULL')
    for table, fields in additions.items():
        if table not in inspector.get_table_names():
            continue
        columns = {c["name"] for c in inspect(engine).get_columns(table)}
        with engine.begin() as conn:
            for name, ddl in fields.items():
                if name not in columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    if name.endswith("_id"):
                        conn.execute(text(f"CREATE INDEX ix_{table}_{name} ON {table} ({name})"))
    if 'notification_logs' in inspector.get_table_names():
        indexes = {i['name'] for i in inspect(engine).get_indexes('notification_logs')}
        if 'ix_notifications_dispatch' not in indexes:
            with engine.begin() as conn:
                conn.execute(text('CREATE INDEX ix_notifications_dispatch ON notification_logs (status, next_attempt_at, id)'))
    if 'change_requests' in inspect(engine).get_table_names():
        with engine.begin() as conn:
            conn.execute(text("UPDATE change_requests SET plan='' WHERE plan IS NULL"))
            conn.execute(text("UPDATE change_requests SET rollback_plan='' WHERE rollback_plan IS NULL"))
            conn.execute(text("UPDATE change_requests SET implementation='' WHERE implementation IS NULL"))
    if 'audit_logs' in inspector.get_table_names():
        indexes = {i['name'] for i in inspect(engine).get_indexes('audit_logs')}
        if 'ix_audit_logs_kind_time' not in indexes:
            with engine.begin() as conn:
                conn.execute(text('CREATE INDEX ix_audit_logs_kind_time ON audit_logs (record_kind, created_at, id)'))
    if 'wiki_spaces' in inspector.get_table_names():
        indexes = {i['name'] for i in inspect(engine).get_indexes('wiki_spaces')}
        unique = {tuple(item['column_names']) for item in inspect(engine).get_unique_constraints('wiki_spaces')}
        if ('personal_user_id',) not in unique and 'uq_wiki_personal_user' not in indexes:
            with engine.begin() as conn:
                conn.execute(text('CREATE UNIQUE INDEX uq_wiki_personal_user ON wiki_spaces (personal_user_id)'))



def upgrade_position_assignments(engine):
    """Keep the original grants while separating assignment and definition scopes."""
    table = 'department_user_positions'
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    columns = {column['name']: column for column in inspector.get_columns(table)}
    primary = inspector.get_pk_constraint(table)['constrained_columns']
    expected = ['user_id', 'position_id', 'department_id']
    if engine.dialect.name == 'sqlite':
        if 'department_id' in columns and primary == expected:
            return
        # SQLite 不支持修改主键；在同一事务内复制原行，失败时保留旧表。
        scope = 'COALESCE(g.department_id, p.department_id)' if 'department_id' in columns else 'p.department_id'
        with engine.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            invalid = conn.scalar(text(f'SELECT COUNT(*) FROM {table} g LEFT JOIN department_positions p '
                                       f'ON p.id=g.position_id WHERE {scope} IS NULL'))
            if invalid:
                raise RuntimeError('岗位分配迁移发现失效岗位，请先修复原数据')
            conn.execute(text('CREATE TABLE department_user_positions_v106 ('
                'user_id INTEGER NOT NULL REFERENCES users(id), '
                'position_id INTEGER NOT NULL REFERENCES department_positions(id), '
                'department_id INTEGER NOT NULL REFERENCES departments(id), '
                'PRIMARY KEY (user_id, position_id, department_id))'))
            conn.execute(text(f'INSERT INTO department_user_positions_v106 '
                f'(user_id, position_id, department_id) SELECT g.user_id, g.position_id, {scope} '
                f'FROM {table} g LEFT JOIN department_positions p ON p.id=g.position_id'))
            conn.execute(text(f'DROP TABLE {table}'))
            conn.execute(text(f'ALTER TABLE department_user_positions_v106 RENAME TO {table}'))
            conn.execute(text(f'CREATE INDEX ix_{table}_department_id ON {table} (department_id)'))
        return
    if engine.dialect.name != 'mysql':
        raise RuntimeError('岗位继承迁移仅支持 MySQL 与 SQLite')
    with engine.begin() as conn:
        if 'department_id' not in columns:
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN department_id INTEGER NULL'))
        conn.execute(text(f'UPDATE {table} g JOIN department_positions p ON p.id=g.position_id '
                          'SET g.department_id=p.department_id WHERE g.department_id IS NULL'))
        if conn.scalar(text(f'SELECT COUNT(*) FROM {table} WHERE department_id IS NULL')):
            raise RuntimeError('岗位分配迁移发现失效岗位，请先修复原数据')
        changes = []
        if 'department_id' not in columns or columns['department_id']['nullable']:
            changes.append('MODIFY COLUMN department_id INTEGER NOT NULL')
        if primary != expected:
            changes += ['DROP PRIMARY KEY', 'ADD PRIMARY KEY (user_id, position_id, department_id)']
        if not any(index['column_names'] == ['department_id'] for index in inspector.get_indexes(table)):
            changes.append(f'ADD INDEX ix_{table}_department_id (department_id)')
        if not any(fk['constrained_columns'] == ['department_id'] for fk in inspector.get_foreign_keys(table)):
            changes.append('ADD CONSTRAINT fk_position_assignment_department FOREIGN KEY (department_id) REFERENCES departments(id)')
        if changes:
            conn.execute(text(f'ALTER TABLE {table} ' + ', '.join(changes)))


def finish_upgrade(engine):
    """Add references after target tables exist (V3 upgrade also works via SQL)."""
    links = [('resource_requests','project_id','projects'), ('change_requests','project_id','projects'),
             ('delivery_projects','project_id','projects'), ('approvals','node_run_id','workflow_nodes')]
    links += [('users', 'department_id', 'departments'), ('projects', 'department_id', 'departments')]
    links += [(table, 'application_department_id', 'departments') for table in
              ('resource_requests', 'change_requests', 'delivery_projects', 'license_requests')]
    if 'license_requests' in inspect(engine).get_table_names():
        links.append(('license_requests', 'project_id', 'projects'))
        links.append(('license_requests', 'final_approver_id', 'users'))
    for table, column, target in links:
        inspector = inspect(engine)
        indexes = {i['name'] for i in inspector.get_indexes(table)}
        with engine.begin() as conn:
            if f'ix_{table}_{column}' not in indexes:
                conn.execute(text(f'CREATE INDEX ix_{table}_{column} ON {table} ({column})'))
            if engine.dialect.name == 'mysql' and not any(fk['constrained_columns'] == [column] for fk in inspector.get_foreign_keys(table)):
                conn.execute(text(f'ALTER TABLE {table} ADD CONSTRAINT fk_v3_{table}_{column} FOREIGN KEY ({column}) REFERENCES {target}(id)'))


def seed_knowledge(db):
    """Initialize after create_all and user seeding; safe on first boot and restart."""
    from sqlalchemy import select, update, cast, String
    from .models import User, WikiSpace, WikiSpaceMember, WikiArticle, WikiArticleVersion, AuditLog
    from .knowledge import ensure_personal_space
    from .roles import has_role
    people = db.scalars(select(User).order_by(User.id)).all()
    if not people:
        return
    owner = next((person for person in people if person.active and has_role(person, 'admin')), people[0])
    default = db.scalar(select(WikiSpace).where(WikiSpace.key == 'OPS'))
    if not default:
        default = WikiSpace(key='OPS', name='运维知识库', description='团队规范、交付经验与故障复盘',
                            created_by=owner.id, owner_id=owner.id, members_initialized=False)
        db.add(default)
        db.flush()
    for space in db.scalars(select(WikiSpace).where(WikiSpace.owner_id.is_(None))):
        space.owner_id = space.created_by
    # Associating legacy articles must not look like a content edit or change
    # the original timestamp used by the first version snapshot.
    db.execute(update(WikiArticle).where(WikiArticle.space_id.is_(None))
               .values(space_id=default.id, updated_at=WikiArticle.updated_at))
    existing = select(WikiArticleVersion.article_id).distinct()
    for article in db.scalars(select(WikiArticle).where(WikiArticle.id.not_in(existing))):
        db.add(WikiArticleVersion(article_id=article.id, version=article.version or 1, title=article.title,
                                 category=article.category, content=article.content, status=article.status,
                                 author_id=article.author_id, created_at=article.updated_at))
    # This per-space marker is never reset on restart: revoked authors stay revoked.
    for space in db.scalars(select(WikiSpace).where(WikiSpace.members_initialized == False)):
        if space.kind == 'shared':
            authors = db.scalars(select(WikiArticle.author_id).where(WikiArticle.space_id == space.id).distinct())
            for author_id in authors:
                if author_id != space.owner_id and not db.get(WikiSpaceMember, (space.id, author_id)):
                    db.add(WikiSpaceMember(space_id=space.id, user_id=author_id, role='editor', added_by=space.owner_id))
        space.members_initialized = True
    for person in people: ensure_personal_space(db, person)
    # Resolve historic audit scopes once; unknown/deleted targets remain hidden.
    article_id, space_id = cast(WikiArticle.id, String), cast(WikiSpace.id, String)
    if db.bind.dialect.name == 'mysql':
        # CAST inherits the connection collation, which may differ from a fresh
        # utf8mb4_unicode_ci schema on MySQL 8's utf8mb4_0900_ai_ci server default.
        article_id = article_id.collate('utf8mb4_unicode_ci')
        space_id = space_id.collate('utf8mb4_unicode_ci')
    db.execute(update(AuditLog).where(AuditLog.target_type == 'wiki', AuditLog.wiki_space_id.is_(None))
        .values(wiki_space_id=select(WikiArticle.space_id).where(article_id == AuditLog.target_id).scalar_subquery()))
    db.execute(update(AuditLog).where(AuditLog.target_type == 'wiki_space', AuditLog.wiki_space_id.is_(None))
        .values(wiki_space_id=select(WikiSpace.id).where(space_id == AuditLog.target_id).scalar_subquery()))
    db.flush()
