-- 星河项目协作平台 v1.0.1：角色职责与部门联动
-- MySQL 8.0+；连接已有业务库后执行，不包含演示数据。
-- 可重复执行；不清空业务，不修改密码、部门或历史审批快照。
-- 用户角色和项目岗位使用相同映射；合并重复授权，保留项目和审批权限。
SET NAMES utf8mb4;
START TRANSACTION;

-- leader → manager
UPDATE users SET `role` = 'manager' WHERE `role` = 'leader';
INSERT IGNORE INTO user_roles (user_id, `role`) SELECT user_id, 'manager' FROM user_roles WHERE `role` = 'leader';
DELETE FROM user_roles WHERE `role` = 'leader';
INSERT IGNORE INTO project_members (project_id, user_id, `role`) SELECT project_id, user_id, 'manager' FROM project_members WHERE `role` = 'leader';
DELETE FROM project_members WHERE `role` = 'leader';

-- project_owner → project_manager
UPDATE users SET `role` = 'project_manager' WHERE `role` = 'project_owner';
INSERT IGNORE INTO user_roles (user_id, `role`) SELECT user_id, 'project_manager' FROM user_roles WHERE `role` = 'project_owner';
DELETE FROM user_roles WHERE `role` = 'project_owner';
INSERT IGNORE INTO project_members (project_id, user_id, `role`) SELECT project_id, user_id, 'project_manager' FROM project_members WHERE `role` = 'project_owner';
DELETE FROM project_members WHERE `role` = 'project_owner';

-- project_director → project_manager_head
UPDATE users SET `role` = 'project_manager_head' WHERE `role` = 'project_director';
INSERT IGNORE INTO user_roles (user_id, `role`) SELECT user_id, 'project_manager_head' FROM user_roles WHERE `role` = 'project_director';
DELETE FROM user_roles WHERE `role` = 'project_director';
INSERT IGNORE INTO project_members (project_id, user_id, `role`) SELECT project_id, user_id, 'project_manager_head' FROM project_members WHERE `role` = 'project_director';
DELETE FROM project_members WHERE `role` = 'project_director';

-- sales_head → commercial_head
UPDATE users SET `role` = 'commercial_head' WHERE `role` = 'sales_head';
INSERT IGNORE INTO user_roles (user_id, `role`) SELECT user_id, 'commercial_head' FROM user_roles WHERE `role` = 'sales_head';
DELETE FROM user_roles WHERE `role` = 'sales_head';
INSERT IGNORE INTO project_members (project_id, user_id, `role`) SELECT project_id, user_id, 'commercial_head' FROM project_members WHERE `role` = 'sales_head';
DELETE FROM project_members WHERE `role` = 'sales_head';

-- sales_director → commercial_head
UPDATE users SET `role` = 'commercial_head' WHERE `role` = 'sales_director';
INSERT IGNORE INTO user_roles (user_id, `role`) SELECT user_id, 'commercial_head' FROM user_roles WHERE `role` = 'sales_director';
DELETE FROM user_roles WHERE `role` = 'sales_director';
INSERT IGNORE INTO project_members (project_id, user_id, `role`) SELECT project_id, user_id, 'commercial_head' FROM project_members WHERE `role` = 'sales_director';
DELETE FROM project_members WHERE `role` = 'sales_director';

-- development_head → technical_head
UPDATE users SET `role` = 'technical_head' WHERE `role` = 'development_head';
INSERT IGNORE INTO user_roles (user_id, `role`) SELECT user_id, 'technical_head' FROM user_roles WHERE `role` = 'development_head';
DELETE FROM user_roles WHERE `role` = 'development_head';
INSERT IGNORE INTO project_members (project_id, user_id, `role`) SELECT project_id, user_id, 'technical_head' FROM project_members WHERE `role` = 'development_head';
DELETE FROM project_members WHERE `role` = 'development_head';

-- 流程版本、在途任务、审批记录、审计日志保持原样。
-- 新版 API 兼容历史角色 ID；保存流程草稿时使用新版岗位。
COMMIT;
