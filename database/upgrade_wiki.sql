-- 星河运维协作平台 / Confluence 风格知识库升级 / MySQL 8.0+
SET NAMES utf8mb4;
USE `yunweishenji`;

CREATE TABLE IF NOT EXISTS wiki_spaces (
  id INTEGER NOT NULL AUTO_INCREMENT,
  `key` VARCHAR(40) NOT NULL,
  name VARCHAR(120) NOT NULL,
  description VARCHAR(300) NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL,
  owner_id INTEGER NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (id), UNIQUE KEY uq_wiki_space_key (`key`),
  FOREIGN KEY (created_by) REFERENCES users(id),
  FOREIGN KEY (owner_id) REFERENCES users(id),
  KEY ix_wiki_spaces_owner_id (owner_id)
) ENGINE=InnoDB CHARSET=utf8mb4;

-- The previous space schema did not include an owner. Add it idempotently.
DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_wiki_add_owner$$
CREATE PROCEDURE yunwei_wiki_add_owner()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='wiki_spaces' AND column_name='owner_id') THEN
    ALTER TABLE wiki_spaces ADD COLUMN owner_id INTEGER NULL;
  END IF;
END$$
DELIMITER ;
CALL yunwei_wiki_add_owner();
DROP PROCEDURE yunwei_wiki_add_owner;

CREATE TABLE IF NOT EXISTS wiki_article_versions (
  id INTEGER NOT NULL AUTO_INCREMENT,
  article_id INTEGER NOT NULL,
  version INTEGER NOT NULL,
  title VARCHAR(180) NOT NULL,
  category VARCHAR(60) NOT NULL,
  content TEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'published',
  author_id INTEGER NOT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (id), KEY ix_wiki_article_versions_article_id (article_id),
  FOREIGN KEY (article_id) REFERENCES wiki_articles(id) ON DELETE CASCADE,
  FOREIGN KEY (author_id) REFERENCES users(id)
) ENGINE=InnoDB CHARSET=utf8mb4;

DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_wiki_add_column$$
CREATE PROCEDURE yunwei_wiki_add_column(IN col_name VARCHAR(64), IN col_ddl VARCHAR(255))
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='wiki_articles' AND column_name=col_name) THEN
    SET @ddl = CONCAT('ALTER TABLE wiki_articles ADD COLUMN `', col_name, '` ', col_ddl);
    PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
  END IF;
END$$
DELIMITER ;
CALL yunwei_wiki_add_column('parent_id', 'INTEGER NULL');
CALL yunwei_wiki_add_column('status', 'VARCHAR(20) NOT NULL DEFAULT ''published''');
CALL yunwei_wiki_add_column('version', 'INTEGER NOT NULL DEFAULT 1');
CALL yunwei_wiki_add_column('revision', 'INTEGER NOT NULL DEFAULT 1');
CALL yunwei_wiki_add_column('space_id', 'INTEGER NULL');
DROP PROCEDURE yunwei_wiki_add_column;

-- Personal libraries and invitation-only public libraries.
DELIMITER $$
DROP PROCEDURE IF EXISTS yunwei_wiki_access_upgrade$$
CREATE PROCEDURE yunwei_wiki_access_upgrade()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='wiki_spaces' AND column_name='kind') THEN
    ALTER TABLE wiki_spaces ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'shared';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='wiki_spaces' AND column_name='category') THEN
    ALTER TABLE wiki_spaces ADD COLUMN category VARCHAR(60) NOT NULL DEFAULT '';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='wiki_spaces' AND column_name='deleted_at') THEN
    ALTER TABLE wiki_spaces ADD COLUMN deleted_at DATETIME NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='wiki_spaces' AND column_name='personal_user_id') THEN
    ALTER TABLE wiki_spaces ADD COLUMN personal_user_id INTEGER NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='wiki_spaces' AND column_name='personal_user_id' AND non_unique=0) THEN
    CREATE UNIQUE INDEX uq_wiki_personal_user ON wiki_spaces(personal_user_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='wiki_spaces' AND column_name='members_initialized') THEN
    ALTER TABLE wiki_spaces ADD COLUMN members_initialized BOOLEAN NOT NULL DEFAULT 0;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='audit_logs') AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='audit_logs' AND column_name='wiki_space_id') THEN
    ALTER TABLE audit_logs ADD COLUMN wiki_space_id INTEGER NULL;
    CREATE INDEX ix_audit_logs_wiki_space_id ON audit_logs(wiki_space_id);
  END IF;
END$$
DELIMITER ;
CALL yunwei_wiki_access_upgrade();
DROP PROCEDURE yunwei_wiki_access_upgrade;

CREATE TABLE IF NOT EXISTS wiki_space_members (
  space_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  role VARCHAR(20) NOT NULL DEFAULT 'viewer',
  added_by INTEGER NOT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY(space_id,user_id),
  FOREIGN KEY(space_id) REFERENCES wiki_spaces(id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES users(id),
  FOREIGN KEY(added_by) REFERENCES users(id),
  KEY ix_wiki_space_members_user_id(user_id)
) ENGINE=InnoDB CHARSET=utf8mb4;

START TRANSACTION;
INSERT IGNORE INTO wiki_spaces (`key`, name, description, created_by, members_initialized, created_at)
SELECT 'OPS', '运维知识库', '团队规范、交付经验与故障复盘', id, 0, CURRENT_TIMESTAMP FROM users ORDER BY id LIMIT 1;
UPDATE wiki_spaces SET owner_id=created_by WHERE owner_id IS NULL;
UPDATE wiki_articles a JOIN wiki_spaces s ON s.`key`='OPS' SET a.space_id=s.id WHERE a.space_id IS NULL;
INSERT INTO wiki_article_versions (article_id, version, title, category, content, status, author_id, created_at)
SELECT a.id, a.version, a.title, a.category, a.content, a.status, a.author_id, a.updated_at FROM wiki_articles a
WHERE NOT EXISTS (SELECT 1 FROM wiki_article_versions v WHERE v.article_id=a.id AND v.version=a.version);


INSERT IGNORE INTO wiki_space_members(space_id,user_id,role,added_by,created_at)
SELECT DISTINCT s.id,a.author_id,'editor',s.owner_id,CURRENT_TIMESTAMP
FROM wiki_spaces s JOIN wiki_articles a ON a.space_id=s.id
WHERE s.kind='shared' AND s.members_initialized=0 AND a.author_id<>s.owner_id;
UPDATE wiki_spaces SET members_initialized=1 WHERE members_initialized=0;
INSERT INTO wiki_spaces(`key`,name,description,created_by,owner_id,kind,personal_user_id,members_initialized,created_at)
SELECT CONCAT('PERSONAL_',LEFT(REPLACE(UUID(),'-',''),24)),'我的个人知识库','仅自己可见的笔记、方案和经验',u.id,u.id,'personal',u.id,1,CURRENT_TIMESTAMP
FROM users u WHERE NOT EXISTS (SELECT 1 FROM wiki_spaces s WHERE s.personal_user_id=u.id);
UPDATE audit_logs l JOIN wiki_articles a ON CAST(a.id AS CHAR)=l.target_id
SET l.wiki_space_id=a.space_id WHERE l.target_type='wiki' AND l.wiki_space_id IS NULL;
UPDATE audit_logs l JOIN wiki_spaces s ON CAST(s.id AS CHAR)=l.target_id
SET l.wiki_space_id=s.id WHERE l.target_type='wiki_space' AND l.wiki_space_id IS NULL;
COMMIT;
