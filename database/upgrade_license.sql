-- 星河运维协作平台 / MySQL 8.0+ / utf8mb4
SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS `yunweishenji` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `yunweishenji`;

-- License 管理增量升级，仅创建新表，不修改历史业务或用户授权。
CREATE TABLE IF NOT EXISTS license_requests (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	code VARCHAR(40) NOT NULL, 
	applicant_id INTEGER NOT NULL, 
	project_id INTEGER, 
	project_name VARCHAR(150) NOT NULL, 
	customer VARCHAR(180) NOT NULL, 
	product VARCHAR(150) NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	expire_time BIGINT NOT NULL, 
	account_number INTEGER NOT NULL, 
	agent_number INTEGER NOT NULL, 
	purpose TEXT NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	stage VARCHAR(30) NOT NULL, 
	final_approver_id INTEGER, 
	license_key_ciphertext TEXT, 
	license_key_hash VARCHAR(64), 
	generated_by INTEGER, 
	generated_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code), 
	FOREIGN KEY(applicant_id) REFERENCES users (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(final_approver_id) REFERENCES users (id), 
	UNIQUE (license_key_hash), 
	FOREIGN KEY(generated_by) REFERENCES users (id),
	KEY `ix_license_requests_applicant_id` (applicant_id),
	KEY `ix_license_requests_final_approver_id` (final_approver_id),
	KEY `ix_license_requests_project_id` (project_id),
	KEY `ix_license_requests_status` (status)
)CHARSET=utf8mb4 ENGINE=InnoDB;
