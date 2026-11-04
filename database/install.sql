-- 星河运维协作平台 / MySQL 8.0+ / utf8mb4
SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS `yunweishenji` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `yunweishenji`;

CREATE TABLE IF NOT EXISTS users (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	username VARCHAR(80) NOT NULL, 
	display_name VARCHAR(80) NOT NULL, 
	department VARCHAR(100) NOT NULL, 
	`role` VARCHAR(30) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	feishu_open_id VARCHAR(500), 
	active BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (feishu_open_id),
	KEY `ix_users_role` (`role`),
	UNIQUE KEY `ix_users_username` (username)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_logs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	actor_id INTEGER, 
	record_kind VARCHAR(20) NOT NULL DEFAULT 'request', 
	actor_name VARCHAR(80) NOT NULL DEFAULT '', 
	actor_username VARCHAR(80) NOT NULL DEFAULT '', 
	method VARCHAR(10) NOT NULL, 
	path VARCHAR(255) NOT NULL, 
	action VARCHAR(80) NOT NULL, 
	target_type VARCHAR(50) NOT NULL, 
	target_id VARCHAR(50) NOT NULL, 
	target_code VARCHAR(80) NOT NULL DEFAULT '', 
	target_name VARCHAR(180) NOT NULL DEFAULT '', 
	summary VARCHAR(600) NOT NULL DEFAULT '', 
	changes TEXT,
	wiki_space_id INTEGER NULL, 
	status_code INTEGER NOT NULL, 
	ip_address VARCHAR(80) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(actor_id) REFERENCES users (id),
	KEY `ix_audit_logs_created_at` (created_at),
	KEY `ix_audit_logs_kind_time` (record_kind, created_at, id)
)CHARSET=utf8mb4 ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS login_tickets (
	ticket_hash VARCHAR(64) NOT NULL, 
	user_id INTEGER NOT NULL, 
	expires_at DATETIME NOT NULL, 
	consumed BOOL NOT NULL, 
	PRIMARY KEY (ticket_hash), 
	FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS notification_logs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	recipient_id INTEGER NOT NULL, 
	event VARCHAR(80) NOT NULL, 
	object_type VARCHAR(30) NOT NULL, 
	object_id INTEGER NOT NULL, 
	message TEXT NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	error TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	sent_at DATETIME,
	attempt_count INTEGER NOT NULL DEFAULT 0,
	next_attempt_at DATETIME,
	dispatch_ready BOOL NOT NULL DEFAULT 0,
	INDEX ix_notifications_dispatch (status, next_attempt_at, id), 
	PRIMARY KEY (id), 
	FOREIGN KEY(recipient_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS oauth_states (
	state_hash VARCHAR(64) NOT NULL, 
	browser_hash VARCHAR(64) NOT NULL, 
	purpose VARCHAR(20) NOT NULL, 
	user_id INTEGER, 
	expires_at DATETIME NOT NULL, 
	consumed BOOL NOT NULL, 
	PRIMARY KEY (state_hash), 
	FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

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

CREATE TABLE IF NOT EXISTS system_settings (
	`key` VARCHAR(80) NOT NULL, 
	value TEXT NOT NULL, 
	is_secret BOOL NOT NULL, 
	updated_by INTEGER, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (`key`), 
	FOREIGN KEY(updated_by) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS user_roles (
	user_id INTEGER NOT NULL, 
	`role` VARCHAR(30) NOT NULL, 
	PRIMARY KEY (user_id, `role`), 
	FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS wiki_spaces (
	id INTEGER NOT NULL AUTO_INCREMENT,
	`key` VARCHAR(40) NOT NULL,
	name VARCHAR(120) NOT NULL,
	description VARCHAR(300) NOT NULL,
	created_by INTEGER NOT NULL,
	owner_id INTEGER,
	kind VARCHAR(20) NOT NULL DEFAULT 'shared',
	category VARCHAR(60) NOT NULL DEFAULT '',
	deleted_at DATETIME NULL,
	personal_user_id INTEGER NULL,
	members_initialized BOOLEAN NOT NULL DEFAULT 1,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE KEY uq_wiki_space_key (`key`),
	UNIQUE KEY uq_wiki_personal_user (personal_user_id),
	FOREIGN KEY(personal_user_id) REFERENCES users (id),
	FOREIGN KEY(created_by) REFERENCES users (id),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	KEY `ix_wiki_spaces_owner_id` (owner_id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS wiki_space_members (
 space_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL,
 role VARCHAR(20) NOT NULL DEFAULT 'viewer',
 added_by INTEGER NOT NULL,
 created_at DATETIME NOT NULL,
 PRIMARY KEY (space_id,user_id),
 FOREIGN KEY (space_id) REFERENCES wiki_spaces(id) ON DELETE CASCADE,
 FOREIGN KEY (user_id) REFERENCES users(id),
 FOREIGN KEY (added_by) REFERENCES users(id),
 KEY ix_wiki_space_members_user_id (user_id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS wiki_articles (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	space_id INTEGER, 
	parent_id INTEGER, 
	title VARCHAR(180) NOT NULL, 
	category VARCHAR(60) NOT NULL, 
	content TEXT NOT NULL, 
	author_id INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL DEFAULT 'published', 
	version INTEGER NOT NULL DEFAULT 1, 
	revision INTEGER NOT NULL DEFAULT 1,
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(author_id) REFERENCES users (id),
	FOREIGN KEY(space_id) REFERENCES wiki_spaces (id),
	FOREIGN KEY(parent_id) REFERENCES wiki_articles (id),
	KEY `ix_wiki_articles_space_id` (space_id),
	KEY `ix_wiki_articles_parent_id` (parent_id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS wiki_article_versions (
	id INTEGER NOT NULL AUTO_INCREMENT,
	article_id INTEGER NOT NULL,
	version INTEGER NOT NULL,
	title VARCHAR(180) NOT NULL,
	category VARCHAR(60) NOT NULL,
	content TEXT NOT NULL,
	status VARCHAR(20) NOT NULL DEFAULT 'published',
	author_id INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	KEY `ix_wiki_article_versions_article_id` (article_id),
	FOREIGN KEY(article_id) REFERENCES wiki_articles (id) ON DELETE CASCADE,
	FOREIGN KEY(author_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS workflow_assignments (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	object_type VARCHAR(30) NOT NULL, 
	stage VARCHAR(30) NOT NULL, 
	department VARCHAR(100) NOT NULL, 
	assignee_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_workflow_route UNIQUE (object_type, stage, department), 
	FOREIGN KEY(assignee_id) REFERENCES users (id)
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

CREATE TABLE IF NOT EXISTS change_requests (
	environment VARCHAR(30) NOT NULL, 
	id INTEGER NOT NULL AUTO_INCREMENT, 
	project_id INTEGER, 
	code VARCHAR(40) NOT NULL, 
	name VARCHAR(180) NOT NULL, 
	change_type VARCHAR(50) NOT NULL, 
	window_start VARCHAR(40) NOT NULL, 
	window_end VARCHAR(40) NOT NULL, 
	owner_id INTEGER NOT NULL, 
	ops_owner_id INTEGER, 
	plan TEXT NOT NULL DEFAULT '', 
	problem_description TEXT NULL,
	rollback_plan TEXT NOT NULL DEFAULT '', 
	implementation TEXT NOT NULL DEFAULT '', 
	status VARCHAR(30) NOT NULL, 
	stage VARCHAR(30) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	deleted_at DATETIME NULL,
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	UNIQUE (code), 
	FOREIGN KEY(owner_id) REFERENCES users (id), 
	FOREIGN KEY(ops_owner_id) REFERENCES users (id),
	KEY `ix_change_requests_project_id` (project_id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS delivery_projects (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	project_id INTEGER, 
	code VARCHAR(40) NOT NULL, 
	name VARCHAR(180) NOT NULL, 
	customer VARCHAR(180) NOT NULL, 
	owner_id INTEGER NOT NULL, 
	cloud_type VARCHAR(30) NOT NULL, 
	deployment_mode VARCHAR(30) NOT NULL, 
	environment VARCHAR(30) NOT NULL, 
	compute_type VARCHAR(30) NOT NULL, 
	accelerator VARCHAR(30) NOT NULL, 
	network_notes TEXT NOT NULL, 
	db_whitelist TEXT NOT NULL, 
	security_group TEXT NOT NULL, 
	account_validation TEXT NOT NULL, 
	notes TEXT NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	stage VARCHAR(30) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	deleted_at DATETIME NULL,
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	UNIQUE (code), 
	FOREIGN KEY(owner_id) REFERENCES users (id),
	KEY `ix_delivery_projects_project_id` (project_id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS project_members (
	project_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	`role` VARCHAR(30) NOT NULL, 
	PRIMARY KEY (project_id, user_id, `role`), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS resource_requests (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	project_id INTEGER, 
	code VARCHAR(40) NOT NULL, 
	project_name VARCHAR(150) NOT NULL, 
	applicant_id INTEGER NOT NULL, 
	environment VARCHAR(30) NOT NULL, 
	resource_type VARCHAR(80) NOT NULL, 
	specification TEXT NOT NULL, 
	specification_values TEXT NULL,
	start_date VARCHAR(30) NOT NULL, 
	end_date VARCHAR(30) NOT NULL, 
	purpose TEXT NOT NULL, 
	capacity_basis TEXT NOT NULL, 
	ports TEXT NOT NULL, 
	whitelist TEXT NOT NULL, 
	fulfillment TEXT NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	stage VARCHAR(30) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	deleted_at DATETIME NULL,
	reminder_enabled BOOLEAN NOT NULL DEFAULT 1,
	reminder_revision INTEGER NOT NULL DEFAULT 1,
	reminder_updated_by INTEGER NULL,
	reminder_updated_at DATETIME NULL,
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	UNIQUE (code), 
	FOREIGN KEY(applicant_id) REFERENCES users (id),
	KEY `ix_resource_requests_project_id` (project_id),
	KEY `ix_resource_requests_project_name` (project_name),
	KEY `ix_resource_requests_status` (status)
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

CREATE TABLE IF NOT EXISTS delivery_credentials (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	project_id INTEGER NOT NULL, 
	db_type VARCHAR(30) NOT NULL, 
	host VARCHAR(180) NOT NULL, 
	username VARCHAR(100) NOT NULL, 
	password_ciphertext TEXT NOT NULL, 
	privilege VARCHAR(30) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES delivery_projects (id) ON DELETE CASCADE,
	KEY `ix_delivery_credentials_project_id` (project_id)
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

CREATE TABLE IF NOT EXISTS approvals (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	node_run_id INTEGER, 
	assignee_snapshot TEXT NOT NULL, 
	object_type VARCHAR(30) NOT NULL, 
	object_id INTEGER NOT NULL, 
	stage VARCHAR(30) NOT NULL, 
	actor_id INTEGER, 
	assignee_id INTEGER, 
	action VARCHAR(30) NOT NULL, 
	comment TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	acted_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(node_run_id) REFERENCES workflow_nodes (id), 
	FOREIGN KEY(actor_id) REFERENCES users (id), 
	FOREIGN KEY(assignee_id) REFERENCES users (id),
	KEY `ix_approvals_assignee_id` (assignee_id),
	KEY `ix_approvals_node_run_id` (node_run_id),
	KEY `ix_approvals_object` (object_type, object_id)
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
	deleted_at DATETIME NULL,
	reminder_enabled BOOLEAN NOT NULL DEFAULT 1,
	reminder_revision INTEGER NOT NULL DEFAULT 1,
	reminder_updated_by INTEGER NULL,
	reminder_updated_at DATETIME NULL,
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

CREATE TABLE IF NOT EXISTS request_rate_limits (
	subject VARCHAR(80) NOT NULL, 
	attempts TEXT NOT NULL, 
	alert_after_ms BIGINT NOT NULL, 
	updated_ms BIGINT NOT NULL, 
	PRIMARY KEY (subject),
	KEY `ix_request_rate_limits_updated_ms` (updated_ms)
)CHARSET=utf8mb4 ENGINE=InnoDB;

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

CREATE TABLE IF NOT EXISTS delivery_attachments (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	delivery_id INTEGER NOT NULL, 
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
	FOREIGN KEY(delivery_id) REFERENCES delivery_projects (id) ON DELETE CASCADE, 
	FOREIGN KEY(uploaded_by) REFERENCES users (id), 
	UNIQUE (storage_key),
	KEY `ix_delivery_attachments_delivery_id` (delivery_id)
)CHARSET=utf8mb4 ENGINE=InnoDB;


-- 可选演示数据：16 位成员，初始密码均为 Demo@2026；请在体验后修改。
SET NAMES utf8mb4;
USE `yunweishenji`;
START TRANSACTION;

INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (1, 'admin', '系统管理员', '平台管理', 'admin', 'pbkdf2_sha256$390000$68fbbb111e260f929e6dd7353aa5921c$8517756597faa237a9467ab41a25c1bfd44a8e926fbbb2037aeb7ddb41914e4c', NULL, true, '2026-10-26 06:58:35.411410') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (2, 'applicant', '陈一诺', '产品研发部', 'applicant', 'pbkdf2_sha256$390000$cd25a2186422d60e4eda7ef11f9ff688$07542ec6d2666874c9ad5bfa28a01e524bc36b07d82f766176adae9ca508f4b0', NULL, true, '2026-10-26 06:58:35.411416') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (3, 'dept', '李明远', '产品研发部', 'department_head', 'pbkdf2_sha256$390000$9f4668ed1f0536e34a9f6699d8f2a2e0$c5a00b83dead69faf6e22c5eda33d6ddb870deb457fc4e66d0e0dec4106988af', NULL, true, '2026-10-26 06:58:35.411418') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (4, 'opslead', '周承宇', '运维部', 'ops_head', 'pbkdf2_sha256$390000$7f8ba049f8ba7ef61499bb3158fd7583$1ba27f51305e285509ccbfaf8ec7e6eeaf72868186df1f59aa962959d341f9a5', NULL, true, '2026-10-26 06:58:35.411419') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (5, 'leader', '王志远', '管理层', 'leader', 'pbkdf2_sha256$390000$70de4dd7c65d8adc1821d0fa96f0eb5b$8a5eb0e11ea0826d2e82dfce6b92cac240fa369a8bb3b6f925447fc80ce04790', NULL, true, '2026-10-26 06:58:35.411419') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (6, 'operator', '林知行', '运维部', 'operator', 'pbkdf2_sha256$390000$26059b0a7a1afe0bd4567fb1b531d530$62838328689a9078a18b12f4446fdd563cac73a5866f2e9db0fb95a2ac7a6463', NULL, true, '2026-10-26 06:58:35.411420') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (7, 'applicant2', '赵若溪', '市场部', 'applicant', 'pbkdf2_sha256$390000$c0e2a8feac23015e33f34866b71a37d3$7ae618b77a5d26cbe1a7eb3bb3d8f0e61807741f5cbe6c2ce1bae7b2fce290c3', NULL, true, '2026-10-26 06:58:35.411421') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (8, 'dept2', '孙思齐', '市场部', 'department_head', 'pbkdf2_sha256$390000$7752090bc5279832594b3d76afefbc51$abc2d973460b1bf9412354d344c86965a8626e137db37e104ce96a73e1ab9584', NULL, true, '2026-10-26 06:58:35.411422') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (9, 'operator2', '许墨', '运维部', 'operator', 'pbkdf2_sha256$390000$6585a6a815d0f3cdb45342228f003600$d73e115b64fa47ed9907d17bacb5d9f5db5a01ea716f21d4a00b365291be505a', NULL, true, '2026-10-26 06:58:35.411422') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (10, 'projectowner', '沈知遥', '产品研发部', 'project_owner', 'pbkdf2_sha256$390000$6859ff39179bf851ef7249b1a5f49240$4a4850ae5f95d2bcebc7eb618ea30b928784a64459a21d5af0874cfdb508610a', NULL, true, '2026-10-26 06:58:37.105075') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (11, 'projectmanager', '顾景行', '市场部', 'project_manager', 'pbkdf2_sha256$390000$2358e1064a532b6901cf7c9033f7ff49$caac0c7fa0da406edcfb7affe78ee1ac291b45e19fa26fae5136b6843f01b9c1', NULL, true, '2026-10-26 06:58:37.105080') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (12, 'projectdirector', '陆承安', '管理层', 'project_director', 'pbkdf2_sha256$390000$f06cb2937f77b7d344c09f2fb844eb19$8ddca505c33c0f80c113024063d9e2ef27540503ce5898daccee158465c257dd', NULL, true, '2026-10-26 06:58:37.105081') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (13, 'saleshead', '宋予安', '市场部', 'sales_head', 'pbkdf2_sha256$390000$293273ee45bde3b792c6ee4f8358a8c5$a6708449ecaf5232ba31792a971461609bbb60c022b4cac97addf767b558f41c', NULL, true, '2026-10-26 06:58:37.105082') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (14, 'salesdirector', '程以宁', '市场部', 'sales_director', 'pbkdf2_sha256$390000$236a8ddb38055f87a5e359d387775246$a64cb10cdc2ba0fe129edb249e6cdacc28d106acc515f4d212440323f798317f', NULL, true, '2026-10-26 06:58:37.105082') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (15, 'devhead', '江屿', '产品研发部', 'development_head', 'pbkdf2_sha256$390000$0f293b911e6c753050101f76a1695ead$55b48e36f9ff2357d15ee762e274490fa25345c49a7d6e971438b0a349d1fed1', NULL, true, '2026-10-26 06:58:37.105083') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO users (id, username, display_name, department, `role`, password_hash, feishu_open_id, active, created_at) VALUES (16, 'devhead2', '苏念', '产品研发部', 'development_head', 'pbkdf2_sha256$390000$f06347fef7735a1297a45a2971c3202a$345d5d760be3b9f5dcd801f0f15d856fd5ec896668465f2b55e99c1ee754ab56', NULL, true, '2026-10-26 06:58:37.105084') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO projects (id, name, department, active, created_by, created_at) VALUES (1, '星河数据分析平台', '产品研发部', true, 1, '2026-10-26 06:58:37.109424') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO projects (id, name, department, active, created_by, created_at) VALUES (2, '市场交付项目', '市场部', true, 1, '2026-10-26 06:58:37.110480') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO system_settings (`key`, value, is_secret, updated_by, updated_at) VALUES ('demo_v3_seeded', 'true', false, NULL, '2026-10-26 06:58:37.112908') ON DUPLICATE KEY UPDATE `key` = `key`;
INSERT INTO system_settings (`key`, value, is_secret, updated_by, updated_at) VALUES ('site_name', '"星河运维协作平台"', false, NULL, '2026-10-26 06:58:37.114253') ON DUPLICATE KEY UPDATE `key` = `key`;
INSERT INTO system_settings (`key`, value, is_secret, updated_by, updated_at) VALUES ('public_base_url', '"http://localhost:8080"', false, NULL, '2026-10-26 06:58:37.114256') ON DUPLICATE KEY UPDATE `key` = `key`;
INSERT INTO system_settings (`key`, value, is_secret, updated_by, updated_at) VALUES ('feishu_app_id', '""', false, NULL, '2026-10-26 06:58:37.114257') ON DUPLICATE KEY UPDATE `key` = `key`;
INSERT INTO system_settings (`key`, value, is_secret, updated_by, updated_at) VALUES ('feishu_tenant_key', '""', false, NULL, '2026-10-26 06:58:37.114258') ON DUPLICATE KEY UPDATE `key` = `key`;
INSERT INTO system_settings (`key`, value, is_secret, updated_by, updated_at) VALUES ('feishu_login_enabled', 'false', false, NULL, '2026-10-26 06:58:37.114258') ON DUPLICATE KEY UPDATE `key` = `key`;
INSERT INTO system_settings (`key`, value, is_secret, updated_by, updated_at) VALUES ('feishu_notifications_enabled', 'false', false, NULL, '2026-10-26 06:58:37.114259') ON DUPLICATE KEY UPDATE `key` = `key`;
INSERT INTO system_settings (`key`, value, is_secret, updated_by, updated_at) VALUES ('local_login_enabled', 'true', false, NULL, '2026-10-26 06:58:37.114259') ON DUPLICATE KEY UPDATE `key` = `key`;
INSERT INTO user_roles (user_id, `role`) VALUES (1, 'admin') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (2, 'applicant') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (3, 'department_head') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (4, 'ops_head') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (5, 'leader') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (6, 'operator') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (7, 'applicant') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (8, 'department_head') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (9, 'operator') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (10, 'project_owner') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (10, 'project_manager') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (11, 'project_manager') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (12, 'project_director') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (13, 'sales_head') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (14, 'sales_director') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (15, 'development_head') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO user_roles (user_id, `role`) VALUES (16, 'development_head') ON DUPLICATE KEY UPDATE `user_id` = `user_id`;
INSERT INTO wiki_articles (id, title, category, content, author_id, updated_at) VALUES (1, '生产环境变更操作规范', '运维规范', '# 生产环境变更操作规范

1. 提前提交变更申请并明确维护窗口。
2. 记录执行方案与回滚方案。
3. 实施完成后填写结果并通知相关人员。', 6, '2026-10-26 06:58:35.425015') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO wiki_articles (id, title, category, content, author_id, updated_at) VALUES (2, '数据库账号交付检查清单', '交付方案', '# 数据库账号交付检查清单

- 核对实例地址与端口
- 验证白名单与最小权限
- 通过平台密文回填密码', 6, '2026-10-26 06:58:35.425017') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_assignments (id, object_type, stage, department, assignee_id) VALUES (1, 'resource', 'department_head', '产品研发部', 3) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_assignments (id, object_type, stage, department, assignee_id) VALUES (2, 'resource', 'department_head', '市场部', 8) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_assignments (id, object_type, stage, department, assignee_id) VALUES (3, 'resource', 'ops_head', '', 4) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_assignments (id, object_type, stage, department, assignee_id) VALUES (4, 'resource', 'leader', '', 5) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_assignments (id, object_type, stage, department, assignee_id) VALUES (5, 'resource', 'operator', '', 6) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_assignments (id, object_type, stage, department, assignee_id) VALUES (6, 'change', 'ops_head', '', 4) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_assignments (id, object_type, stage, department, assignee_id) VALUES (7, 'change', 'leader', '', 5) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_assignments (id, object_type, stage, department, assignee_id) VALUES (8, 'change', 'operator', '', 6) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_assignments (id, object_type, stage, department, assignee_id) VALUES (9, 'delivery', 'ops_head', '', 4) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_assignments (id, object_type, stage, department, assignee_id) VALUES (10, 'delivery', 'leader', '', 5) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_assignments (id, object_type, stage, department, assignee_id) VALUES (11, 'delivery', 'operator', '', 6) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_templates (kind, draft, revision, updated_by, updated_at) VALUES ('resource', '{"steps": [{"id": "department_head", "name": "部门负责人", "type": "approval", "mode": "all", "assignee": {"source": "department_role", "role": "department_head"}}, {"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}}, {"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}}, {"id": "operator", "name": "运维执行/回填", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}}]}', 1, NULL, '2026-10-26 06:58:35.485510') ON DUPLICATE KEY UPDATE `kind` = `kind`;
INSERT INTO workflow_templates (kind, draft, revision, updated_by, updated_at) VALUES ('change', '{"steps": [{"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}}, {"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}}, {"id": "operator", "name": "运维执行/回填", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}}]}', 1, NULL, '2026-10-26 06:58:35.485515') ON DUPLICATE KEY UPDATE `kind` = `kind`;
INSERT INTO workflow_templates (kind, draft, revision, updated_by, updated_at) VALUES ('delivery', '{"steps": [{"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}}, {"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}}, {"id": "operator", "name": "运维执行/回填", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}}]}', 1, NULL, '2026-10-26 06:58:35.485516') ON DUPLICATE KEY UPDATE `kind` = `kind`;
INSERT INTO workflow_versions (id, kind, version, definition, published_by, created_at) VALUES (1, 'resource', 1, '{"steps": [{"id": "department_head", "name": "部门负责人", "type": "approval", "mode": "all", "assignee": {"source": "department_role", "role": "department_head"}}, {"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}}, {"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}}, {"id": "operator", "name": "运维执行/回填", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}}]}', NULL, '2026-10-26 06:58:35.486287') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_versions (id, kind, version, definition, published_by, created_at) VALUES (2, 'change', 1, '{"steps": [{"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}}, {"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}}, {"id": "operator", "name": "运维执行/回填", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}}]}', NULL, '2026-10-26 06:58:35.486290') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_versions (id, kind, version, definition, published_by, created_at) VALUES (3, 'delivery', 1, '{"steps": [{"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}}, {"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}}, {"id": "operator", "name": "运维执行/回填", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}}]}', NULL, '2026-10-26 06:58:35.486290') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO change_requests (environment, id, project_id, code, name, change_type, window_start, window_end, owner_id, ops_owner_id, problem_description, plan, rollback_plan, implementation, status, stage, created_at, updated_at) VALUES ('生产', 1, NULL, 'CHG-2026-001', '生产环境网关证书更新', '配置变更', '2026-10-27T22:00', '2026-10-27T23:00', 2, 6, '更新即将到期的生产网关证书，避免 HTTPS 服务中断。', '', '', '', 'pending', 'ops_head', '2026-10-26 06:58:35.419643', '2026-10-26 06:58:35.419648') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO delivery_projects (id, project_id, code, name, customer, owner_id, cloud_type, deployment_mode, environment, compute_type, accelerator, network_notes, db_whitelist, security_group, account_validation, notes, status, stage, created_at, updated_at) VALUES (1, NULL, 'DEL-2026-001', '华东客户私有化部署', '华东科技有限公司', 2, '专有云', '云平台', 'POC', '虚拟机', 'NVIDIA', '业务网与数据网隔离', '10.16.2.0/24', '443, 3306', '待运维验证', '', 'pending', 'ops_head', '2026-10-26 06:58:35.421324', '2026-10-26 06:58:35.421328') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (1, 10, 'project_owner') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (1, 10, 'project_manager') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (1, 12, 'project_director') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (1, 13, 'sales_head') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (1, 14, 'sales_director') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (1, 15, 'development_head') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (1, 16, 'development_head') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (1, 2, 'applicant') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (1, 6, 'operator') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (2, 10, 'project_owner') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (2, 11, 'project_manager') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (2, 12, 'project_director') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (2, 13, 'sales_head') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (2, 14, 'sales_director') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (2, 15, 'development_head') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (2, 16, 'development_head') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (2, 7, 'applicant') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO project_members (project_id, user_id, `role`) VALUES (2, 6, 'operator') ON DUPLICATE KEY UPDATE `project_id` = `project_id`;
INSERT INTO resource_requests (id, project_id, code, project_name, applicant_id, environment, resource_type, specification, start_date, end_date, purpose, capacity_basis, ports, whitelist, fulfillment, status, stage, created_at, updated_at) VALUES (1, NULL, 'RES-2026-001', '星河数据分析平台', 2, '生产', 'ECS', '4 核 8 GB · 100 GB SSD · 2 台', '2026-10-28', '2027-10-28', '部署分析服务与任务调度组件', '日均 50 万条事件，双节点保障可用性', '443, 8080', '10.20.0.0/16', '', 'pending', 'department_head', '2026-10-26 06:58:35.423194', '2026-10-26 06:58:35.423213') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO resource_requests (id, project_id, code, project_name, applicant_id, environment, resource_type, specification, start_date, end_date, purpose, capacity_basis, ports, whitelist, fulfillment, status, stage, created_at, updated_at) VALUES (2, NULL, 'RES-2026-002', '客户画像服务', 2, '测试', 'Redis', '4 GB 主从版', '2026-10-24', '2027-04-24', '缓存客户画像查询结果', '约 100 万键，平均 2 KB', '', '', '', 'in_progress', 'operator', '2026-10-26 06:58:35.424225', '2026-10-26 06:58:35.424228') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_instances (id, object_type, object_id, round, version_id, snapshot, status, created_at) VALUES (1, 'resource', 1, 1, NULL, '{"legacy": true, "branches": [], "path": [{"id": "department_head", "name": "部门负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [3]}, "assignees": [{"id": 3, "name": "李明远", "required_role": "department_head", "roles": ["department_head"], "department": "产品研发部"}]}, {"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}, "assignees": [{"id": 4, "name": "周承宇", "required_role": "ops_head", "roles": ["ops_head"], "department": null}]}, {"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}, "assignees": [{"id": 5, "name": "王志远", "required_role": "leader", "roles": ["leader"], "department": null}]}, {"id": "operator", "name": "运维人员", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}, "assignees": [{"id": 6, "name": "林知行", "required_role": "operator", "roles": ["operator"], "department": null}]}]}', 'pending', '2026-10-26 06:58:35.491520') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_instances (id, object_type, object_id, round, version_id, snapshot, status, created_at) VALUES (2, 'resource', 2, 1, NULL, '{"legacy": true, "branches": [], "path": [{"id": "department_head", "name": "部门负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [3]}, "assignees": [{"id": 3, "name": "李明远", "required_role": "department_head", "roles": ["department_head"], "department": "产品研发部"}]}, {"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}, "assignees": [{"id": 4, "name": "周承宇", "required_role": "ops_head", "roles": ["ops_head"], "department": null}]}, {"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}, "assignees": [{"id": 5, "name": "王志远", "required_role": "leader", "roles": ["leader"], "department": null}]}, {"id": "operator", "name": "运维人员", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}, "assignees": [{"id": 6, "name": "林知行", "required_role": "operator", "roles": ["operator"], "department": null}]}]}', 'in_progress', '2026-10-26 06:58:35.507370') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_instances (id, object_type, object_id, round, version_id, snapshot, status, created_at) VALUES (3, 'change', 1, 1, NULL, '{"legacy": true, "branches": [], "path": [{"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}, "assignees": [{"id": 4, "name": "周承宇", "required_role": "ops_head", "roles": ["ops_head"], "department": null}]}, {"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}, "assignees": [{"id": 5, "name": "王志远", "required_role": "leader", "roles": ["leader"], "department": null}]}, {"id": "operator", "name": "运维人员", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}, "assignees": [{"id": 6, "name": "林知行", "required_role": "operator", "roles": ["operator"], "department": null}]}]}', 'pending', '2026-10-26 06:58:35.519988') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_instances (id, object_type, object_id, round, version_id, snapshot, status, created_at) VALUES (4, 'delivery', 1, 1, NULL, '{"legacy": true, "branches": [], "path": [{"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}, "assignees": [{"id": 4, "name": "周承宇", "required_role": "ops_head", "roles": ["ops_head"], "department": null}]}, {"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}, "assignees": [{"id": 5, "name": "王志远", "required_role": "leader", "roles": ["leader"], "department": null}]}, {"id": "operator", "name": "运维人员", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}, "assignees": [{"id": 6, "name": "林知行", "required_role": "operator", "roles": ["operator"], "department": null}]}]}', 'pending', '2026-10-26 06:58:35.530208') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (1, 1, 0, '部门负责人', 'approval', 'all', '{"id": "department_head", "name": "部门负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [3]}, "assignees": [{"id": 3, "name": "李明远", "required_role": "department_head", "roles": ["department_head"], "department": "产品研发部"}]}', 'pending') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (2, 1, 1, '运维负责人', 'approval', 'all', '{"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}, "assignees": [{"id": 4, "name": "周承宇", "required_role": "ops_head", "roles": ["ops_head"], "department": null}]}', 'waiting') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (3, 1, 2, '领导', 'approval', 'all', '{"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}, "assignees": [{"id": 5, "name": "王志远", "required_role": "leader", "roles": ["leader"], "department": null}]}', 'waiting') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (4, 1, 3, '运维人员', 'execution', 'all', '{"id": "operator", "name": "运维人员", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}, "assignees": [{"id": 6, "name": "林知行", "required_role": "operator", "roles": ["operator"], "department": null}]}', 'waiting') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (5, 2, 0, '部门负责人', 'approval', 'all', '{"id": "department_head", "name": "部门负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [3]}, "assignees": [{"id": 3, "name": "李明远", "required_role": "department_head", "roles": ["department_head"], "department": "产品研发部"}]}', 'legacy_unrecorded') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (6, 2, 1, '运维负责人', 'approval', 'all', '{"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}, "assignees": [{"id": 4, "name": "周承宇", "required_role": "ops_head", "roles": ["ops_head"], "department": null}]}', 'legacy_unrecorded') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (7, 2, 2, '领导', 'approval', 'all', '{"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}, "assignees": [{"id": 5, "name": "王志远", "required_role": "leader", "roles": ["leader"], "department": null}]}', 'legacy_unrecorded') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (8, 2, 3, '运维人员', 'execution', 'all', '{"id": "operator", "name": "运维人员", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}, "assignees": [{"id": 6, "name": "林知行", "required_role": "operator", "roles": ["operator"], "department": null}]}', 'pending') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (9, 3, 0, '运维负责人', 'approval', 'all', '{"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}, "assignees": [{"id": 4, "name": "周承宇", "required_role": "ops_head", "roles": ["ops_head"], "department": null}]}', 'pending') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (10, 3, 1, '领导', 'approval', 'all', '{"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}, "assignees": [{"id": 5, "name": "王志远", "required_role": "leader", "roles": ["leader"], "department": null}]}', 'waiting') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (11, 3, 2, '运维人员', 'execution', 'all', '{"id": "operator", "name": "运维人员", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}, "assignees": [{"id": 6, "name": "林知行", "required_role": "operator", "roles": ["operator"], "department": null}]}', 'waiting') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (12, 4, 0, '运维负责人', 'approval', 'all', '{"id": "ops_head", "name": "运维负责人", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [4]}, "assignees": [{"id": 4, "name": "周承宇", "required_role": "ops_head", "roles": ["ops_head"], "department": null}]}', 'pending') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (13, 4, 1, '领导', 'approval', 'all', '{"id": "leader", "name": "领导", "type": "approval", "mode": "all", "assignee": {"source": "fixed", "user_ids": [5]}, "assignees": [{"id": 5, "name": "王志远", "required_role": "leader", "roles": ["leader"], "department": null}]}', 'waiting') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO workflow_nodes (id, instance_id, position, name, node_type, mode, config, status) VALUES (14, 4, 2, '运维人员', 'execution', 'all', '{"id": "operator", "name": "运维人员", "type": "execution", "mode": "all", "assignee": {"source": "fixed", "user_ids": [6]}, "assignees": [{"id": 6, "name": "林知行", "required_role": "operator", "roles": ["operator"], "department": null}]}', 'waiting') ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO approvals (id, node_run_id, assignee_snapshot, object_type, object_id, stage, actor_id, assignee_id, action, comment, created_at, acted_at) VALUES (1, 8, '{"id": 6, "name": "林知行", "required_role": "operator", "roles": ["operator"], "department": null}', 'resource', 2, 'operator', NULL, 6, 'pending', '', '2026-10-26 06:58:35.461836', NULL) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO approvals (id, node_run_id, assignee_snapshot, object_type, object_id, stage, actor_id, assignee_id, action, comment, created_at, acted_at) VALUES (2, 1, '{"id": 3, "name": "李明远", "required_role": "department_head", "roles": ["department_head"], "department": "产品研发部"}', 'resource', 1, 'department_head', NULL, 3, 'pending', '', '2026-10-26 06:58:35.461841', NULL) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO approvals (id, node_run_id, assignee_snapshot, object_type, object_id, stage, actor_id, assignee_id, action, comment, created_at, acted_at) VALUES (3, 9, '{"id": 4, "name": "周承宇", "required_role": "ops_head", "roles": ["ops_head"], "department": null}', 'change', 1, 'ops_head', NULL, 4, 'pending', '', '2026-10-26 06:58:35.461842', NULL) ON DUPLICATE KEY UPDATE `id` = `id`;
INSERT INTO approvals (id, node_run_id, assignee_snapshot, object_type, object_id, stage, actor_id, assignee_id, action, comment, created_at, acted_at) VALUES (4, 12, '{"id": 4, "name": "周承宇", "required_role": "ops_head", "roles": ["ops_head"], "department": null}', 'delivery', 1, 'ops_head', NULL, 4, 'pending', '', '2026-10-26 06:58:35.461843', NULL) ON DUPLICATE KEY UPDATE `id` = `id`;
COMMIT;

-- 到期提醒计划与按收件人去重记录

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
