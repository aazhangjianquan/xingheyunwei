-- v1.0.5 个人提醒 / MySQL 8.0+ / 可重复执行。
-- 仅新增表；从 v1.0.3 升级需先执行 v1.0.4.sql。
SET NAMES utf8mb4;
USE `yunweishenji`;

CREATE TABLE IF NOT EXISTS personal_reminders (
	id INTEGER NOT NULL AUTO_INCREMENT,
	owner_id INTEGER NOT NULL,
	content TEXT NOT NULL,
	remind_at DATETIME NOT NULL,
	state VARCHAR(20) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	deleted_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	INDEX ix_personal_reminders_due (state, remind_at, id),
	INDEX ix_personal_reminders_owner (owner_id, deleted_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS personal_reminder_attachments (
	id INTEGER NOT NULL AUTO_INCREMENT,
	reminder_id INTEGER NOT NULL,
	uploaded_by INTEGER NOT NULL,
	filename VARCHAR(255) NOT NULL,
	storage_key VARCHAR(64) NOT NULL,
	storage_backend VARCHAR(16) NOT NULL DEFAULT 'local',
	storage_bucket VARCHAR(63) NOT NULL DEFAULT '',
	extension VARCHAR(10) NOT NULL,
	content_type VARCHAR(100) NOT NULL,
	size INTEGER NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(reminder_id) REFERENCES personal_reminders (id),
	FOREIGN KEY(uploaded_by) REFERENCES users (id),
	UNIQUE (storage_key),
	INDEX ix_personal_reminder_attachments_reminder_id (reminder_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS personal_reminder_recipients (
	reminder_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	notification_id INTEGER,
	PRIMARY KEY (reminder_id, user_id),
	FOREIGN KEY(notification_id) REFERENCES notification_logs (id),
	FOREIGN KEY(reminder_id) REFERENCES personal_reminders (id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	UNIQUE (notification_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
