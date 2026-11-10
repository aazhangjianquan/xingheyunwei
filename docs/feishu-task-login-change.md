# 飞书任务消息免密进入方案

日期：2026-11-09。用户回复“可以”，确认方案并授权实施，随后提供正式域名 `https://xh.agentspro.cn`，完成飞书应用配置并明确要求升级。状态：已部署线上并开启任务免密，真实飞书用户授权仍需用户点击任务确认。

## 目标与范围

从飞书机器人消息点击“查看任务并处理”或“查看申请并审批”，通过当前飞书用户身份登录平台并打开对应单据；普通浏览器在没有有效平台会话时使用账号密码登录。

复用管理员已经配置的成员 open_id，不创建新账号，不开放自助绑定，不改变任务分配和审批权限。已有有效平台会话继续按当前机制恢复；不要求每次打开浏览器都重新输入密码。

## 验收标准

- 飞书内点击任务消息，身份认证成功后打开原任务，无需输入平台账号密码。首次使用可能需要确认飞书授权，具体取决于企业应用授权与客户端能力。
- 普通浏览器未登录时，访问首页、任务地址或复制出的消息地址均显示账号密码登录。
- 飞书实际认证的 open_id 只能映射到同一应用下已绑定且启用的平台成员；缺少绑定、账号停用、授权取消或认证失败时显示可操作的提示，不自动创建账号。
- 登录成功后仍检查单据查看与操作权限。消息转发后按实际打开者身份处理，不冒用原收件人；点击卡片不会自动提交审批或执行结果。
- state、临时票据和授权码不可重放，跳转地址仅允许平台内已知任务路由；消息链接不携带平台长期登录令牌或成员密钥。
- 保留账号密码登录、通知投递与重试、原有会话恢复行为。飞书身份与已有会话不一致时避免直接沿用其他人的身份。

## 变更前已确认的行为

1. 线上 `http://101.126.67.61/` 可访问，公开 `/api/auth/options` 返回 `feishu_login_enabled=false`、`local_login_enabled=true`。
2. `backend/app/auth_feishu.py` 的飞书登录、绑定、回调及票据接口均返回 410 停用提示。
3. `backend/app/settings.py` 强制密码登录模式；`backend/app/main.py` 启动时将旧配置归一化，因此只改数据库开关不会启用免密。
4. `backend/app/notification_cards.py` 的卡片按钮通过 `open_url` 打开普通任务详情地址，没有飞书身份认证。
5. `frontend/src/App.vue` 从 localStorage 恢复平台会话；没有会话时显示密码表单；旧飞书回调参数被处理为停用提示。
6. `users.feishu_open_id` 已支持加密保存，通知发送时复用该身份；可用于认证后匹配平台成员。
7. `OAuthState`、`LoginTicket` 旧表模型仍存在，目前没有使用。可评估复用，不以恢复旧接口代码代替新的鉴权审查。
8. 当前检查未读取线上管理配置、App Secret 或数据库；线上飞书应用的网页授权能力、允许地址和可用范围尚未确认。

## 拟定流程

1. 新任务卡片链接增加受控的飞书任务入口，携带平台内任务目标。
2. 飞书客户端内的入口发起飞书官方身份认证；后端验证授权结果、应用与租户上下文，并匹配启用成员的 open_id。
3. 使用短期、一次性且绑定发起浏览器的状态与票据完成平台登录，将会话写入现有前端机制后清除临时参数，并打开原任务。
4. 普通浏览器进入该入口时转到对应任务的密码登录流程。客户端环境或入口参数只影响交互引导，服务端必须验证真实飞书凭据才可签发平台会话。
5. 授权失败可重试或使用密码登录，保留任务目标。飞书凭据、授权码和完整 open_id 不写入日志。

实施采用飞书 OAuth 授权码流程，不承诺首次使用完全没有授权确认界面。用户指定正式平台地址为 `https://xh.agentspro.cn`，对应回调为 `https://xh.agentspro.cn/api/auth/feishu/callback`。该前置条件尚未在线上飞书控制台验证。

## 改动预算

