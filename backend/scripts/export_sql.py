"""Regenerate the reviewable MySQL install scripts from the application model."""
import json
import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable
from app.database import Base, SessionLocal, engine
from app.main import seed, OBJECTS
from app.flow_engine import seed_v3
from app.flow_api import seed_demo
from app.licenses import seed_license_workflow
from app.models import SystemSetting
from app.settings import DEFAULTS, SECRET_KEYS

ROOT = Path(__file__).resolve().parents[2]
directory = ROOT / "database"
directory.mkdir(exist_ok=True)
dialect = mysql.dialect()
header = "-- 星河运维协作平台 / MySQL 8.0+ / utf8mb4\nSET NAMES utf8mb4;\nCREATE DATABASE IF NOT EXISTS `yunweishenji` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\nUSE `yunweishenji`;\n"
schema = [header]
for table in Base.metadata.sorted_tables:
    table.dialect_options["mysql"]["engine"] = "InnoDB"
    table.dialect_options["mysql"]["charset"] = "utf8mb4"
    schema.append(str(CreateTable(table, if_not_exists=True).compile(dialect=dialect)).strip() + ";\n")
    for index in sorted(table.indexes, key=lambda i: i.name):
        # Indexes belong inside CREATE TABLE, making the whole script repeatable.
        columns = ", ".join(dialect.identifier_preparer.quote(c.name) for c in index.columns)
        unique = "UNIQUE " if index.unique else ""
        fragment = f",\n\t{unique}KEY `{index.name}` ({columns})"
        content = schema[-1]
        end = content.rfind(")")
        schema[-1] = content[:end].rstrip() + fragment + "\n" + content[end:]
schema_sql = "\n".join(schema)
(directory / "schema.sql").write_text(schema_sql)

Base.metadata.create_all(engine)
with SessionLocal() as db:
    seed(db)
    seed_v3(db, OBJECTS)
    seed_demo(db)
    seed_license_workflow(db)
    for key, value in DEFAULTS.items():
        if key not in SECRET_KEYS:
            db.add(SystemSetting(key=key, value=json.dumps(value, ensure_ascii=False), is_secret=False))
    db.commit()
    demo = ["-- 可选演示数据：16 位成员，初始密码均为 Demo@2026；请在体验后修改。\nSET NAMES utf8mb4;\nUSE `yunweishenji`;\nSTART TRANSACTION;\n"]
    for table in Base.metadata.sorted_tables:
        rows = db.execute(table.select()).mappings().all()
        for row in rows:
            statement = table.insert().values(**dict(row))
            compiled = str(statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
            pk = next(iter(table.primary_key.columns)).name
            compiled += f" ON DUPLICATE KEY UPDATE `{pk}` = `{pk}`;"
            demo.append(compiled)
    demo.append("COMMIT;\n")
    demo_sql = "\n".join(demo)
    (directory / "demo_data.sql").write_text(demo_sql)
    (directory / "install.sql").write_text(schema_sql + "\n\n" + demo_sql)
print(f"Generated {len(Base.metadata.tables)} tables and 16 demo accounts in database/")

# Incremental DDL is repeatable and retains all existing rows and keys.
legacy = {"users", "resource_requests", "change_requests", "delivery_projects", "delivery_credentials", "approvals", "wiki_articles", "audit_logs", "notification_logs", "system_settings", "workflow_assignments", "oauth_states", "login_tickets", "license_requests"}
upgrade = [header, "-- 升级前请备份数据库和 api_instance 密钥卷。不要导入 demo_data.sql 覆盖旧库。\n"]
for item in schema[1:]:
    if any(f"CREATE TABLE IF NOT EXISTS {name} (" in item for name in set(Base.metadata.tables) - legacy):
        upgrade.append(item)
upgrade.append("""
DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_v3_add_column$$
CREATE PROCEDURE yunwei_v3_add_column(IN tab_name VARCHAR(64), IN col_name VARCHAR(64), IN col_ddl VARCHAR(255))
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=tab_name AND column_name=col_name) THEN
    SET @ddl = CONCAT('ALTER TABLE `',tab_name,'` ADD COLUMN `',col_name,'` ',col_ddl);
    PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
  END IF;
END$$
DELIMITER ;
CALL yunwei_v3_add_column('resource_requests','project_id','INTEGER NULL');
CALL yunwei_v3_add_column('change_requests','project_id','INTEGER NULL');
CALL yunwei_v3_add_column('change_requests','environment','VARCHAR(30) NOT NULL DEFAULT ''生产''');
CALL yunwei_v3_add_column('delivery_projects','project_id','INTEGER NULL');
CALL yunwei_v3_add_column('approvals','node_run_id','INTEGER NULL');
CALL yunwei_v3_add_column('approvals','assignee_snapshot','TEXT NULL');
DROP PROCEDURE yunwei_v3_add_column;
INSERT IGNORE INTO user_roles (user_id, role) SELECT id, role FROM users;
-- 重启新版 API 后幂等补齐索引/外键，生成默认发布版本并迁移旧单据审批快照。
-- 旧单据 project_id 保持 NULL；不推断历史项目归属，不重跑已通过节点。
""")
(directory / 'upgrade_v3.sql').write_text('\n'.join(upgrade))

license_ddl = next(item for item in schema[1:] if 'CREATE TABLE IF NOT EXISTS license_requests (' in item)
(directory / 'upgrade_license.sql').write_text(header + '\n-- License 管理增量升级，仅创建新表，不修改历史业务或用户授权。\n' + license_ddl)
attachment_ddl = next(item for item in schema[1:] if 'CREATE TABLE IF NOT EXISTS delivery_attachments (' in item)
(directory / 'upgrade_delivery_attachments.sql').write_text(header + '\n-- 交付附件元数据；新附件保存在 MinIO 私有桶，旧本地附件保留原存储标记。\n' + attachment_ddl)
