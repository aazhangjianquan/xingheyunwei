-- 星河运维协作平台 / MySQL 8.0+ / utf8mb4
SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS `yunweishenji` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `yunweishenji`;

-- Private metadata for change request attachments. Object content remains in the configured private store.
CREATE TABLE IF NOT EXISTS change_attachments (
	id INTEGER NOT NULL AUTO_INCREMENT,
	change_id INTEGER NOT NULL,
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
	FOREIGN KEY(change_id) REFERENCES change_requests (id) ON DELETE CASCADE,
	FOREIGN KEY(uploaded_by) REFERENCES users (id),
	UNIQUE (storage_key),
	KEY `ix_change_attachments_change_id` (change_id)
)CHARSET=utf8mb4 ENGINE=InnoDB;
