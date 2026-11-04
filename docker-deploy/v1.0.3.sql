-- v1.0.3 到期飞书提醒升级 / MySQL 8.0+ / 可重复执行；新版 API 启动时也会自动迁移。
SET NAMES utf8mb4;
USE `yunweishenji`;
DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_reminder_column$$
CREATE PROCEDURE yunwei_reminder_column(IN table_arg VARCHAR(64), IN column_arg VARCHAR(64), IN ddl_arg VARCHAR(255))
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=table_arg AND column_name=column_arg) THEN
    SET @reminder_ddl = CONCAT('ALTER TABLE `', table_arg, '` ADD COLUMN `', column_arg, '` ', ddl_arg);
    PREPARE stmt FROM @reminder_ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
  END IF;
END$$
DELIMITER ;
CALL yunwei_reminder_column('resource_requests', 'reminder_enabled', 'BOOLEAN NOT NULL DEFAULT 1');
CALL yunwei_reminder_column('resource_requests', 'reminder_revision', 'INTEGER NOT NULL DEFAULT 1');
CALL yunwei_reminder_column('resource_requests', 'reminder_updated_by', 'INTEGER NULL');
CALL yunwei_reminder_column('resource_requests', 'reminder_updated_at', 'DATETIME NULL');
CALL yunwei_reminder_column('license_requests', 'reminder_enabled', 'BOOLEAN NOT NULL DEFAULT 1');
CALL yunwei_reminder_column('license_requests', 'reminder_revision', 'INTEGER NOT NULL DEFAULT 1');
CALL yunwei_reminder_column('license_requests', 'reminder_updated_by', 'INTEGER NULL');
CALL yunwei_reminder_column('license_requests', 'reminder_updated_at', 'DATETIME NULL');
DROP PROCEDURE yunwei_reminder_column;

CREATE TABLE IF NOT EXISTS reminder_plans (
	id INTEGER NOT NULL AUTO_INCREMENT,
	object_type VARCHAR(30) NOT NULL,
	object_id INTEGER NOT NULL,
	period VARCHAR(100) NOT NULL,
	instance_id INTEGER,
	rule VARCHAR(30) NOT NULL,
	scheduled_at DATETIME NOT NULL,
	expires_at DATETIME NOT NULL,
	state VARCHAR(20) NOT NULL,
	note VARCHAR(180) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_reminder_plan UNIQUE (object_type, object_id, period, rule),
	FOREIGN KEY(instance_id) REFERENCES workflow_instances (id),
	INDEX ix_reminder_plans_due (state, scheduled_at, id),
	INDEX ix_reminder_plans_object (object_type, object_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS reminder_deliveries (
	id INTEGER NOT NULL AUTO_INCREMENT,
	plan_id INTEGER NOT NULL,
	recipient_id INTEGER NOT NULL,
	notification_id INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_reminder_delivery UNIQUE (plan_id, recipient_id),
	FOREIGN KEY(plan_id) REFERENCES reminder_plans (id),
	FOREIGN KEY(recipient_id) REFERENCES users (id),
	UNIQUE (notification_id),
	FOREIGN KEY(notification_id) REFERENCES notification_logs (id),
	INDEX ix_reminder_deliveries_plan_id (plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS resource_reminder_contacts (
	id INTEGER NOT NULL AUTO_INCREMENT,
	resource_id INTEGER NOT NULL,
	instance_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	source_task_id INTEGER,
	source VARCHAR(20) NOT NULL,
	reason VARCHAR(500) NOT NULL,
	created_by INTEGER,
	PRIMARY KEY (id),
	CONSTRAINT uq_resource_reminder_contact UNIQUE (resource_id, instance_id, user_id),
	FOREIGN KEY(resource_id) REFERENCES resource_requests (id),
	FOREIGN KEY(instance_id) REFERENCES workflow_instances (id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	FOREIGN KEY(source_task_id) REFERENCES approvals (id),
	FOREIGN KEY(created_by) REFERENCES users (id),
	INDEX ix_resource_reminder_contacts_resource_id (resource_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
