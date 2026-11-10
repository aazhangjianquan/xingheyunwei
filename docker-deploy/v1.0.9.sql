-- v1.0.9 知识库空间开放范围 / MySQL 8.0+ / 可重复执行。
-- 从 v1.0.8 升级。先备份数据库与密钥，暂停服务写入，再执行 SQL 并同步更新 API、后台服务和前端。
-- 既有空间默认指定人员，原成员和编辑权限保留；个人库继续私有。
SET NAMES utf8mb4;
USE `yunweishenji`;
DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_wiki_access_v109$$
CREATE PROCEDURE yunwei_wiki_access_v109()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='wiki_spaces' AND column_name='access_scope') THEN
    ALTER TABLE wiki_spaces ADD COLUMN access_scope VARCHAR(20) NOT NULL DEFAULT 'members';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='wiki_spaces' AND column_name='include_subdepartments') THEN
    ALTER TABLE wiki_spaces ADD COLUMN include_subdepartments BOOLEAN NOT NULL DEFAULT 1;
  END IF;
END$$
DELIMITER ;
CALL yunwei_wiki_access_v109();
DROP PROCEDURE yunwei_wiki_access_v109;
CREATE TABLE IF NOT EXISTS wiki_space_departments (
  space_id INTEGER NOT NULL,
  department_id INTEGER NOT NULL,
  PRIMARY KEY (space_id, department_id),
  INDEX ix_wiki_space_departments_department_id (department_id),
  FOREIGN KEY (space_id) REFERENCES wiki_spaces(id) ON DELETE CASCADE,
  FOREIGN KEY (department_id) REFERENCES departments(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
