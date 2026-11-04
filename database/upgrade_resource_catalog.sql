-- Resource catalog and type-specific specification values.
SET NAMES utf8mb4;
USE `yunweishenji`;

SET @column_exists := (
  SELECT COUNT(*) FROM information_schema.columns
  WHERE table_schema = DATABASE() AND table_name = 'resource_requests' AND column_name = 'specification_values'
);
SET @sql := IF(@column_exists = 0,
  'ALTER TABLE resource_requests ADD COLUMN specification_values TEXT NULL',
  'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
