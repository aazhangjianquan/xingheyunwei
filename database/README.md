# SQL 执行顺序

全新库：`mysql -u root -p < database/install.sql`。

已有首版库：先备份，再执行 `upgrade_v2.sql`，随后执行 `schema.sql`，最后重启 API。不要在已有业务库执行 `demo_data.sql`。

Docker 首次启动时自动依次导入 `schema.sql` 和 `demo_data.sql`。SQL 不包含飞书应用凭据或数据库凭据明文；演示账号初始密码统一为 `Demo@2026`，已用 PBKDF2 哈希保存。

## License 管理升级

已有 V3 数据库可执行 `upgrade_license.sql` 后重启新版 API；直接重启新版 API 也会自动创建新表。此脚本只创建 `license_requests`，可重复执行，不修改用户或旧单据。

管理员在「用户与权限」明确授予指定成员 `license_manager`（License 管理者）角色，普通申请者无需新增角色。新建库的 `schema.sql` 和 `install.sql` 已包含此表。

License 审批升级：已有 License 表执行 `upgrade_license_approval.sql` 后重启；直接重启新版 API 也会自动补字段。旧申请密文保留，待补走审批后才可读取。管理员在「系统配置 → 审批流程 → License 授权」指定最终审批人并发布，再提交历史申请审批。

License 关联项目：新版 API 启动自动增加 `project_id` 及索引、MySQL 外键，也可执行 `upgrade_license_project.sql`。新申请及驳回重提必须选择申请者有权限的启用项目；历史记录不按名称推断归属。历史待补审批申请需明确选择项目，在途及已完成审批保持原流程、状态和密钥。

## 审批流程与知识库升级

审批流程编辑器按 Jira Workflow 的状态机方式管理草稿、条件分支、会签/或签、处理人和发布版本；提交时冻结流程快照，详情页保留当前节点、意见、转交和历史轮次。

知识库按 Confluence 的空间、目录、文档状态和版本历史组织内容。新版 API 启动会创建默认「运维知识库」空间，把历史文章归入该空间并生成 v1 快照；也可手动执行 `upgrade_wiki.sql`。文档支持草稿、发布、归档，编辑会产生不可变版本记录。

## 操作日志升级

新版 API 启动自动增加业务操作日志字段及查询索引，也可执行 `upgrade_audit.sql`。旧日志完整保留在「接口访问与异常」，不会凭接口路径推断历史创建单号、修改前后内容或删除对象。

「业务操作」记录新版上线后的实际业务变动：操作人姓名及用户名快照、北京时间、业务类型、对象 ID / 单号 / 名称、具体动作和字段差异，过长文本会明确标记截断。业务日志与业务数据在同一个事务提交；失败尝试保留在接口日志。密码、License Key、飞书密钥和执行回填正文不进入字段差异。日志仅管理员可查询，没有修改或删除日志的接口。

## 请求限流与告警升级

新版 API 启动自动创建 `request_rate_limits`，也可手动执行 `upgrade_request_limits.sql`。表保存账号与 IP 的滚动窗口计数和告警去重时间，支持多进程共享；不更改原有业务、密钥、通知和审计记录。计数包含被限流的请求，健康检查、静态资源、CORS 预检除外。飞书收件人为当前启用的管理员，仍需启用通知并配置对应的 `open_id`。

## 私有化交付附件升级

新版 API 启动自动创建 `delivery_attachments`，也可执行 `upgrade_delivery_attachments.sql` 后重启；脚本可重复执行，不修改已有交付单。新库的 `schema.sql`、`install.sql` 已包含此表。

此表仅保存附件元数据。新附件原件及 PDF 预览缓存保存在 Compose 配置的 MinIO 私有桶，历史本地附件在迁移前仍使用 `api_instance` 卷的 `delivery-attachments/`。备份和恢复时必须配套保存数据库、整个实例卷与 MinIO 桶。更新 API 镜像后才具备 MinIO SDK 与 LibreOffice 文档预览运行时，仅执行 SQL 无法提供 Office 预览。

已有附件表的 MinIO 升级执行 `upgrade_attachment_minio.sql`，或由新版 API 启动自动补齐 `storage_backend`、`storage_bucket`；原记录默认保持 `local`，不推断历史文件已经位于 MinIO。运行 `docker compose exec -T api python scripts/migrate_attachments_to_minio.py`，逐个上传并回读校验大小与 SHA256 后才切换为 `minio`，保留本地原件，支持重复执行。新上传写入配置的私有桶，每条记录保留实际桶名。

## 变更附件升级

已有数据库执行 `upgrade_change_attachments.sql` 后重启 API；也可由新版 API 启动时自动创建 `change_attachments`。升级脚本可重复执行，不改动已有变更单。附件原件写入当前配置的私有 MinIO 桶，设置 `ATTACHMENT_STORAGE=local` 时使用私有本地目录；附件元数据保存于数据库。上传权限仅授予变更单创建人，查看、预览与下载沿用变更单可见权限。

## 飞书 open_id 加密升级

`users.feishu_open_id` 使用应用主密钥自动加密写入数据库，应用 ORM 读取时才在内存中解密。接口只返回脱敏值，飞书通知发送前短暂解密，不把完整 open_id 返回给前端。新版 API 启动会迁移历史明文；也可先执行 `upgrade_feishu_open_id.sql` 扩展字段，再重启 API 完成迁移。必须备份数据库和 `api_instance/keys.json`，更换主密钥会使历史密文不可恢复。

## v1.0.1 角色与部门职责

已有业务库执行 `v1.0.1.sql`，同步更新 API、Worker、前端镜像。脚本仅合并旧角色到新角色；不包含演示数据，不清空业务、密码或审批历史，可重复执行。新版后端也能兼容未迁移的历史角色和流程快照。部门沿用 `users.department`，不增加结构或环境配置。职责和部署说明见 `../docs/roles-v1.0.1.md`。

## v1.0.2 知识库版本

`v1.0.2.sql` 为已有文章增加独立修订号 `revision`，可重复执行，API 启动也可自动补充。自动保存不再递增文档版本，手动保存成功才生成一个历史版本。已有版本保留；更新前后端后刷新编辑页面。详见 `../docs/wiki-versioning-v1.0.2.md`。
