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
