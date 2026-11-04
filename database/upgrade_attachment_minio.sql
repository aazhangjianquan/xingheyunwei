-- MinIO 附件增量字段；旧记录保持本地存储，文件迁移由 API 脚本校验后切换。
SET NAMES utf8mb4;
USE `yunweishenji`;

DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_attachment_minio_upgrade$$
CREATE PROCEDURE yunwei_attachment_minio_upgrade()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='delivery_attachments' AND column_name='storage_backend') THEN
    ALTER TABLE delivery_attachments ADD COLUMN storage_backend VARCHAR(16) NOT NULL DEFAULT 'local';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='delivery_attachments' AND column_name='storage_bucket') THEN
    ALTER TABLE delivery_attachments ADD COLUMN storage_bucket VARCHAR(63) NOT NULL DEFAULT '';
  END IF;
END$$
DELIMITER ;
CALL yunwei_attachment_minio_upgrade();
DROP PROCEDURE yunwei_attachment_minio_upgrade;