| 文件 | 原因 |
| --- | --- |
| `backend/app/auth_feishu.py` | 新增任务免密入口、授权验证与一次性兑换；继续关闭自助绑定 |
| `backend/app/settings.py` | 增加独立的任务免密配置，与普通密码登录分开控制 |
| `backend/app/main.py` | 调整启动配置归一化与认证接入，保留现有业务授权 |
| `backend/app/notification_cards.py` | 为任务按钮生成新的入口地址 |
| `backend/app/admin.py` | 调整飞书配置检查后的指引，涵盖任务免密前置配置 |
| `frontend/src/App.vue`、`frontend/src/api.js` | 任务入口识别、认证完成后恢复任务、失败提示与身份切换处理 |
| `frontend/src/SystemSettings.vue` | 任务免密开关与管理员配置说明 |
| `backend/tests/test_auth_and_config.py`、`backend/tests/test_notification_cards.py` | 更新原密码专用断言并增加关键鉴权回归 |
| `frontend/tests/` 中相关会话回归文件 | 覆盖免密入口与普通密码登录的路由恢复 |
| `README.md`、本文档 | 配置、验收及回滚记录 |

优先复用现有 OAuth 状态、票据表与依赖。如原表无法安全支持浏览器绑定及任务回跳，先补充说明数据结构影响，再调整预算；目前不安排数据库结构变更。

## 风险、验证与回滚

重点风险为登录 CSRF、票据重放、开放重定向、会话身份混淆、停用账号登录，以及把收件人身份误作打开者身份。状态绑定、短期票据原子消费、站内路由白名单、实际 open_id 校验与原有任务授权必须贯穿整个流程。

实现后运行相关后端鉴权与通知卡片回归，检查前端构建和会话路由；验证多 API 副本下的状态共享与票据并发消费。真实飞书人工验收覆盖首次授权、已授权、手机及桌面打开、外部浏览器、转发链接、绑定缺失、停用账号、过期与失败重试。真实飞书授权与线上发布尚未执行，本文不构成验收通过记录。

上线先保持任务免密开关关闭，完成飞书控制台配置与联调后开启。关闭开关即可恢复任务密码登录；回滚前后端与 Worker 到配套版本，旧入口应回落到普通任务登录。保留已有成员绑定与业务数据，废弃未使用临时状态及票据，不恢复或删除当前工作区其他未提交改动。

## 实施记录

独立配置 `feishu_task_login_enabled` 默认关闭，加入公共配置白名单与前端 bootstrap 白名单；原通用登录开关继续关闭，密码登录继续开启。核对后确认启动归一化只处理旧开关，`main.py` 无需因本次需求再改。

飞书内新旧四类任务详情地址均可进入免密流程，新审批卡片另带 `feishu_task=1` 标记。标记或客户端 User-Agent 仅用于交互引导，服务端签发会话必须完成官方授权码兑换及用户信息读取。外部浏览器不启动飞书扫码流程。

`/api/auth/feishu/task/start` 创建 5 分钟状态，使用共享数据库保存哈希；加密、HttpOnly、SameSite=Lax Cookie 保存浏览器随机数与站内目标，HTTPS 时设置 Secure。回调原子消费状态，匹配解密后的 open_id；不根据卡片收件人登录，不自动建号或绑定。

回调创建 60 秒票据，哈希绑定浏览器 Cookie；认证后的 Cookie 额外绑定应用和身份摘要，避免兑换前换绑或切换应用。票据通过 fragment 返回，前端清除临时地址和旧账号会话后 POST 兑换；后端校验同源 Origin、浏览器绑定、票据时效，并再次检查账号状态与身份。成功后沿用平台原令牌及任务授权。

失败返回固定错误标识，前端显示对应中文提示、密码登录与显式重试，不自动循环跳转。Nginx 现有安全日志使用 `$uri`，不含查询参数；API 镜像已关闭 Uvicorn access log，审计只记录请求路径，不包含授权码和票据。

实际新增的配套文件包括 `frontend/src/bootstrap.js` 白名单适配、`backend/app/audit.py` 配置字段中文名、启动性能回归公共字段断言，以及 `backend/tests/feishu_task_ui_fixture.py`、`scripts/verify_feishu_task_ui.cjs` 隔离浏览器夹具。未增加依赖、数据库字段或迁移脚本。保留工作区原有组织管理、项目岗位、提醒等未提交改动。

## 验证记录

以下命令均已执行；使用隔离 SQLite 与模拟飞书接口，不向真实成员发送通知。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 鉴权与配置 | `PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -p 'test_auth_and_config.py' -v` | 27 项通过，包含旧 IP 入口兼容 |
| 卡片与通知投递 | `PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -p 'test_notif*.py' -v` | 17 项通过 |
| 启动、公共配置与审计 | `PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -p 'test_startup_performance.py' -v` | 6 项通过 |
| 全部现有前端回归 | 在 `frontend/` 执行 `node --test tests/*.test.mjs` | 145 项通过 |
| 前端构建 | 在 `frontend/` 执行 `node node_modules/vite/bin/vite.js build` | 通过；提示单个 JS chunk 超过 500 kB，不阻止构建 |
| 真实 Chrome 页面 | 先运行下述夹具，再运行 UI 脚本 | 4 组检查通过，无页面异常 |
| 差异格式 | `git diff --check` | 通过 |

