"""Generate deterministic, data-free MySQL 8 SQL from the current ORM metadata."""
import argparse
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def render():
    # Importing metadata must never touch an operator's database or instance keys.
    with tempfile.TemporaryDirectory(prefix='workspace-schema-') as folder:
        os.environ.update(INSTANCE_DIR=folder, DATABASE_URL='sqlite://',
                          DATABASE_CONFIG_MODE='environment', REQUIRE_SHARED_KEYS='false')
        sys.path.insert(0, str(ROOT / 'backend'))
        from app import main  # register every model; lifespan is not executed
        from app.database import Base
        from sqlalchemy import MetaData, String, Text, text
        from sqlalchemy.dialects.mysql import dialect as mysql_dialect
        from sqlalchemy.schema import CreateIndex, CreateTable, DefaultClause
        dialect = mysql_dialect()
        metadata = MetaData()
        for original in Base.metadata.sorted_tables:
            table = original.to_metadata(metadata)
            table.dialect_options['mysql'].update(engine='InnoDB', charset='utf8mb4', collate='utf8mb4_unicode_ci')
            for column in table.columns:
                if isinstance(column.type, Text) and column.server_default is not None:
                    argument = column.server_default.arg
                    expression = String().literal_processor(dialect)(argument) if isinstance(argument, str) else str(argument)
                    # MySQL requires expression syntax for TEXT defaults (8.0.13+).
                    column.server_default = DefaultClause(text('(' + expression + ')'))
            constraints = sorted(table.constraints, key=lambda item: (
                type(item).__name__, tuple(column.name for column in item.columns), str(item.name or '')))
            for position, constraint in enumerate(constraints):
                constraint._creation_order = position
        statements = []
        for table in metadata.sorted_tables:
            ddl = str(CreateTable(table, if_not_exists=True).compile(dialect=dialect)).strip()
            # MySQL dialect option iteration order varies with Python hash seed.
            ddl = re.sub(r'\n\)[^\n]*$', '\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci', ddl)
            inline = []
            for index in sorted(table.indexes, key=lambda item: item.name):
                sql = str(CreateIndex(index).compile(dialect=dialect)).strip()
                match = re.fullmatch(r'CREATE (UNIQUE )?INDEX (\S+) ON \S+ (.+)', sql)
                if not match:
                    raise ValueError('无法生成索引：' + index.name)
                inline.append((match[1] or '') + 'INDEX ' + match[2] + ' ' + match[3])
            if inline:
                position = ddl.rfind('\n)')
                ddl = ddl[:position] + ',\n\t' + ',\n\t'.join(inline) + ddl[position:]
            statements.append(ddl + ';')
        header = '''-- 星河项目协作平台 v1.0.0 / MySQL 8.0.13+（建议 MySQL 8.4）
-- 全新安装用纯结构 SQL：不包含测试数据、账号、密码或任何 INSERT。
-- 初始管理员由 API 的 BOOTSTRAP_ADMIN_* 环境变量在首次启动时创建。
-- 既有数据库升级/迁移应保留原数据和密钥，不将本文件当作 ALTER 升级脚本。
-- CREATE TABLE IF NOT EXISTS 允许空结构安装重复执行；不会修补旧表字段。
SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS `yunweishenji` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `yunweishenji`;

'''
        return header + '\n\n'.join(statements) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = ROOT / 'k8s-deploy/v1.0.0.sql'
    sql = render()
    if args.check:
        if not path.exists() or path.read_text() != sql:
            raise SystemExit('v1.0.0.sql 与当前模型不一致，请重新生成')
        print('v1.0.0.sql 与当前模型一致，未包含数据写入语句。')
    else:
        path.write_text(sql)
        print('已生成 k8s-deploy/v1.0.0.sql')
