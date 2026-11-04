# Docker Compose：直接填写配置

根目录的 `docker-compose.yml` 已填写具体值，没有 `${...}` 外部变量引用，不需要复制 `.env`，也不需要先运行凭据生成脚本。数据库、Redis 和 MinIO 使用现有配置值；本次仅修改配置文件，未重启当前服务。

## 复制到其他服务器

复制完整项目目录（包含 `backend`、`frontend`、`database`、License JAR 和 `docker-compose.yml`），在新服务器的项目目录执行：

```sh
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
```

`api` 镜像直接命名为 `yunweishenji-api`，Worker 使用同一镜像。API 副本数直接写为 `replicas: 2`；改成 `3` 即可，无需 `API_REPLICAS` 变量。首次构建需要能够下载镜像和依赖。

当前前端端口为 `127.0.0.1:8080:80`，适合本机访问或宿主机反向代理。若要直接通过其他服务器 IP 访问页面，把这一项改成 `8080:80`，浏览器打开 `http://服务器IP:8080`；MySQL 和 8008 文档端口可继续仅绑定本机。

这是配置写法调整，保留了原 Compose 的演示初始化行为。需要无演示数据的生产新装时，使用 `k8s-deploy/v1.0.0.sql` 替代 MySQL 的初始化结构文件，去掉 `demo_data.sql` 挂载，并在 `api.environment` 直接填写 `SEED_DEMO_DATA: "false"`、`BOOTSTRAP_ADMIN_USERNAME` 和符合密码规则的 `BOOTSTRAP_ADMIN_PASSWORD`。已有数据库不重建、不重新导入初始化 SQL。

## API 中已经填好的连接

| 项目 | 当前值 / 修改位置 |
|---|---|
| MySQL | `DATABASE_URL` 直接连接 Compose 的 `mysql:3306/yunweishenji`，密码已填入 |
| Redis | `REDIS_URL: redis://redis:6379/0`，密码已填入 |
| MinIO | `http://115.190.46.231:9000`，桶 `yunweishenji`，原 AK/SK 已保留 |
| 信任的前端代理 | `TRUSTED_PROXY_CIDRS: 172.29.90.10/32`，对应前端固定 IP |
| 实例目录 | `INSTANCE_DIR: /app/instance` |
| SQL 下载目录 | `SQL_DIR: /app/database` |

修改 MySQL 普通账号密码时，同时修改 `mysql.environment.MYSQL_PASSWORD` 和 `api.environment.DATABASE_URL` 中的密码；连接串密码含特殊字符时需进行 URL 编码。修改 Redis 密码时，同时修改 `redis.environment.REDIS_PASSWORD` 和 `api.environment.REDIS_PASSWORD`。Worker 通过 YAML 共用配置，无需再抄一遍。MySQL 已有数据卷的实际账号密码不会因修改 Compose 自动改变，须先在数据库内同步修改。

`mysql`、`redis` 是同一个 Compose 网络中的服务名，整套服务一起部署时就使用这些名字；只有改用外部数据库或 Redis 时才替换为外部地址。API 经前端同源代理提供服务，Compose 已省略 `CORS_ORIGINS`；直接使用服务器 IP 或正式域名访问同一入口，无需配置它。浏览器直接跨域请求独立 API 时再添加实际前端来源。`TRUSTED_PROXY_CIDRS` 继续保留，用于识别 Nginx 转发的真实客户端 IP，保证操作日志中的来源 IP 准确。请求限流与频率告警已停用，不需要额外配置开关。

## INSTANCE_DIR、SQL_DIR 是否有用

**INSTANCE_DIR 有用，保留。** 后端用它保存 `keys.json`（登录签名及敏感数据加密主密钥）、`database.json`（在页面保存的加密数据库连接配置）和启动锁；历史本地附件也位于此目录下。`api_instance:/app/instance` 让所有 API、Worker 共享并持久保存这些文件。新装首次自动生成同一份主密钥；重建容器不会丢失。当前不必额外填写 `APP_SECRET`、`FERNET_KEY`。

**SQL_DIR 有用，但只用于 SQL 文件列表和下载。** `./database:/app/database:ro` 把宿主机 SQL 目录只读挂入容器，后台“系统配置 → 数据库”通过 `SQL_DIR` 找到这些文件。它不决定数据库文件存在哪，也不会自动执行 SQL。真正的 MySQL 数据在 `mysql_data:/var/lib/mysql`；首次建库执行的是 MySQL 服务的 `/docker-entrypoint-initdb.d/` 挂载。

这两个值已直接写成容器内路径，和 `volumes` 一一对应，不用改成服务器磁盘目录。若要换宿主机存储位置，修改挂载冒号左侧即可。当前镜像的源码默认路径与这些挂载不同，不能单独删掉这两个配置后仍期望文件被正确找到。

## 迁移和更新

迁移旧平台须一起恢复 MySQL 数据、`api_instance` 卷中的原主密钥以及附件对象；不能只复制 Compose 和数据库。新服务器上的同名卷是空卷，不会自动复制旧服务器内容。不要执行 `docker compose down -v` 删除原卷。

直接改 Compose 的环境值后，使用 `docker compose up -d api worker` 重新创建容器，单纯 `restart` 不会加载新环境。文件里的 `$$REDIS_PASSWORD`、`$$MYSQL_ROOT_PASSWORD` 仅供容器内启动命令和健康检查读取上方已经填写的密码，不需要主机再配置变量。
