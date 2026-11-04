-- 星河运维协作平台 / License 关联项目增量升级 / MySQL 8.0+
-- 也可直接重启新版 API 自动迁移；请保留现有数据库与 api_instance 卷。
SET NAMES utf8mb4;
USE `yunweishenji`;
DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_license_project_upgrade$$
CREATE PROCEDURE yunwei_license_project_upgrade()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='license_requests' AND column_name='project_id') THEN
    ALTER TABLE license_requests ADD COLUMN project_id INTEGER NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='license_requests' AND index_name='ix_license_requests_project_id') THEN
    CREATE INDEX ix_license_requests_project_id ON license_requests (project_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name='license_requests' AND column_name='project_id' AND referenced_table_name='projects') THEN
    ALTER TABLE license_requests ADD CONSTRAINT fk_license_project FOREIGN KEY (project_id) REFERENCES projects(id);
  END IF;
END$$
DELIMITER ;
CALL yunwei_license_project_upgrade();
DROP PROCEDURE yunwei_license_project_upgrade;
-- 不按名称推断历史归属，不改审批、申请参数、密钥或已生成状态。
-- 历史待补审批申请需选择申请者有权限的启用项目；在途审批按原快照继续。
