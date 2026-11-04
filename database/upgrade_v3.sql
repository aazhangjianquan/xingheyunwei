-- 星河运维协作平台 / MySQL 8.0+ / utf8mb4
SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS `yunweishenji` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `yunweishenji`;

-- 升级前请备份数据库和 api_instance 密钥卷。不要导入 demo_data.sql 覆盖旧库。

CREATE TABLE IF NOT EXISTS projects (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(150) NOT NULL, 
	department VARCHAR(100) NOT NULL, 
	active BOOL NOT NULL, 
	created_by INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name), 
	FOREIGN KEY(created_by) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS user_roles (
	user_id INTEGER NOT NULL, 
	`role` VARCHAR(30) NOT NULL, 
	PRIMARY KEY (user_id, `role`), 
	FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS workflow_templates (
	kind VARCHAR(30) NOT NULL, 
	draft TEXT NOT NULL, 
	revision INTEGER NOT NULL, 
	updated_by INTEGER, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (kind), 
	FOREIGN KEY(updated_by) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS workflow_versions (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	kind VARCHAR(30) NOT NULL, 
	version INTEGER NOT NULL, 
	definition TEXT NOT NULL, 
	published_by INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_flow_version UNIQUE (kind, version), 
	FOREIGN KEY(published_by) REFERENCES users (id),
	KEY `ix_workflow_versions_kind` (kind)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS project_members (
	project_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	`role` VARCHAR(30) NOT NULL, 
	PRIMARY KEY (project_id, user_id, `role`), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS workflow_instances (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	object_type VARCHAR(30) NOT NULL, 
	object_id INTEGER NOT NULL, 
	round INTEGER NOT NULL, 
	version_id INTEGER, 
	snapshot TEXT NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_flow_round UNIQUE (object_type, object_id, round), 
	FOREIGN KEY(version_id) REFERENCES workflow_versions (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS workflow_nodes (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	instance_id INTEGER NOT NULL, 
	position INTEGER NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	node_type VARCHAR(30) NOT NULL, 
	mode VARCHAR(10) NOT NULL, 
	config TEXT NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_instance_position UNIQUE (instance_id, position), 
	FOREIGN KEY(instance_id) REFERENCES workflow_instances (id),
	KEY `ix_workflow_nodes_instance_id` (instance_id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS task_transfers (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	task_id INTEGER NOT NULL, 
	from_user_id INTEGER, 
	to_user_id INTEGER NOT NULL, 
	actor_id INTEGER NOT NULL, 
	reason TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(task_id) REFERENCES approvals (id), 
	FOREIGN KEY(from_user_id) REFERENCES users (id), 
	FOREIGN KEY(to_user_id) REFERENCES users (id), 
	FOREIGN KEY(actor_id) REFERENCES users (id),
	KEY `ix_task_transfers_task_id` (task_id)
)ENGINE=InnoDB CHARSET=utf8mb4;


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
