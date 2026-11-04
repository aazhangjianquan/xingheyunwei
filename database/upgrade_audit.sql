-- 操作日志增量升级 / MySQL 8.0+；新版 API 启动时也会自动执行同等迁移。
-- 仅增加字段和索引，保留所有历史日志，不推断历史单据或字段差异。
SET NAMES utf8mb4;
USE `yunweishenji`;
DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_audit_column$$
CREATE PROCEDURE yunwei_audit_column(IN col_name VARCHAR(64), IN col_ddl VARCHAR(255))
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='audit_logs' AND column_name=col_name) THEN
    SET @audit_ddl = CONCAT('ALTER TABLE audit_logs ADD COLUMN `', col_name, '` ', col_ddl);
    PREPARE stmt FROM @audit_ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
  END IF;
END$$
DROP PROCEDURE IF EXISTS yunwei_audit_index$$
CREATE PROCEDURE yunwei_audit_index()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='audit_logs' AND index_name='ix_audit_logs_kind_time') THEN
    CREATE INDEX ix_audit_logs_kind_time ON audit_logs (record_kind, created_at, id);
  END IF;
END$$
DELIMITER ;
CALL yunwei_audit_column('record_kind', 'VARCHAR(20) NOT NULL DEFAULT ''request''');
CALL yunwei_audit_column('actor_name', 'VARCHAR(80) NOT NULL DEFAULT ''''');
CALL yunwei_audit_column('actor_username', 'VARCHAR(80) NOT NULL DEFAULT ''''');
CALL yunwei_audit_column('target_code', 'VARCHAR(80) NOT NULL DEFAULT ''''');
CALL yunwei_audit_column('target_name', 'VARCHAR(180) NOT NULL DEFAULT ''''');
CALL yunwei_audit_column('summary', 'VARCHAR(600) NOT NULL DEFAULT ''''');
CALL yunwei_audit_column('changes', 'TEXT NULL');
CALL yunwei_audit_index();
DROP PROCEDURE yunwei_audit_column;
DROP PROCEDURE yunwei_audit_index;
