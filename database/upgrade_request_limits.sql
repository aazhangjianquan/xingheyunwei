-- API 请求限流与管理员告警 / MySQL 8.0+
-- 新版 API 启动也会自动创建；只新增计数表，不改历史业务或日志。
SET NAMES utf8mb4;
USE `yunweishenji`;

CREATE TABLE IF NOT EXISTS request_rate_limits (
	subject VARCHAR(80) NOT NULL, 
	attempts TEXT NOT NULL, 
	alert_after_ms BIGINT NOT NULL, 
	updated_ms BIGINT NOT NULL, 
	PRIMARY KEY (subject),
	KEY `ix_request_rate_limits_updated_ms` (updated_ms)
)CHARSET=utf8mb4 ENGINE=InnoDB;
