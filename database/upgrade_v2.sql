-- 从首版升级：先备份 yunweishenji，再执行本文件，最后重启 API。
-- 新表结构由随后执行 schema.sql 补齐；此文件只升级已有表。
USE `yunweishenji`;
SET @has_assignee = (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'approvals' AND column_name = 'assignee_id');
SET @ddl = IF(@has_assignee = 0, 'ALTER TABLE approvals ADD COLUMN assignee_id INTEGER NULL, ADD INDEX ix_approvals_assignee_id (assignee_id), ADD CONSTRAINT fk_approval_assignee FOREIGN KEY (assignee_id) REFERENCES users(id)', 'SELECT 1');
PREPARE upgrade_stmt FROM @ddl;
EXECUTE upgrade_stmt;
DEALLOCATE PREPARE upgrade_stmt;
UPDATE users SET feishu_open_id = NULL WHERE feishu_open_id = '';
SET @has_open_id_unique = (SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = 'feishu_open_id' AND non_unique = 0);
SET @ddl = IF(@has_open_id_unique = 0, 'CREATE UNIQUE INDEX uq_users_feishu_open_id ON users(feishu_open_id)', 'SELECT 1');
PREPARE upgrade_stmt FROM @ddl;
EXECUTE upgrade_stmt;
DEALLOCATE PREPARE upgrade_stmt;
-- 如存在重复 open_id，上述唯一索引会失败；请先在用户管理里解除重复绑定。
-- 下一步：mysql -u <用户> -p < database/schema.sql
