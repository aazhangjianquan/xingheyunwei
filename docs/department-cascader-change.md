# 成员所属部门分级选择与搜索

## 需求与现状

2026-11-09，用户要求添加成员的所属部门先显示第一层、可逐层选择，并支持部门搜索。本次以用户明确指定的交互为实施范围，无需再次确认同一方案。沿用已有需求变更工作流核对入口、影响、回退和验证。

`App.vue` 添加成员目前用原生 select 展示全部部门完整路径，`MemberOrganization.vue` 调整成员部门使用同样的平铺列表；岗位列表随最终选定的部门变化。已有 `/departments` 响应包含 id、parent_id、name、path、active，足够在前端完成分级与搜索。

## 范围与设计

- 共用 DepartmentPicker：打开只显示提供目录中的顶层部门；点击有下级的部门展开下一级，末级直接选择。非末级提供“选择本级”，保留任职父部门的能力。
- 可搜索部门名称和完整路径，结果显示完整路径，点击结果直接选择已有部门。
- 浏览下级、搜索、取消不会改变已选部门或清空岗位；只有确认另一个部门才触发现有岗位切换。
- 沿用两个成员入口已有的部门可见范围、停用限制、提交 department_id 和服务端权限校验。权限受限目录中缺少父级的部门作为可见顶层显示。
- 支持键盘、必填校验、弹层外点击关闭和手机布局；弹层不被成员对话框的滚动区域裁剪。

## 影响与回退

变更预算为新前端选择组件、两个成员入口、相关真实浏览器验证及本文档；没有后端、权限、数据模型或 SQL 变化。项目表单、申请部门、组织树等其他入口不在本次范围。已有工作区修改保留。

风险主要是原生 select 的必填校验、岗位重置时机、分支部门可选性、受限部门目录和小屏弹层；用浏览器交互验证。回退只需恢复本次前端差异或部署前保留的前端镜像，数据库无需回退。

## 验证记录

2026-11-09 完成以下实际验证：

- `node --test frontend/tests/*.test.mjs`：138/138 通过。
- `pnpm --dir frontend build`：通过，无新增依赖。
- `node scripts/verify_department_picker_ui.cjs`：临时 SQLite、loopback API 的 15 项真实 Chrome 验收通过，浏览器运行错误为 0。覆盖第一层展示、9 级部门导航、面包屑、父部门与叶部门选择、名称及路径搜索、空结果及清空、浏览取消保留岗位、重复选择保留编辑、确认换部门重置岗位、原生 required 拦截、键盘及外点击、手机边界、实际创建与组织保存、负责人原权限、停用历史部门、缺少可见父级、同名展示与空目录。
- 桌面 1440×1080、手机 390×844 截图已查看，弹层没有横向溢出或被表单裁剪。导航切换时发现的焦点丢失已修复：焦点保留在弹层内，Esc 可关闭并返回触发按钮。
- `git diff --check`：通过。本次 App.vue 仅引入组件及替换该字段；MemberOrganization 保留原过滤规则、组织保存接口和多部门关系。

命令使用工作机 bundled Node 路径；浏览器脚本通过 bundled Playwright 与本机 Chrome 执行。隔离验收报告为 `reports/department-picker-ui.json`；截图为 `reports/department-picker-{desktop,mobile,deep}.png`。特殊历史和受限目录场景通过隔离浏览器响应覆盖验证，未更改部署系统的数据。

## 本地部署

已执行 `docker compose build frontend` 和 `docker compose up -d --no-deps --wait frontend`，更新 `http://localhost:8080`；仅重建前端，全部 7 个服务健康。前端发布标签仍为 `yunweishenji-frontend-v1.0.7:latest`，镜像 ID `4baa0e0ad311`。无数据库、SQL 或后端变化；不新增 v1.0.8 SQL。远程服务器未操作。

`node scripts/verify_department_picker_deployed.cjs`：部署后 5 项只读 Chrome 验证通过，覆盖添加成员首层、父级选择、搜索路径、手机布局及 Esc、调整组织岗位入口；无 API 写请求或浏览器运行错误。报告为 `reports/department-picker-deployed-ui.json`，截图为 `reports/department-picker-deployed-{desktop,mobile}.png`。

部署前镜像保留为 `yunweishenji-frontend:before-department-picker-20261109-152425`。如需回退，将该标签重新打为 `yunweishenji-frontend:latest` 与 `yunweishenji-frontend-v1.0.7:latest`，再执行 `docker compose up -d --no-deps --wait frontend`；无需数据库回退。详情见 `reports/department-picker-deployment.json`。

## 决策记录

- 共用单列逐级导航和面包屑，保证任意深度在手机上也能进入和返回；进入父部门只浏览，点击“选择本级”明确确认归属。
- 打开时始终从可见顶层开始，只有确认部门才更新值；搜索结果展示完整路径以区分同名或相似部门。
- 复用已有目录与权限过滤，不为补齐层级请求额外部门；目录中不存在的父级视为边界。
- 使用 Teleport 和视口定位防止表单滚动容器裁剪弹层，同时保留原生必填校验和现有岗位、组织 API；回退边界仅为前端。
