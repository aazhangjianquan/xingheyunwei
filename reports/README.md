# 验收与升级记录

`bash scripts/upgrade_v3.sh` 会生成升级日志 `upgrade-v3.log`、数据库及密钥备份、真实 MySQL 验收 `workflow-v3-mysql.json`。

`workflow-v3-sqlite.json` 仅表示隔离开发库结果，不包含 MySQL 行锁并发验证。旧的 `workflow-verification.json` 对应上一版固定流程。

数据库和密钥备份含敏感信息，已在 `.gitignore` 排除，并由脚本设置私有权限。不要公开备份目录。
