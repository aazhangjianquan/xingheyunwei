-- 星河项目协作平台 v1.0.2：知识库自动保存与正式版本分离
-- MySQL 8.0+；连接已有业务库后执行；可重复执行。
-- 自动保存只更新当前内容；手动保存才新增历史版本。
-- 独立 revision 用于并发冲突检测，不展示为文档版本号。
-- 不删除、不合并已有历史版本，也不修改正文、版本号和更新时间。
SET NAMES utf8mb4;
SET @wiki_revision_ddl = IF(
  EXISTS (SELECT 1 FROM information_schema.columns
          WHERE table_schema = DATABASE() AND table_name = 'wiki_articles' AND column_name = 'revision'),
  'SELECT 1',
  'ALTER TABLE wiki_articles ADD COLUMN revision INTEGER NOT NULL DEFAULT 1'
);
PREPARE wiki_revision_stmt FROM @wiki_revision_ddl;
EXECUTE wiki_revision_stmt;
DEALLOCATE PREPARE wiki_revision_stmt;
