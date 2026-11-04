# 面板任务内整合主机巡检

**目标：** 用户进入面板任务即可使用原运维作业，减少重复导航，保留权限、历史和操作确认。

**架构：** 页面组合，后台和数据库保持原样。现有隔离 checkout、feature/operations-reliability 和 PR #4 继续使用。按 writing-plans 与 executing-plans 内联执行，关键变化先失败测试。

## 实现

- [ ] 在 `spug_web/src/pages/exec/panel/index.test.js` 验证管理员两类型切换、未授权不读取任何数据、仅宝塔用户不能读取巡检、预填查询保存、选择类型写入 URL、旧路由带 run_id 重定向。
- [ ] 提取原 `panel/index.js` 内容到 `panel/Workspace.js`；新 index 只提供 Breadcrumb、任务类型切换、授权和组件挂载。`operations/index.js` 增加 embedded 模式，隐藏独立 Breadcrumb／重复标题并统一新建任务标签。
- [ ] `routes.js` 面板任务 auth 使用 `exec.panel.view|admin`，原运维作业路由隐藏并指向兼容重定向；保留管理员限定。路由静态检查确保只有一个可见入口。
- [ ] 验证巡检旧结果 URL、浏览器前后导航、切换只读、计划表单和 SSL 链接。更新 operations 指南与平台手册。

运行前端目标测试：`D:/nodejs/node.exe node_modules/react-app-rewired/bin/index.js test --watch=false --runInBand src/pages/exec/panel/index.test.js src/pages/exec/operations/index.test.js src/pages/schedule/PanelPlanSelector.test.js src/pages/schedule/PanelPreview.test.js`（spug_web，NODE_OPTIONS=--openssl-legacy-provider）。通过后完整前端回归、目标 ESLint 和 build。

## 发布

- [ ] 新增固定 `panel-workspace-ui` profile，严格限制仅 routes、panel/index、panel/Workspace、operations/index 的 UI 变更，不允许 API 或数据库修改。复用旧发布器完整文件收据和配对备份，并补 SSL 六表已有数据、活跃 SSL 作业／租约的保全和空闲判断，不忽略新表。
- [ ] 发布工具先失败测试，验证新 SSL 基线 migrate-only 入口、候选全 API 与原镜像一致、默认配置无变化、SSL 非空表可保全、活跃 SSL 任务阻止发布及失败回退。
- [ ] 干净提交、打包、隔离 Linux 回归和候选镜像、GitHub CI、模拟浏览器验收。重新核实现网版本及业务空闲后建立新配对备份、发布、独立核对页面与全部业务摘要。
- [ ] 更新实际结果与未处理事项、原草稿 PR，不合并主分支。不得执行客户任务或改动客户监控开关。
