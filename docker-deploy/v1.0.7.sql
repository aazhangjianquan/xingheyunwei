-- v1.0.7 岗位业务授权与统一部门首审 / MySQL 8.0+ / 可重复执行。
-- 从 v1.0.3 起依次执行 v1.0.4、v1.0.5、v1.0.6，再执行本脚本。
-- 先备份数据库、附件和实例密钥，暂停 API、Worker、提醒调度器。
-- 本脚本仅新增四类申请的申请部门字段、索引及外键，旧记录保持 NULL。
-- 新 API 首次启动在事务中将原直接业务授权迁入等价部门岗位，并发布统一首审的新流程版本。
-- 保留原项目成员关系、历史审批快照、旧流程版本及 License 密钥；不指定新负责人/管理员。
-- 无有效部门的异常成员保留原授权，检查 system_settings.organization_business_positions_v107。
SET NAMES utf8mb4;
USE `yunweishenji`;

DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_application_department_v107$$
CREATE PROCEDURE yunwei_application_department_v107()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='resource_requests' AND column_name='application_department_id') THEN
    ALTER TABLE resource_requests ADD COLUMN application_department_id INTEGER NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='resource_requests' AND index_name='ix_resource_requests_application_department_id') THEN
    CREATE INDEX ix_resource_requests_application_department_id ON resource_requests (application_department_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name='resource_requests' AND column_name='application_department_id' AND referenced_table_name='departments') THEN
    ALTER TABLE resource_requests ADD CONSTRAINT fk_v3_resource_requests_application_department_id FOREIGN KEY (application_department_id) REFERENCES departments(id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='change_requests' AND column_name='application_department_id') THEN
    ALTER TABLE change_requests ADD COLUMN application_department_id INTEGER NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='change_requests' AND index_name='ix_change_requests_application_department_id') THEN
    CREATE INDEX ix_change_requests_application_department_id ON change_requests (application_department_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name='change_requests' AND column_name='application_department_id' AND referenced_table_name='departments') THEN
    ALTER TABLE change_requests ADD CONSTRAINT fk_v3_change_requests_application_department_id FOREIGN KEY (application_department_id) REFERENCES departments(id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='delivery_projects' AND column_name='application_department_id') THEN
    ALTER TABLE delivery_projects ADD COLUMN application_department_id INTEGER NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='delivery_projects' AND index_name='ix_delivery_projects_application_department_id') THEN
    CREATE INDEX ix_delivery_projects_application_department_id ON delivery_projects (application_department_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name='delivery_projects' AND column_name='application_department_id' AND referenced_table_name='departments') THEN
    ALTER TABLE delivery_projects ADD CONSTRAINT fk_v3_delivery_projects_application_department_id FOREIGN KEY (application_department_id) REFERENCES departments(id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='license_requests' AND column_name='application_department_id') THEN
    ALTER TABLE license_requests ADD COLUMN application_department_id INTEGER NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='license_requests' AND index_name='ix_license_requests_application_department_id') THEN
    CREATE INDEX ix_license_requests_application_department_id ON license_requests (application_department_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name='license_requests' AND column_name='application_department_id' AND referenced_table_name='departments') THEN
    ALTER TABLE license_requests ADD CONSTRAINT fk_v3_license_requests_application_department_id FOREIGN KEY (application_department_id) REFERENCES departments(id);
  END IF;
END$$
DELIMITER ;
CALL yunwei_application_department_v107();
DROP PROCEDURE yunwei_application_department_v107;
