# Docker Compose 部署

## v1.0.9 知识库空间开放范围

从 v1.0.8 升级时，先备份数据库、附件与实例密钥，暂停 API、Worker 和提醒调度器，执行本目录 `v1.0.9.sql`，再同步更新后端服务及前端。SQL 可重复执行；更早版本先按顺序升级至 v1.0.8。内置增量升级也补齐字段和新表，但多副本生产环境建议先执行 SQL。

原空间默认保持指定人员和原成员角色，个人库保持私有。全员或部门开放仅授予阅读权限，人员编辑权限仍单独维护。回退旧代码会收回部门/全员的隐式阅读权限，保留原直接成员；新增表和字段可保留。详见知识库权限变更记录。

## v1.0.8 项目岗位与审批执行

项目岗位独立于旧业务角色，支持从全部启用的平台成员中跨部门选人。销售/售前拆分并同时保留原成员，项目部门负责人改为技术负责人，移除项目管理者和产品开发。部门岗位、成员任职、多部门关系及基础角色保留；组织首审规则保持，运维审批和回填按关联项目的运维负责人、运维人员取人。

先备份数据库、附件与实例密钥，暂停 API、Worker、提醒调度器后执行本目录 `v1.0.8.sql`。从 v1.0.3 升级须依次执行 `v1.0.4.sql` → `v1.0.5.sql` → `v1.0.6.sql` → `v1.0.7.sql` → `v1.0.8.sql`。SQL 仅补充节点资格字段；首次新版 API 启动在事务中完成项目任命与旧授权迁移，验证后再启动其他副本、后台服务和新版前端。不能只升级镜像，不能向已有库导入安装或演示数据。

升级记录为 `system_settings.project_positions_v108`，旧任命与授权按同前缀归档；旧快照、审批历史与发布版本保持。旧全局流程若只明确指定一位运维负责人或回填人，会为缺少该项目岗位的项目补齐等价任命；多人时不猜测，管理员需在项目内指定。迁移不依据历史任务恢复已经撤销的项目成员。回退须同时恢复升级前数据库及旧镜像。详见 [变更与验收](../docs/project-position-routing-change.md)。

## v1.0.7 岗位授权与统一部门首审

备份数据库、附件及实例密钥，暂停 API、Worker、提醒调度器后执行本目录 `v1.0.7.sql`，再同步升级后端各服务和前端镜像。从 v1.0.3 升级依次执行 `v1.0.4.sql` → `v1.0.5.sql` → `v1.0.6.sql` → `v1.0.7.sql`。SQL 可重复执行，本次不能只升级镜像。

四类申请增加本次申请部门；旧记录不回填。首次启动将原直接业务授权迁移为等价部门岗位，保留项目关系和在途审批，新发布流程统一首审。成员角色仅保留基础与系统角色。选择部门只能来自本人已加入部门；负责人本人从上级开始，逐级负责人和其他启用管理员兜底，始终排除申请人。无人可审则提交前提示补齐配置。异常历史授权保留并记录在 `organization_business_positions_v107` 的 `unmapped_users`，修复组织后重启可再次迁移。完整回退需恢复备份，不能直接恢复旧镜像后丢弃岗位授权。详见 [变更记录](../docs/organization-position-approval-change.md)。

## v1.0.6 部门岗位向下引用升级

上级部门定义的岗位可被全部后代部门引用，上下级链路禁止新增同名岗位。人员岗位分配新增实际任职部门，同一成员可在多个部门分别引用同一岗位；现有岗位分配回填原定义部门，已有角色和项目关系保留。

升级前备份数据库与实例密钥，暂停 API、Worker 和提醒调度器的写入。远程当前为 v1.0.3 时，依次执行 `v1.0.4.sql`、`v1.0.5.sql`、`v1.0.6.sql`，再同步升级 API、Worker、提醒调度器和前端镜像。脚本可重复执行；本次涉及表结构变化，不能只升级镜像。历史同名岗位仅报告、不自动合并。启用继承分配后，不直接回退到不理解任职部门的旧代码。详情见 [岗位继承变更](../docs/organization-position-inheritance-change.md)。

`docker-compose.yml` 已直接填好具体值，不需要 `.env`。API 和 Worker 不挂载目录，只有 MySQL、Redis、MinIO 保存各自的数据。

镜像需提前放到目标服务器：`yunweishenji-api:latest`、`yunweishenji-frontend:latest`。使用当前源码构建的镜像，包含禁用演示数据及停用限流的更新；构建架构与目标服务器保持一致。

## 1. 启动数据库、Redis、MinIO

在本目录执行：

```sh
docker compose config --quiet
docker compose up -d --wait mysql redis minio
```

## 2. 正式新装：先使用空数据库

`v1.0.0.sql` 是纯结构文件，只创建缺少的表，**不会删除旧用户、项目、申请单或演示数据**。如果 `./data/mysql` 已经启动过，必须先确认这是可以清空的正式新环境，并保留备份：

```sh
docker compose down
mv data/mysql "data/mysql-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p data/mysql
docker compose up -d --wait mysql
```

已有正式业务数据时不要执行上面的清空步骤；应保留原库，使用迁移方式处理。不要为了清理数据直接重新执行 `v1.0.0.sql`。

## 3. 手动执行 SQL

新装执行 `database/v1.0.0.sql`，只有表结构，没有测试数据。可直接运行：

```sh
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot' < database/v1.0.0.sql
```

数据库名统一为 `yunweishenji`，账号为 `xinghe`。有旧数据时先备份，Compose 中修改库名或密码不会自动修改已有数据库。

