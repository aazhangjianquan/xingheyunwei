-- 飞书 open_id 使用应用主密钥加密保存；启动新版 API 会自动完成明文迁移。
SET NAMES utf8mb4;
USE `yunweishenji`;
ALTER TABLE users MODIFY COLUMN feishu_open_id VARCHAR(500) NULL;
