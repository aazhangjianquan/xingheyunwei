# Docker Compose 部署

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