## 4. 创建附件桶

访问 `http://服务器IP:9001`，使用 Compose 中的 MinIO 账号密码登录，创建私有桶 `yunweishenji`。

## 5. 启动平台

```sh
docker compose up -d --wait --wait-timeout 300 api worker frontend
```

访问 `http://服务器IP`。初始管理员账号是 `admin`，密码在 API 的 `BOOTSTRAP_ADMIN_PASSWORD` 中，首次启动前可以直接修改。已有账号不会因修改此值而重置密码。正式前端镜像已移除“演示角色”和演示密码入口；如果页面仍显示它，说明服务器仍在使用旧的 `yunweishenji-frontend` 镜像。

API、Worker 重复填写的六项配置保持一致。`APP_SECRET` 和 `FERNET_KEY` 固定保存，迁移已有平台时必须使用原来的两种主密钥。没有 SQL 自动导入、请求限流、API 挂载或额外配置脚本。三个 `data` 目录保存持久数据，不要删除；只有确认是正式新装且已备份时，才按上面的步骤重建 `data/mysql`。

## API 启动报排序规则冲突（1267）

日志中的 `utf8mb4_0900_ai_ci` 与 `utf8mb4_unicode_ci` 冲突，来自旧后端镜像的知识库审计迁移：把数字 ID 转成字符串时，没有给转换结果指定排序规则。当前源码已在这两处比较中显式使用 `COLLATE utf8mb4_unicode_ci`。

使用当前项目源码，在项目根目录重建镜像并更新 API、Worker：

```sh
docker build -f backend/Dockerfile -t yunweishenji-api:latest .
cd docker-deploy
docker compose up -d --force-recreate api worker
docker compose logs --tail=100 api
```

如果在本机构建、另一台服务器运行，需要先把新镜像通过镜像仓库或 `docker save` / `docker load` 传到服务器，再执行服务器上的容器重建命令。只 `restart` 或重新导入 SQL 都不会更新旧镜像里的代码。使用与服务器 CPU 架构一致的镜像。

不用删除数据目录，不用重新导入 `v1.0.0.sql`。不要只给 `DATABASE_URL` 加 `collation` 参数：当前 SQLAlchemy/PyMySQL 的连接初始化会覆盖这个参数，无法保证迁移比较正确。

## v1.0.1 角色与部门联动升级

已有平台保留数据库与密钥。升级文件在本目录的 `v1.0.1.sql`，只有角色迁移，没有演示数据，不重置账号或单据。

先备份数据库并准备新版前后端镜像，在本目录停止业务服务、执行升级、重建容器：

```sh
docker compose stop api worker
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot "$MYSQL_DATABASE"' < v1.0.1.sql
docker compose up -d --force-recreate --wait --wait-timeout 300 api worker frontend
```

角色与部门配置方法见 `../docs/roles-v1.0.1.md`。本次不增加配置变量，不挂载额外目录，不重新导入 `v1.0.0.sql`。如果镜像是在其他电脑构建的，先将新版镜像传到服务器；只重启旧镜像不会更新功能。

## v1.0.2 知识库版本升级

本目录 `v1.0.2.sql` 只补充文章并发修订号，可重复执行，不删除正文和历史版本。已有库先备份，再执行升级并更新 API、Worker 和前端镜像；新版 API 启动也可自动补字段。更新后刷新编辑页面，自动保存不增加版本，点击“保存文档”成功后增加一个版本。不需要新增环境变量或挂载。详见 `../docs/wiki-versioning-v1.0.2.md`。

## v1.0.3 到期飞书提醒升级

本目录 `v1.0.3.sql` 增加 License、资源申请的提醒开关，以及提醒计划、按收件人去重投递和资源运维责任人表，可重复执行，保留历史数据。升级时同步更新 API、通知 Worker、前端与 `reminder-scheduler`；新版 API 启动也会自动迁移。

## 组织、部门与岗位管理升级

远程版本为 v1.0.3 时，准备新版前后端镜像并备份原库、原密钥。在本目录使用新版镜像运行 `docker compose run --rm --no-deps api python scripts/organization_precheck.py`，处理报告中的名称冲突。暂停 API、Worker 和提醒调度服务后，通过原 MySQL 连接执行本目录 `v1.0.4.sql`，再统一启动新版服务；API 初始化会完成旧部门名称的引用回填。

不要重新导入演示数据。历史岗位和负责人不自动推断，由管理员到「用户与权限 → 部门管理」补齐。可用 `ORGANIZATION_WRITES_ENABLED=false` 暂停组织配置与人员归属写入，保持当前兼容版本读取；不支持直接回退到不认识岗位授权的旧镜像。完整操作与验收见 [实施记录](../docs/organization-management-verification.md)。

## v1.0.5 个人提醒升级

备份数据库与密钥，准备新版镜像，暂停 API、Worker、`reminder-scheduler` 后执行本目录 `v1.0.5.sql`，再同步更新 API、Worker、`reminder-scheduler` 与前端。此 SQL 只新增个人提醒、收件关系和附件元数据三张表，可重复执行；从 v1.0.3 升级需先执行 `v1.0.4.sql`。API 启动也会补建缺少的表，不重新导入安装或演示数据。

「工作管理 → 个人提醒」先保存草稿，添加附件后启用。调度器每分钟扫描，收件人必须有飞书 ID；未启用系统飞书通知时会显示待配置，创建人可在配置恢复后重试。附件沿用当前 MinIO 私有桶及存储配置，无需新增环境变量。见 [个人提醒实施记录](../docs/personal-reminders-change.md)。
