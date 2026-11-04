-- 星河运维协作平台 / License 审批增量升级 / MySQL 8.0+
-- 已有 License 管理库使用此脚本，然后重启新版 API。
SET NAMES utf8mb4;
USE `yunweishenji`;
DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_license_approval_upgrade$$
CREATE PROCEDURE yunwei_license_approval_upgrade()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='license_requests' AND column_name='stage') THEN
    ALTER TABLE license_requests ADD COLUMN stage VARCHAR(30) NOT NULL DEFAULT 'awaiting_workflow';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='license_requests' AND column_name='final_approver_id') THEN
    ALTER TABLE license_requests ADD COLUMN final_approver_id INTEGER NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='license_requests' AND index_name='ix_license_requests_final_approver_id') THEN
    CREATE INDEX ix_license_requests_final_approver_id ON license_requests (final_approver_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name='license_requests' AND column_name='final_approver_id' AND referenced_table_name='users') THEN
    ALTER TABLE license_requests ADD CONSTRAINT fk_license_final_approver FOREIGN KEY (final_approver_id) REFERENCES users(id);
  END IF;
END$$
DELIMITER ;
CALL yunwei_license_approval_upgrade();
DROP PROCEDURE yunwei_license_approval_upgrade;
-- 原密文保留，未经过审批的历史申请需补走审批，不把原生成者认作审批人。
UPDATE license_requests l SET l.status='pending', l.stage='awaiting_workflow', l.final_approver_id=NULL
WHERE NOT EXISTS (SELECT 1 FROM workflow_instances w WHERE w.object_type='license' AND w.object_id=l.id);
-- 重启新版 API 自动初始化 License 流程草稿；管理员选定最终审批人并发布后可提交。
