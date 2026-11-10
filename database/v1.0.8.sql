-- v1.0.8 项目岗位独立任命与审批执行衔接 / MySQL 8.0+ / 可重复执行。
-- 先备份数据库、附件及实例密钥，暂停 API、Worker 与提醒调度器。
-- v1.0.7 基础上执行；更早版本须先依次完成 v1.0.4～v1.0.7。
-- 本脚本仅增加在途节点的资格策略字段，原审批快照、任务和旧流程版本不修改。
-- 新 API 首次启动以事务迁移项目岗位、归档原任命与旧授权、清理旧业务角色依赖。
-- 销售/售前拆成销售与售前；项目部门负责人映射为技术负责人；移除项目管理者/产品开发。
-- 部门岗位定义、岗位人员、多部门任命及基础系统角色保留。
-- 升级结果见 system_settings.project_positions_v108；原任命/授权按记录归档在同前缀键。
-- 回退须停服务，恢复升级前数据库与旧镜像；不能只回滚 API 镜像。
SET NAMES utf8mb4;
USE `yunweishenji`;
DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_project_positions_v108$$
CREATE PROCEDURE yunwei_project_positions_v108()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='workflow_nodes' AND column_name='assignment_policy') THEN
    ALTER TABLE workflow_nodes ADD COLUMN assignment_policy TEXT NULL;
  END IF;
END$$
DELIMITER ;
CALL yunwei_project_positions_v108();
DROP PROCEDURE yunwei_project_positions_v108;