本机 shell 没有默认 `node`，实际使用 Codex 依赖运行时的 `/Users/zhangjianquan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`；Playwright 使用同一运行时的 `node/node_modules`。可复现的浏览器检查：

```sh
PYTHONPATH=backend:backend/tests .venv/bin/python backend/tests/feishu_task_ui_fixture.py
# 另一个终端执行；若夹具输出不同单据 ID，设置 FEISHU_UI_TASK 为该 TASK_PATH。
NODE_PATH=/Users/zhangjianquan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules /Users/zhangjianquan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node scripts/verify_feishu_task_ui.cjs
```

回归覆盖正确身份、外部浏览器、转发后的实际用户、状态与票据过期、重放、同源检查、不同浏览器的有效 Cookie 混用、账号停用/解绑/换绑、开关关闭、HTTPS Cookie、重启保留开关，以及两个并发兑换仅一个成功。浏览器覆盖旧消息链接进入原任务、普通浏览器复制链接、拒绝授权后的失败与重试、管理员开关及回调展示、390px 手机布局。浏览器报告见 `reports/feishu-task-ui.json`，截图为 `reports/feishu-task-*.png`。

首次鉴权回归发现过期票据用例计算 Cookie 哈希时未处理 Cookie jar 的外层引号，修正测试读取后通过；生产请求由 Starlette 按 Cookie 规范解析，不受影响。首次浏览器夹具遇到 Playwright 对 HTTP 重定向只拦截首跳，已改为在夹具中模拟独立导航，完整流程通过。此修改仅影响隔离夹具。

本机检查域名时 DNS 解析超时；通过 `curl --resolve xh.agentspro.cn:443:101.126.67.61 -I https://xh.agentspro.cn` 直接连接现有 IP 的 443 端口未成功。这只能说明本次检查未连通，不能证明域名应指向该 IP。部署前需确认实际域名解析、HTTPS 入口及反向代理。

开发阶段未验证真实企业应用授权、真实飞书客户端和 MySQL 实机；后续线上检查已补充应用凭据、正式授权入口与生产 MySQL 并发消费，详见下方。真实飞书用户首次授权和客户端身份登录仍需用户操作；验证过程未向真实飞书发送消息。

## 上线配置与回滚操作

1. 确认正式域名和证书可用；在系统配置中保存 `public_base_url=https://xh.agentspro.cn`。
2. 在原企业自建应用中启用网页应用，配置主页、应用可用范围及安全设置中的重定向 URL `https://xh.agentspro.cn/api/auth/feishu/callback`，完成发布；保留原机器人消息权限。
3. 部署配套版本的 API、前端、Worker；此工作区 Compose 可执行 `docker compose up -d --build --no-deps api frontend worker`。生产部署应使用其现有 Compose/镜像发布方式，并先保留原镜像和配置。数据库无需执行新增 SQL。
4. 确认成员绑定本应用 open_id，开启「飞书任务免密」，在飞书内完成真实用户联调。只提供飞书密钥不能证明回调地址已配置，凭据检查也不能替代联调。
5. 回滚时先关闭 `feishu_task_login_enabled`，前端立即停止自动认证；有需要时恢复配套版本。临时状态和票据会过期，可保留，不改成员、审批、通知和业务数据。工作区存在其他改动，不通过重置整个工作区回滚本功能。

## 决策记录

背景：已有飞书机器人和成员身份绑定，但平台只接受密码登录，任务卡片会停在登录页。用户要求飞书消息免输密码进入原任务，外部浏览器仍保留密码入口。

选择官方 OAuth 授权码验证实际用户，以独立开关控制任务免密，并复用共享数据库中的状态/票据表。没有采用消息中附加长期平台令牌的方式，因为链接转发会传播登录能力；没有按 User-Agent 或入口参数直接签发会话，因为这些字段不能证明用户身份。没有恢复通用飞书扫码登录和自助绑定，因为本次仅授权任务免密。

通过 Cookie 绑定、固定站内跳转、同源 POST、一次性原子消费及兑换前复核身份减少登录 CSRF、重放与账号混淆风险。现有随机加密 open_id 不能用密文等值查询，匹配在服务端内存完成。若未来成员规模需要加速，可另行设计受控检索摘要，本次不新增个人身份索引或数据迁移。关闭独立开关即可恢复原有密码登录路径。

## 官方接口参考

