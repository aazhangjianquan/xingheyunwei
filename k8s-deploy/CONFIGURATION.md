# 关键配置变量与迁移对照

## 从 Compose 换到 K8s 后，配置放哪里

| Compose 配置 | K8s 对应位置 |
|---|---|
| `build.context` / `dockerfile` | 先在构建机生成并推送镜像，集群读取 `kustomization.yaml` 的镜像地址 |
| 普通 `environment` | `config.env` → Kustomize ConfigMap |
| 数据库密码、主密钥、Redis 密码、MinIO AK/SK | `.private/workspace-secrets.yaml` → Secret |
| `API_REPLICAS` | `manifests/api.yaml` 的 `spec.replicas`，默认 2 |
| Worker 数量 | `manifests/worker.yaml` 的 `spec.replicas`，默认 1 |
| `ports: 8080` | Ingress → 前端 Service 80 → 前端容器 8080 |
| API 的本机 8008 文档入口 | API 仅内网 8000；业务通过平台域名 `/api/`，Swagger 可临时 port-forward 后访问本机 `/docs` |
| `depends_on` / `healthcheck` | startup / readiness / liveness probes，数据库和对象桶提前准备 |
| `api_instance` 卷 | 新包用各 Pod `emptyDir`；两种主密钥统一下发，数据库配置改为环境管理，附件使用 MinIO |
| `mysql_data` / `redis_data` | 外部托管服务，或 StatefulSet 的持久 PVC |
| `127.0.0.1`、Compose 服务名 `mysql` / `redis` | 替换为 Pod 可达的数据库地址、K8s Service DNS；不直接沿用本机地址 |

## 必填的 Secret

所有 API 与 Worker 必须使用**同一个 `workspace-secrets`**。前端只读 ConfigMap，不接收后端 Secret。

| 变量 | 用途 | 必须注意 |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy MySQL 连接串 | `mysql+pymysql://账号:URL编码后的密码@地址:3306/yunweishenji?charset=utf8mb4`；密码含 `@`、`:`、`/`、`#`、`%` 等需进行 URL 编码 |
| `APP_SECRET` | 登录令牌签名 | 所有副本相同，至少 32 字符；迁移必须沿用原值，否则原会话失效 |
| `FERNET_KEY` | 数据库凭据、飞书密钥、License 等敏感数据加解密 | 必须是 32 字节密钥的 URL-safe Base64；迁移必须原值，换新值无法解密历史数据 |
| `REDIS_PASSWORD` | API、Worker、Redis 认证 | 三者一致；已有外部 Redis 时填真实密码，不能留空让生成器改成另一个密码 |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | 私有桶对象读写 | 从原环境安全复制或创建受限账号；此包不写入现有真实 AK/SK |
| `BOOTSTRAP_ADMIN_USERNAME` | 空库初始管理员账号 | 新装默认 `admin`，可在私有输入中修改；已有用户时不再创建 |
| `BOOTSTRAP_ADMIN_PASSWORD` | 空库初始管理员密码 | 至少 12 位、至少三类字符；留空由生成器生成。重启或修改 Secret 不会重置原密码 |
| `BOOTSTRAP_ADMIN_NAME` | 初始管理员显示姓名 | 默认“系统管理员” |

Secret 是敏感配置的载体，Base64 不等于加密；使用集群现有的密钥管理、RBAC 和备份机制保护它。`.private/` 已加入 Git 忽略；不要提交生成后的 Secret、原 keys.json 或数据库备份。

## ConfigMap：后端与对象存储

