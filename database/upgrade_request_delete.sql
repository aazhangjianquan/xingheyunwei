-- Soft delete support for business requests. Records remain available to audit,
-- while normal lists, details and workflow actions hide deleted rows.
SET NAMES utf8mb4;
USE `yunweishenji`;

DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_request_delete_columns$$
CREATE PROCEDURE yunwei_request_delete_columns()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='resource_requests' AND column_name='deleted_at') THEN
    ALTER TABLE resource_requests ADD COLUMN deleted_at DATETIME NULL, ADD INDEX ix_resource_requests_deleted_at (deleted_at);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='change_requests' AND column_name='deleted_at') THEN
    ALTER TABLE change_requests ADD COLUMN deleted_at DATETIME NULL, ADD INDEX ix_change_requests_deleted_at (deleted_at);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='delivery_projects' AND column_name='deleted_at') THEN
    ALTER TABLE delivery_projects ADD COLUMN deleted_at DATETIME NULL, ADD INDEX ix_delivery_projects_deleted_at (deleted_at);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='license_requests' AND column_name='deleted_at') THEN
    ALTER TABLE license_requests ADD COLUMN deleted_at DATETIME NULL, ADD INDEX ix_license_requests_deleted_at (deleted_at);
  END IF;
END$$
DELIMITER ;
CALL yunwei_request_delete_columns();
DROP PROCEDURE yunwei_request_delete_columns;
