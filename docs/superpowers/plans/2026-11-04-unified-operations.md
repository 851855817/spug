# 面板任务内整合主机巡检

**目标：** 用户进入面板任务即可使用原运维作业，减少重复导航，保留权限、历史和操作确认。

**架构：** 页面组合，后台和数据库保持原样。现有隔离 checkout、feature/operations-reliability 和 PR #4 继续使用。按 writing-plans 与 executing-plans 内联执行，关键变化先失败测试。

## 实现

- [x] 在 `spug_web/src/pages/exec/panel/index.test.js` 验证管理员两类型切换、未授权不读取任何数据、仅宝塔用户不能读取巡检、预填查询保存、选择类型写入 URL、旧路由带 run_id 重定向。
- [x] 提取原 `panel/index.js` 内容到 `panel/Workspace.js`；新 index 只提供 Breadcrumb、任务类型切换、授权和组件挂载。`operations/index.js` 增加 embedded 模式，隐藏独立 Breadcrumb／重复标题并统一新建任务标签。
- [x] `routes.js` 面板任务 auth 使用 `exec.panel.view|admin`，原运维作业路由隐藏并指向兼容重定向；保留管理员限定。路由静态检查确保只有一个可见入口。
- [x] 验证巡检旧结果 URL、浏览器前后导航、切换只读、计划表单和 SSL 链接。更新 operations 指南与平台手册。

运行前端目标测试：`D:/nodejs/node.exe node_modules/react-app-rewired/bin/index.js test --watch=false --runInBand src/pages/exec/panel/index.test.js src/pages/exec/operations/index.test.js src/pages/schedule/PanelPlanSelector.test.js src/pages/schedule/PanelPreview.test.js`（spug_web，NODE_OPTIONS=--openssl-legacy-provider）。通过后完整前端回归、目标 ESLint 和 build。

## 发布

- [x] 新增固定 `panel-workspace-ui` profile，严格限制仅 routes、panel/index、panel/Workspace、operations/index、panel/TaskDetail 的五处 UI 变更（含切换后确认框失效修复），不允许 API 或数据库修改。复用旧发布器完整文件收据和配对备份，并补 SSL 六表已有数据、活跃 SSL 作业／租约的保全和空闲判断，不忽略新表。
- [x] 发布工具先失败测试，验证新 SSL 基线 migrate-only 入口、候选全 API 与原镜像一致、默认配置无变化、SSL 非空表可保全、活跃 SSL 任务阻止发布及失败回退。
- [x] 干净提交、打包、隔离 Linux 回归和候选镜像、GitHub CI、模拟浏览器验收。重新核实现网版本及业务空闲后建立新配对备份、发布、独立核对页面与全部业务摘要。
- [x] 更新实际结果与未处理事项、原草稿 PR，不合并主分支。不得执行客户任务或改动客户监控开关。

## 实现与本地证据（2026-11-04）

- 统一入口、SSL 预填、旧结果重定向、浏览器前后导航和类型权限已实现。前端 49 套／477 项通过，五处运行时 UI 目标 ESLint 通过。
- 独立审查复现并修复切换后残留确认框：离开任务销毁弹窗，generation 防止旧回调提交，动作提交前重查权限。5 条新增回归先失败后通过。
- 发布保护保留全部 SSL 表。仅规范化已释放 worker（空 token、空 job、已过期）的 expires_at，仍保留行与其他字段；15 分钟内到期的周期来源会阻止发布，不修改用户开关。隔离真实 SQLite 与真实 scheduler tick 审查证实无任务时两 tick 摘要稳定，到期来源仍阻止。
- SSL 发布回归 34 项通过；本次发布 profile 12 项通过。最终完整发布回归、构建、模拟浏览器、Linux 隔离与现网验收继续记录在发布记录中。

## 最终结果

应用 `e6ee4e3` 已于 2026-11-04 14:24（UTC+8）上线。Linux 682 项、发布保护 197 项、到期 10 项、SSL 发布 34 项、post-ssl 镜像 6 项均通过；最终 Chrome 10 条验收和应用／PR CI 三作业成功。新配对备份、69 项摘要、完整 API、六表既有数据、9 服务、15 项健康及公网 200 已独立核验。详见 [发布记录](../../releases/2026-11-04-panel-workspace.md)。未合并主分支，未执行客户任务或修改其开关。