| 变量 | 包内值 | 调整方式 |
|---|---|---|
| `SEED_DEMO_DATA` | `false` | 生产保持 false，禁用演示用户、项目、文章和申请初始化 |
| `REQUIRE_SHARED_KEYS` | `true` | 生产保持 true，缺少统一主密钥时直接拒绝启动 |
| `DATABASE_CONFIG_MODE` | `environment` | 生产保持 environment，只读取 Secret 的连接串；页面保存数据库连接会提示由部署统一管理 |
| `REDIS_URL` | `redis://workspace-redis:6379/0` | 默认本包 Redis；外部 Redis 填实际稳定服务地址，TLS 用 `rediss://`。直接 Sentinel / Cluster 发现协议未实现 |
| `REDIS_NAMESPACE` | `project_workspace_prod` | 同一环境 API / Worker 必须一致；不同平台/测试环境需不同。只允许字母、数字、下划线、短横线 |
| `ATTACHMENT_STORAGE` | `minio` | 多副本生产固定使用 MinIO；禁止依赖各 Pod 的 local 文件存储 |
| `MINIO_ENDPOINT` | 当前已知的 `http://115.190.46.231:9000` | 确认 K8s 节点/Pod 可达；生产已有 HTTPS 内网地址时优先使用。填写 S3 API 地址，非控制台 9001 |
| `MINIO_BUCKET` | `yunweishenji` | 桶需提前创建，保持私有；迁移时保留原对象和存储桶记录 |
| `MINIO_REGION` | `us-east-1` | 与 MinIO / S3 实际区域一致；单区域 MinIO 常用此值 |
| `CORS_ORIGINS` | `https://workspace.example.com` | 修改为实际完整前端来源；多个用逗号分隔。同源代理也要核对正式域名 |
| `INSTANCE_DIR` | `/app/instance` | Pod 临时目录，保存由同一 Secret 派生的 keys.json 与锁文件；不存业务附件 |
| `SQL_DIR` | `/app/database` | 只读挂载纯结构 SQL，页面可下载 `v1.0.0.sql` / `schema.sql`，不挂载演示数据 |
| `LICENSE_TOOL_JAR` | `/app/license-tool/LicenseTool-1.0-SNAPSHOT-jar-with-dependencies.jar` | 镜像中必须存在；随 API 镜像一起更新 |
| `JAVA_BIN` | `java` | API 镜像已安装 Java 17；可保留 |
| `SOFFICE_BIN` | 未显式设置 | 默认查找镜像中的 LibreOffice；只有自定义镜像变更可执行路径时才需要设置 |
| `HOME` / `TMPDIR` / `XDG_CACHE_HOME` | `/tmp`、`/tmp`、`/tmp/.cache` | 非 root、只读根文件系统下，为文档预览提供可写临时目录 |

飞书 App ID、App Secret、通知开关、成员 open_id、平台名称和通知里的访问地址仍在系统配置/用户管理中设置并保存在 MySQL，不在这里另建一套环境变量配置。正式域名变更后，同步保存系统配置中的平台访问地址。

## 操作日志的真实客户端 IP：代理配置对应实际网络

链路为 **客户端 → Ingress Controller → 前端 Nginx → API**。

1. Ingress 必须正确识别公网/内网客户端。直连云负载均衡时按控制器和 CNI 的要求保留源地址，例如合适的 `externalTrafficPolicy: Local`；如果前面还有可信 L7 代理，按控制器文档设置可信代理 CIDR、转发头或 PROXY protocol。不要无条件信任互联网用户提交的 X-Forwarded-For。
2. `INGRESS_PROXY_CIDR` 供前端 Nginx 使用，表示 Ingress 与前端建立连接时的实际来源范围。包内 `10.244.0.0/16` 是 Pod CIDR 示例。Ingress 使用 hostNetwork / SNAT 时可能是节点 IP，需要按实际链路修改。模板目前接受单个 CIDR；多范围请在模板中增加对应的 `set_real_ip_from`。
3. `TRUSTED_PROXY_CIDRS` 供 API 使用，表示前端 Pod 的地址范围，多个用逗号分隔。前端恢复真实客户端地址后覆盖写出 X-Forwarded-For，API 只接受可信前端的头。
4. `network-policies.yaml` 把前端入站限制为 Ingress namespace + Controller Pod，把 API 入站限制为前端 Pod。必须使用支持 NetworkPolicy 的 CNI，并核对实际 namespace / 标签，不能用 `0.0.0.0/0` 代替信任配置。

可以只读查看现状，再填写实际地址：

