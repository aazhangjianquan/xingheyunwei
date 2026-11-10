-- v1.0.6 部门岗位向下引用 / MySQL 8.0+ / 可重复执行。
-- 从 v1.0.3 升级时，先执行 v1.0.4.sql、v1.0.5.sql。
-- 先备份并暂停 API、Worker、调度器；为原分配回填任职部门，不改变岗位与授权。
SET NAMES utf8mb4;
USE `yunweishenji`;

DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_position_assignment_scope$$
CREATE PROCEDURE yunwei_position_assignment_scope()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='department_user_positions' AND column_name='department_id') THEN
    ALTER TABLE department_user_positions ADD COLUMN department_id INTEGER NULL;
  END IF;
  UPDATE department_user_positions g JOIN department_positions p ON p.id=g.position_id
    SET g.department_id=p.department_id WHERE g.department_id IS NULL;
  IF EXISTS (SELECT 1 FROM department_user_positions WHERE department_id IS NULL) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Position assignment has no department; repair legacy data first';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='department_user_positions' AND column_name='department_id' AND is_nullable='YES') THEN
    ALTER TABLE department_user_positions MODIFY COLUMN department_id INTEGER NOT NULL;
  END IF;
  IF (SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='department_user_positions' AND index_name='PRIMARY') <> 'user_id,position_id,department_id' THEN
    ALTER TABLE department_user_positions DROP PRIMARY KEY, ADD PRIMARY KEY (user_id, position_id, department_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='department_user_positions' AND index_name='ix_department_user_positions_department_id') THEN
    CREATE INDEX ix_department_user_positions_department_id ON department_user_positions (department_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name='department_user_positions' AND column_name='department_id' AND referenced_table_name='departments') THEN
    ALTER TABLE department_user_positions ADD CONSTRAINT fk_position_assignment_department FOREIGN KEY (department_id) REFERENCES departments(id);
  END IF;
END$$
DELIMITER ;
CALL yunwei_position_assignment_scope();
DROP PROCEDURE yunwei_position_assignment_scope;
