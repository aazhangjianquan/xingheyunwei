# 个人提醒：需求、设计与验收

## 已批准范围

2026-11-09 用户确认：提醒选择北京时间日期和时刻；列表和详情仅创建人可见；被提醒人登录后可查看、下载附件；允许实施并新增 `v1.0.5.sql`。

- 工作管理新增「个人提醒」。填写一段文字、一次性提醒时间，选择一位或多位收件人，也可只选自己。
- 仅已启用且已配置飞书 ID 的成员可选；后端重复校验，不返回飞书 ID。
- 仅创建人可管理提醒；系统管理员不获得其他人的提醒查看权限。
- 附件沿用私有 MinIO / 本地存储与文件内容校验，单文件 20 MB。接收人到提醒时间后可登录附件页，不能访问创建人的提醒列表和详情。
- 复用现有每分钟调度器和通知 Worker；数据库持久化、按提醒和收件人去重，失败沿用三次重试预算。不开启重复提醒，不调整工单审批与现有到期提醒。

## 代码基线与影响

`frontend/src/App.vue` 定义工作管理导航；`backend/app/reminder_scheduler.py` 每分钟生成到期通知，`notification_queue.py` / `feishu.py` 持久化与投递；`attachments.py` / `attachment_storage.py` 处理私有文件。现有提醒绑定资源与 License，没有个人提醒模型。

新增独立个人提醒 API、模型和页面；现有入口仅负责挂载。调度器、飞书发送前检查和通知列表增加个人提醒分支，防止取消后继续投递或通过管理员通知列表泄露内容。数据库安装结构与升级包同步更新。

## 状态和失败恢复

先保存草稿，再上传文件，最后启用定时提醒。上传失败保留草稿与已上传文件，可继续上传；启用时再次检查未来时间及收件人。到时锁定提醒并为每位收件人创建唯一通知记录，然后进入已触发状态。取消或删除阻止尚未发送的通知；已发消息不会撤回。附件保留私有存储，不生成公开链接。

## 风险、验证与回退

验证跨账号和管理员越权、附件接收者权限、无飞书 ID 与停用账号、北京时间换算、调度重复扫描、取消与发送前复核、上传失败恢复及旧提醒回归。运行前端生产构建并实际检查页面。

升级只新增表，使用 `v1.0.5.sql`；从 v1.0.3 需先执行 v1.0.4 组织迁移。回退前停写并停止新版调度器，保留新增数据表和文件；旧版不会处理个人提醒。无需删除表或恢复整库，不回退已有业务数据。

## 决策与验收记录

采用独立模型，避免将一次性个人提醒塞入工单到期规则；沿用现有通知与文件基础设施，避免增加调度依赖。发送前重新核对取消状态、创建人账号和收件人配置；失败通知由创建人在详情中重试，系统管理员不能通过通知重试接口操作别人的提醒。最多选择 100 位收件人，最多添加 10 个附件。

时间按 UTC 保存，输入和展示固定为北京时间；浏览器时区不参与时间解释。到时才开放指定收件人的附件页，只提供文件，不返回创建人的文字、其他收件人或发送记录。删除提醒采用软删除，阻止访问并保留文件供配套备份；已发出的飞书消息无法撤回。

## 验证记录

- 后端：`PYTHONPATH=backend .venv/bin/python -m unittest backend.tests.test_personal_reminders backend.tests.test_reminders backend.tests.test_notifications backend.tests.test_notification_cards backend.tests.test_attachments backend.tests.test_k8s_deployment -q`，65 项，1 项按原条件跳过，无失败。覆盖跨账号及管理员越权、收件人资格、时区、草稿启用、附件验证及共享、取消与删除、调度幂等、发送前撤销、创建人重试。
- 前端：`node --test frontend/tests/*.test.mjs`，138 项全部通过，其中新增 5 项个人提醒交互测试；验证保存和启用失败保留草稿、重复提交、上传异常阻止启用、卸载忽略迟到结果，以及旧附件功能。生产构建 `pnpm --dir frontend build` 通过。
- MySQL 8.4：独立 tmpfs 测试容器导入无个人提醒的旧结构，插入旧账号和通知，再执行 v1.0.5 两次。旧密码哈希和通知内容完整保留；两条线程同时扫描同一提醒只生成一条通知，取消后记录停止。报告 `reports/personal-reminder-mysql.json`。
- 实际浏览器：构建后 Vue 配合独立 SQLite API，`scripts/verify_personal_reminders_ui.cjs` 的 8 项检查通过；无页面异常。覆盖桌面、390px 布局、两位收件人、草稿上传与启用、附件预览下载、其他管理员拒绝访问、被提醒人列表隔离。浏览器时区设为 `America/New_York`，输入北京时间 09:30，正确保存 UTC 01:30。复验时先运行 `PYTHONPATH=backend .venv/bin/python backend/tests/personal_reminder_ui_fixture.py` 启动独立临时库，再运行浏览器脚本（需要 Playwright 与 Chrome）。截图 `reports/personal-reminder-desktop.png`、`reports/personal-reminder-mobile.png`。
- 安装结构校验、四份版本 SQL 一致性与 `git diff --check` 通过。全新安装 SQL 已同步三张个人提醒表，当前模型共 41 张表。

验收仅使用独立测试数据；飞书传输使用模拟接口，没有向真实成员发送通知。个人提醒已于 2026-11-09 随岗位继承一并部署到本地 <http://localhost:8080/personal-reminders>，API、Worker、调度器和前端同步更新；7 个服务健康。实际页面、新建表单及接口只读验收通过，没有向真实成员发送测试消息。备份为 `reports/organization-backups/20261109-123203/`。远程服务器尚未连接；从远程 v1.0.3 更新到当前完整版本，依次执行 v1.0.4、v1.0.5、v1.0.6 后升级镜像。部署详情见 [岗位继承变更](organization-position-inheritance-change.md)。