```sh
kubectl get nodes -o custom-columns=NAME:.metadata.name,POD_CIDR:.spec.podCIDR
kubectl -n ingress-nginx get pods -o wide
kubectl -n kube-system get svc kube-dns
```

`DNS_RESOLVER` 默认 `kube-dns.kube-system.svc.cluster.local`；自定义 DNS 域或 Service 名的集群应填写实际集群 DNS IP。`API_UPSTREAM=workspace-api:8000` 对应 API 的 Headless Service，Nginx 根据就绪 Pod DNS 动态更新，不使用 Docker 的 `127.0.0.11`。

本模板按常见 IPv4 Pod 网络与 ingress-nginx 编写。IPv6-only 集群需调整 Nginx resolver 的 `ipv6=off` 设置及两个可信代理范围；使用其他 Ingress Controller 时同步调整 Ingress annotations 与 NetworkPolicy。

最终核对 Nginx 和 API 日志中的客户端来源地址。两个不同真实 IP 应显示不同地址；如果均显示同一个 Ingress / 节点地址，先修正该链路，再开启面向用户的入口。当前请求限流和频率告警已停用，不设置 IP、账号或平台总量配额。这里的可信代理配置仅用于准确记录来源 IP。

## 镜像、存储与容量

- `kustomization.yaml` 的镜像地址和 Tag 必改；版本建议固定 Tag 或 digest，避免 latest。API/Worker 使用同一后端镜像。
- 私有仓库按集群规范准备镜像拉取 Secret，并在三个 Deployment 的 Pod spec 配置 `imagePullSecrets`。构建目标架构必须匹配节点。
- Redis 默认 `ReadWriteOnce` 5Gi PVC；无默认 StorageClass 时要填写 `storageClassName`。可选 MySQL 为 20Gi PVC。PVC 不应随卸载应用而误删。
- Redis `maxmemory=256mb`，容器上限 768Mi，预留 AOF 重写与进程内存；`noeviction` 保护待处理通知队列，容量不足会明确失败。按实际流量同时调整 Redis 内存参数和容器限制。
- API 默认 2 副本，每个请求 250m CPU / 512Mi，最大 2 CPU / 2Gi；LibreOffice 预览和 Java 生成 License 会额外占用内存，按文件大小及并发测量后调整。
- Worker 默认 1，前端默认 2；可以独立扩容。API 和前端各有至少保留 1 个的 PDB，优先分散到不同节点；单节点不提供节点级容灾。
- API startup probe 最长约 10 分钟，覆盖迁移等待；readiness 检查 Redis，liveness 只检查 API 进程端口，避免 Redis 故障引发全平台反复重启。
- 若集群要求限制出站流量，需要另外放行 DNS、数据库、Redis、MinIO 与飞书 HTTPS；当前 NetworkPolicy 只限制入站，不阻断这些出站依赖。

需要临时查看 Swagger 时，可在管理机运行 `kubectl -n project-workspace port-forward deployment/workspace-api 8008:8000`，再访问 `http://127.0.0.1:8008/docs`；无需公开 API NodePort。

## 数据库与迁移

- `v1.0.0.sql` 不创建数据库账号，不包含密码，不写测试数据；应用账号需由 DBA 单独创建。
- 当前版本启动会做增量结构检查，数据库账号需要目标库上的 SELECT/INSERT/UPDATE/DELETE 和 CREATE/ALTER/INDEX/REFERENCES 等相应权限；初始化用 MySQL 命名锁串行，防止多个副本并发迁移。
- 新装先建表，再启动应用；迁移先恢复原库及原两种主密钥。旧数据上不要使用“重新生成主密钥”或“删 PVC 重装”来修复启动失败。
- Secret 修改不会自动改变已启动容器的环境变量；数据库连接、主密钥、Redis 密码修改后，按计划同时滚动 API / Worker。主密钥不能直接轮换，历史密文需要专门重加密迁移。
- 只有使用 MinIO 且数据库环境管理开启时，API/Worker 的临时实例目录才不需要共享持久盘。历史 `storage_backend=local` 附件必须先转入 MinIO。
