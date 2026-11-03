# 任务计划执行宝塔任务实现计划

> 面向 AI 工作者：按 executing-plans 和 test-driven-development 执行；完成后独立审查。

**目标：** 任务计划配置宝塔操作，到点全量重新预检，成功后自动执行，集中查看结果。

**架构：** 沿用 Task/History 表，增加版本化定义与面板 worker 协调器。复用现有 PanelTask/面板锁/操作适配器，所有定时调度均从数据库重新取授权和固定范围。

**技术栈：** Django、APScheduler、MariaDB、React、MobX、Ant Design。

对应规格：`docs/superpowers/specs/2026-11-03-scheduled-panel-tasks.md`。

## 文件与步骤

- [x] 1. 新建 `spug_api/apps/schedule/test_panel_tasks.py`：先验证未经授权不可保存、全量预检失败不得 dispatch、重复触发不重复创建；命令 `../spug-testenv/Scripts/python.exe manage.py test apps.schedule.test_panel_tasks --settings=spug.test_settings`（在 spug_api，解释器路径相应加一层）。先确认因接口缺失失败。
- [x] 2. 新建 `spug_api/apps/schedule/panel_tasks.py`：定义/模板/授权/运行协调；修改 schedule views/models/scheduler/executors/urls 接入；修改 baota worker/upload_runner 的发送授权和 upload_store 的引用保留。逐项运行上面的隔离测试和 apps.baota 现有回归。
- [x] 3. 新建前端 `pages/schedule/PanelPlan.js` 与测试；修改 Step1/Step2/Step3/Form/store/Table/Info 接入面板任务配置、激活确认、重新预检、详情。Jest 验证固定范围、禁止缺失确认、不会把面板结果渲染成 SSH 成功。
- [x] 4. 独立规格与代码审查，修复后跑前后端完整相关回归；使用模拟接口浏览器验收，不访问客户接口。
- [x] 5. 新增精确发布 profile 与其测试，构建并核验 source/image，更新使用文档、Git 和 PR #4。线上检查和配对备份完成后安全发布并验证；不合并主分支。

## 执行记录

开始：用户已确认自动执行语义。现网基线 567eb5a 只是初始事实，发布前必须重新核实。未开启或执行任何客户计划。


实现检查点：后端本地完整回归 578 项通过、跳过 11 项；之后补充重启和生产锁语义回归，任务计划专项 34 项通过（SQLite 跳过 1 项 MariaDB 并发测试）。前端 44 套件 434 项通过、限定目录 ESLint 无警告、生产构建通过。发布工具 185 项通过（Windows 跳过 1 项 Linux 锁测试），expiry 10 项通过；独立审查再次运行 12 项新 profile 合约通过。原有 0.05/0.15 秒 transport 测试曾在 Windows 失败，隔离复跑及后续完整回归通过，未改该代码或放宽断言。

独立审查修复并验证：手工开始接口不能绕过协调器；阶段/账号/权限/发送时点重新授权；schedule→PanelTask 锁顺序；旧 monitor 动态 Host 目标兼容；面板 ID 不混入 Host targets；上传目录来源不参与范围身份、持久文件引用、源账号限制；SSL 跳过网站域名固定；异常数据按计划隔离；通知独立 claim-once；历史跳过记录保留 latest；冷却时间覆盖运行完成时刻。发送意图后重启保留 uncertain 和面板锁、不自动重发。

现网只读预检：仍为 567eb5a，业务空闲、63 项保全摘要可解密，porcelain 配色及 revision 未变。尚未发布；真实浏览器模拟验收与最终归档继续进行。不执行任何客户任务，不激活计划验收。

浏览器验收已完成并记录在 `docs/releases/2026-11-03-scheduled-panel-tasks-browser.json`：真实隔离 Chrome、17 项断言、18 个模拟 API 请求，无真实客户调用或执行入口调用。功能提交 35fb559 与发布文档提交 f62d1dd 的 push/PR 前端、后端、MariaDB CI 全部通过。f62d1dd 的 1062 个公开源码文件逐字节一致。

首次服务器准备未通过，尚未发布：镜像及 581 项隔离后端回归通过（10 项条件跳过），但 185 项 Linux 发布保护有一项读取 CI 配置失败。根因是新增的发布测试依赖 `.github/workflows/baota-checks.yml`，打包源文件清单尚未包含它。已补充按真实 pathspec 过滤的 CLI/归档字节回归，在旧代码重现缺失，再精确加入该文件；不修改已校验暂存包，不跳过测试，重新提交、归档和完整隔离准备。

最终发布：48c305d，push/PR CI 三项均绿，公开 1062 文件一致，17 项最终构建浏览器断言通过。服务器新归档完整运行 581 后端（10 跳过）、185 Linux 发布保护、10 旧发布保护及 6 镜像测试通过。重新核实 567eb5a 空闲后，配对备份并受控上线。独立确认 63 保全对象、原进程、11 精确 API 覆盖及两份完整树、API/UI 收据、9 服务与 15 健康项；公网、回环、本机隧道均为 48c305d，JS/CSS 与发布构建一致。未激活或执行客户计划。详见 `docs/releases/2026-11-03-scheduled-panel-tasks.md` 及相邻验证 JSON。
