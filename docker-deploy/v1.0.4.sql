-- v1.0.4 组织、部门与岗位管理升级 / MySQL 8.0+ / 可重复执行。
-- 先备份并暂停 API、Worker 与提醒调度服务；启动新版 API 完成部门目录回填。
SET NAMES utf8mb4;
USE `yunweishenji`;

CREATE TABLE IF NOT EXISTS departments (
	id INTEGER NOT NULL AUTO_INCREMENT,
	name VARCHAR(100) NOT NULL,
	parent_id INTEGER,
	active BOOL NOT NULL,
	sort_order INTEGER NOT NULL,
	description VARCHAR(500) NOT NULL,
	revision INTEGER NOT NULL,
	approval_mode VARCHAR(30) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name),
	FOREIGN KEY(parent_id) REFERENCES departments (id),
	INDEX ix_departments_parent_id (parent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_organization_column$$
CREATE PROCEDURE yunwei_organization_column(IN table_arg VARCHAR(64), IN column_arg VARCHAR(64), IN ddl_arg VARCHAR(255))
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=table_arg AND column_name=column_arg) THEN
    SET @organization_ddl = CONCAT('ALTER TABLE `', table_arg, '` ADD COLUMN `', column_arg, '` ', ddl_arg);
    PREPARE stmt FROM @organization_ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
  END IF;
END$$
DROP PROCEDURE IF EXISTS yunwei_organization_link$$
CREATE PROCEDURE yunwei_organization_link(IN table_arg VARCHAR(64))
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name=table_arg AND index_name=CONCAT('ix_',table_arg,'_department_id')) THEN
    SET @organization_ddl = CONCAT('CREATE INDEX ix_',table_arg,'_department_id ON `',table_arg,'` (department_id)');
    PREPARE stmt FROM @organization_ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name=table_arg AND column_name='department_id' AND referenced_table_name='departments') THEN
    SET @organization_ddl = CONCAT('ALTER TABLE `',table_arg,'` ADD CONSTRAINT fk_org_',table_arg,'_department FOREIGN KEY (department_id) REFERENCES departments(id)');
    PREPARE stmt FROM @organization_ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
  END IF;
END$$
DELIMITER ;
CALL yunwei_organization_column('users', 'department_id', 'INTEGER NULL');
CALL yunwei_organization_column('users', 'organization_revision', 'INTEGER NOT NULL DEFAULT 1');
CALL yunwei_organization_column('projects', 'department_id', 'INTEGER NULL');
CALL yunwei_organization_link('users');
CALL yunwei_organization_link('projects');
DROP PROCEDURE yunwei_organization_column;
DROP PROCEDURE yunwei_organization_link;

CREATE TABLE IF NOT EXISTS department_aliases (
	name VARCHAR(100) NOT NULL,
	department_id INTEGER NOT NULL,
	PRIMARY KEY (name),
	FOREIGN KEY(department_id) REFERENCES departments (id),
	INDEX ix_department_aliases_department_id (department_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS department_managers (
	department_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	PRIMARY KEY (department_id, user_id),
	FOREIGN KEY(department_id) REFERENCES departments (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS department_positions (
	id INTEGER NOT NULL AUTO_INCREMENT,
	department_id INTEGER NOT NULL,
	name VARCHAR(100) NOT NULL,
	active BOOL NOT NULL,
	manager_assignable BOOL NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_department_position_name UNIQUE (department_id, name),
	FOREIGN KEY(department_id) REFERENCES departments (id),
	INDEX ix_department_positions_department_id (department_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS department_position_roles (
	position_id INTEGER NOT NULL,
	`role` VARCHAR(30) NOT NULL,
	PRIMARY KEY (position_id, `role`),
	FOREIGN KEY(position_id) REFERENCES department_positions (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS department_user_positions (
	user_id INTEGER NOT NULL,
	position_id INTEGER NOT NULL,
	PRIMARY KEY (user_id, position_id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	FOREIGN KEY(position_id) REFERENCES department_positions (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS department_memberships (
    user_id INTEGER NOT NULL,
    department_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, department_id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    FOREIGN KEY(department_id) REFERENCES departments (id),
    INDEX ix_department_memberships_department_id (department_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
