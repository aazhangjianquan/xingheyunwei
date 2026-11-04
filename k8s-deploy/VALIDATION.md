# 部署包验证结果

验证使用独立、可销毁的 MySQL / Redis / 应用容器，未使用业务数据库写入验证数据，未向实际 Kubernetes 集群执行部署。

| 检查 | 结果 |
|---|---|
| Kustomize 离线渲染 | 17 个默认资源，另有 2 个可选 MySQL 资源 |
| K8s 1.31 严格资源格式校验 | 通过；实际集群还需检查其版本、准入、CNI、Ingress 和 StorageClass |
| MySQL 8.4 导入 `v1.0.0.sql` | 28 张表全部建立；导入后逐表 COUNT 均为 0 |
| 纯结构 SQL 重复导入 | 通过，未插入任何数据 |
| SQL 与模型一致性 | 28 张表与当前 ORM 对齐，生成结果可重复检查 |
| 生产首次启动 | 两个应用容器并发启动后，只有指定的一个管理员，没有演示项目、申请单、文章或通知 |
| 跨副本密钥及登录 | 两个独立临时实例目录使用同一环境密钥，同一登录令牌可访问两个应用容器 |
| API、前端、Worker、Redis 非 root + 只读根目录 | 实际运行通过；临时目录和数据目录单独提供可写挂载 |
| Java License 工具 | 以非 root 身份、只读根目录生成验证授权成功，未输出密钥内容 |
| 真实 IP 代理模板 | 模拟可信 Ingress 后，前端 Nginx 和 API 正确识别两个不同客户端 IP |
| 原版本限流检查（历史） | 这是停用前的验证结果；2026-10-29 已移除 API 入口限流，旧 20/120 配额不再执行 |
| 后端回归 | 169 项执行，166 通过、3 项外部环境测试跳过；最终迁移/知识库修正另有 16 项回归通过 |
| 前端回归及镜像构建 | 93 项通过；两个 v1.0.0 验证镜像均构建成功 |

上述容器运行检查覆盖应用在非 root / 只读文件系统下的行为，不等于在你的实际集群验证过负载均衡、证书、PVC 挂载、真实客户端 IP 或外部数据库/MinIO 网络。按 README 完成环境替换后，再执行集群服务器端 dry-run 与部署验收。

详细本地报告位于项目的 `reports/k8s-validation.json`、`reports/k8s-sql-verification.json`、`reports/k8s-runtime-verification.json` 及对应测试日志。报告不包含真实凭据。

## 2026-10-29：停用请求限流

已移除 API 请求入口的限流和频率告警生成，无需新增环境开关。可信代理配置保留用于操作日志的来源 IP；Redis 继续承担通知队列与飞书令牌缓存。K8s 部署需按 README 从当前源码构建新镜像，实际集群未执行更新。

- 后端 169 项回归执行通过，151 项通过、18 项外部环境测试跳过；另外提供隔离 Redis 后，Redis 模块 16 项全部通过。
- 隔离环境中，同一登录客户端连续 130 次请求全部成功，不创建计数或频率通知；超过旧短窗口后仍可创建文章并记录业务日志。
- 本机 Docker 两个 API 副本及 Worker 已更新；实际 Nginx 入口 2.26 秒内连续 130 次未登录请求全部返回原有认证错误 401，没有 429、计数增长或频率通知增长。
- 通知 Worker 消费与 ACK、Redis AOF 及持久化策略验证通过，未向飞书发送验收消息。

本次报告为 `reports/rate-disabled-http-verification.json`、`reports/rate-disabled-runtime.log`、`reports/rate-disabled-backend-tests.log` 和 `reports/rate-disabled-redis-tests.log`。