- 获取授权码：<https://open.feishu.cn/document/authentication-management/access-token/obtain-oauth-code>
- 获取用户访问凭证：<https://open.feishu.cn/document/authentication-management/access-token/get-user-access-token>
- 用户身份：`GET https://open.feishu.cn/open-apis/authen/v1/user_info`。

## 线上升级与验证（2026-11-09）

用户确认已完成网页应用主页和安全设置中的授权重定向 URL，明确授权升级并提供服务器 SSH 连接。`https://xh.agentspro.cn` 已正常解析并返回 200，系统中的平台地址原本已是该域名，飞书凭据与通知均已配置。

读取线上正在运行的应用文件并逐一比较，后端差异仅有 `auth_feishu.py`、`settings.py`、`notification_cards.py`、`admin.py`、`audit.py`。基于现有 v1.0.8 镜像增量构建，不下载或变更依赖。前端采用本地已验收的产物，公开 JS、CSS、SVG 内容哈希均匹配。原数据库、Redis、MinIO、网络、端口及其余 Compose 配置保持。

备份目录为 `/root/xinghe/backups/feishu-task-20261109-190414`，仅 root 可访问；保存一致性数据库备份、原 Compose、容器配置、实例主密钥和升级前后核对信息。镜像构建期间原服务继续工作，随后短暂停止写入并备份，再启动新 API 和其他服务。38 张业务/组织/流程表的完整行集合摘要在停止业务写入后与新 API 启动后相同；主密钥摘要一致，原有系统设置保持，仅新增默认关闭的独立开关，之后按用户授权开启。

服务器当前主密钥位于容器可写层的 `/.instance`。此次明确备份并保留同一主密钥，API、Worker、调度器使用相同镜像并逐个核对。为安全回退额外构建带原密钥的旧代码镜像，避免回退时意外生成新密钥；未把备份密钥下载到本地或上传公共仓库。

生产 MySQL 中对 OAuth 状态与登录票据分别执行两个并发原子消费，每组恰好一个成功；测试仅创建精确标识的临时认证记录，完成后删除。既有飞书应用鉴权成功，未发送任何测试消息。通过系统配置接口逻辑开启 `feishu_task_login_enabled`，并记录“服务器升级（用户授权）”业务配置审计；重启后开关仍为开启。

线上检查发现旧卡片可能仍指向 IP，补充跳转：当飞书任务入口的 Host 与正式域名不一致时，先重定向到正式域名的任务认证入口，再设置绑定 Cookie，避免从 IP 开始的 Cookie 无法送到 HTTPS 回调。新增鉴权用例验证跳转和完整兑换，最终 27 项通过。该兼容修复已随 r2 API 镜像部署。

最终镜像：

- API、Worker、调度器：`yunweishenji-api-v1.0.8-feishu-task-20261109-r2:latest`。
- 前端：`yunweishenji-frontend-v1.0.8-feishu-task-20261109:latest`。
- 保留原密钥的旧代码回退镜像：`yunweishenji-api-feishu-task-rollback-190414:latest`。

原有七个容器均运行正常，API、Worker、调度器、MySQL、Redis 健康；`/api/health` 为 200。容器内 9 个应用/前端发布文件与清单哈希匹配。真实 Chrome 对正式域名的七组检查全部通过：普通浏览器密码登录、公共开关、外部浏览器入口回到原任务、飞书入口生成正确 HTTPS 回调和安全 Cookie、旧 IP Host 兼容跳转、拒绝授权后的提示/密码回退/重试、管理端开关和回调展示及桌面/手机布局。无页面异常、无业务写请求。检查没有代替真实成员完成飞书用户授权。

本机直接访问旧 IP 的 80 端口在最后验证时超时；旧 IP Host 兼容通过服务器内部请求前端代理验证。因此不把旧 IP 的公网可达性记为通过，后续任务消息应使用正式 HTTPS 域名。配置和校验详情见 `reports/feishu-task-production-deployment.json`、`reports/feishu-task-production-ui.json`；截图为 `reports/feishu-task-production-*.png`。

用户下一步：在已登录的飞书客户端里打开任务消息的“查看任务并处理”；首次可能需要确认授权。如果旧消息仍使用不可达的 IP 地址，应从使用正式域名的新任务消息验证。

线上回退应先关闭任务免密开关，再将 Compose 的三个后端镜像改为上述保留密钥的回退镜像、前端恢复 `yunweishenji-frontend-v1.0.8:latest`，使用服务器实际安装的 `docker-compose` 重建服务。此次没有数据库结构迁移，正常回退无需覆盖业务库；完整数据库备份保留供故障恢复。不能仅换回不含原运行期密钥的基础镜像。
