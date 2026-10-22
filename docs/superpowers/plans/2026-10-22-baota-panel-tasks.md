# 宝塔批量面板任务实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 逐任务实现；实现者串行工作，任务后分别进行规格和质量审查。

**目标：** 完成用户确认的八类 Linux 宝塔批量操作、API 接入、逐对象记录和可恢复后台执行，并部署到现有 Spug。

**架构：** `apps.baota` 内分别提供连接客户端、资源读取、操作适配器、数据库队列及 HTTP 视图。前端新增 `/exec/panel`，复用现有分组面板及权限。变更先预检形成固定快照，用户提交后专用 worker 执行；未知投递状态保留面板锁，禁止重发。

**技术栈：** Django 4.2、requests、cryptography、MariaDB、React、Ant Design、Docker/Supervisor。

## 工作区和验证命令

工作区：`work/spug-panel-tasks`，分支 `feature/baota-panel-tasks`。原开发检出保留在 `work/spug`。

后端在项目根目录执行：`../spug-testenv/Scripts/python.exe spug_api/manage.py test apps.baota.tests apps.baota.test_connection apps.baota.test_operations apps.baota.test_tasks --settings=spug.test_settings --noinput`。测试使用 SQLite 内存库，不加载生产覆盖配置。Windows 与生产 Linux 各验证一次。

前端在 `spug_web` 执行：`node node_modules/react-app-rewired/bin/index.js test --watch=false --runInBand --runTestsByPath src/pages/baota/helpers.test.js src/pages/exec/panel/helpers.test.js`，环境 CI=true。

生产构建：环境 NODE_OPTIONS=--openssl-legacy-provider、GENERATE_SOURCEMAP=false，运行 `node node_modules/react-app-rewired/bin/index.js build`。

## 文件边界

- `apps/baota/client.py`：签名、限流超时、TLS 信任、无重定向、受限响应解析。
- `apps/baota/resources.py`：读取每台面板的网站、软件、PHP 扩展、版本及证书元信息。
- `apps/baota/operations/{base,sites,software,certificates,upgrade}.py`：参数校验、预检、下发、读回及受控结果。
- `apps/baota/models.py`：API 配置、任务、逐面板/对象状态、持久面板锁。
- `apps/baota/task_service.py`、`worker.py`：任务预检/提交/取消、DB 领取和恢复。
- `apps/baota/task_views.py`、`connection_views.py`：权限、所有者限制、错误封装。
- `apps/baota/management/commands/runpanelworker.py`：独立后台进程。
- `spug_web/src/pages/exec/panel/{index,TaskForm,OperationForm,TaskDetail,helpers}.js`：操作目录、分组选择、逐对象参数、预览及日志。
- `deploy/baota/`：固定提交构建及 Supervisor 启动。

## 任务 1：API 接入和连接客户端

- [x] 先添加 `test_connection.py`，验证双 MD5、URL 安全入口分离、重定向拒绝、响应大小上限、密钥加密和不返回、留空编辑保留密钥、配置变化使连接失效、连接权限、TLS 指纹拒绝不匹配。

```python
record = self.panel(api_base_url='https://example.com:8888', api_key='test-key')
self.assertTrue(record['has_api_key'])
self.assertNotIn('api_key_ciphertext', record)
self.assertEqual(Panel.objects.get(pk=record['id']).get_api_key(), 'test-key')
```

- [x] 运行新增测试，确认因缺少功能失败；保留红灯证据。
- [x] 在 models.py 添加 API 地址、加密密钥、TLS CA/指纹、连接时间与版本、配置修订号；现有记录默认未配置。PanelView 保存前校验，配置变化清除最后连接结果。
- [x] 实现 `PanelClient.request(path, action=None, params=None, mutating=False, timeout=None)`；固定同源路径、签名、无重定向、大小限制和安全错误。`PanelClient.close()` 释放会话及临时 CA 文件。支持系统信任、专用 CA 和固定 SHA-256 证书指纹。
- [x] 实现 `/baota/connection/` 保存配置后的连接检查和 `/baota/certificate/` 无凭据证书检查；连接测试仅读系统信息。前端在面板表单中配置 API 并在列表测试已保存配置。
- [x] 运行新增和既有后端测试、前端相关测试，审查后提交 `feat: add encrypted Baota API connections`。

## 任务 2：资源读取及八类操作适配器

- [x] `test_operations.py` 先验证 9 个操作码、严格参数校验、跨面板网站 ID 重新解析、同版本已符合、SSL pending 不成功、伪静态失败恢复、软件队列关联、未知版本拒绝变更及禁止旧 UpdatePanel 预检。

```python
result = operation.reconcile(client, frozen_target, remote_result)
self.assertNotEqual(result['state'], 'success')  # pending SSL order
```

- [x] 定义统一协议：`validate(params)` 返回净化参数；`preflight(client, params)` 返回 `{items, summary}`；每项有稳定目标、before 和执行计划；`execute(client, item)` 完成下发及读回，或返回可监控的 remote 元数据；`reconcile(client, item, remote)` 仅查询核实。结果仅含允许字段。
- [x] 操作码为 `panel_upgrade`、`site_add`、`site_basedir`、`site_rewrite`、`ssl_apply`、`ssl_renew`、`software_install`、`php_extension_install`、`software_uninstall`；SSL 两个子操作归一类，页面显示八类。
- [x] 实现 resources.py 查询网站、软件商店、PHP 分支/扩展、模板、SSL 及升级选项；适配 11.7.0/13.1.0 的已核对响应结构。内部软件标识取服务端列表后再校验，不允许任意脚本参数。
- [x] 分文件实现操作，以研究文档的官方源码为依据；软件异步队列与 task_list 分开，原始日志不透传。配置失败报告逐网站结果，核对面板自身回滚；不自动恢复或重复加载。证书采用 HTTP 文件验证、签发/续签、部署、读回完整流程。
- [x] 测试异常投递、配置不适用和前后状态，再提交 `feat: add versioned Baota operation adapters`。

## 任务 3：持久任务、API 和 worker

- [ ] `test_tasks.py` 先验证目标去重/快照、权限/所有者、预检后参数不能修改、配置修订失效、并发领取、同面板串行、取消只针对未下发、超时待核实和重启不重发。

```python
worker.recover_interrupted()
self.assertEqual(target.refresh_from_db() or target.state, 'uncertain')
self.assertTrue(PanelLock.objects.filter(panel=target.panel).exists())
```

- [ ] models.py 添加 PanelTask、PanelTaskTarget、PanelLock；任务固定参数及目标快照，面板外键 PROTECT。目标 JSON 保存逐网站/扩展结果及远端关联，持久锁由事务与唯一约束保证。
- [ ] task_service.py 提供 prepare、start、cancel、reconcile、resolve；读取目录及资源路由分别检查权限。普通用户只访问自有任务；manage 可处理全部任务。管理员解除待核实锁必须记录理由及核实结果。

预检也交给专用 worker，在 prepare 时保存校验后的参数和去重面板清单并立即返回任务 ID，避免多台面板依次请求超过 HTTP 超时。页面轮询到固定预览后才开放 start；准备或预检完成本身均不授权下发。重启可以重做未完成的只读预检，但不能重发已保存变更意图的子项。

SSL 的第一次 ACME 请求自身可能同步整个面板内相同域名集合的证书，包含未选网站、非网站证书目录和面板 TLS，并重载 Web 服务。预览固定该实际范围；start 允许额外提交所要求的确认代码（不允许修改参数或目标），确认必须保存后才可写下发意图。后续所选网站的部署仅在尚未自动部署时单独下发。已保存伪静态文件不等于证实 Web 服务成功加载；接口不能核实加载时保留待核实结果及锁，由管理员凭实际检查结果处理。
- [ ] DB worker 默认 2 个线程，每线程单独连接；网络调用不占用数据库事务。领取排队面板时先抢持久锁，标记 dispatching 后才发送；进程恢复时该状态直接转待核实，查询结果后再释放锁。
- [ ] runpanelworker 命令使用单进程锁防止重复启动；轮询数据库，关闭时停止领取，不能删除队列；超时不重复发送变更。
- [ ] 测试通过、分两阶段审查后提交 `feat: persist and execute Baota panel tasks`。

## 任务 4：批量任务页面

- [ ] `helpers.test.js` 先验证分组递归去重、面板/网站映射、粘贴建站表格、SSL/版本参数、结果汇总，覆盖不合法域名及错配面板。
- [ ] TaskForm 依次选择面板和操作，OperationForm 提供八类可理解的参数；网站逐面板选择或展开全部；建站提供结构化网格和批量粘贴，不要求用户写 API JSON。
- [ ] 资源由后端接口读取；未知版本、未配密钥和依赖错误展示到目标行。prepare 后显示固定预览；start 请求只提交任务 ID，防止界面修改越过预检。
- [ ] index 提供记录、过滤、搜索目录；TaskDetail 轮询安全摘要和逐对象结果，提供未下发取消、只读核实与有权限的管理员处理。
- [ ] routes.js 增加 `/exec/panel` 及父级权限；codes.js 增加 view/create/execute/cancel/manage。前端测试和生产构建通过后审查并提交 `feat: add batch panel task workflow`。

## 任务 5：Linux 集成和隔离验证

- [ ] 在 GitHub Actions 的隔离 Linux 环境运行后端测试；测试使用内存数据库且不连接生产服务。生产服务器上的两个宝塔实验容器保持停止，不再运行软件编译实验。
- [ ] 为新 worker 添加 Supervisor 配置，在构建镜像内注入，确认入口 rsync 后仍从持久应用路径运行。
- [ ] 用受控模拟面板进行真实 HTTP 集成：八类请求、断连、不确定状态、worker 重启、TLS 和脱敏；隔离的宝塔版本容器核对真实资源及可安全执行的变更。证书验证环境与客户域名隔离。
- [ ] 真实面板不具备可核实条件的版本不标为已实测；记录每项证据和限制。修复审查问题后重复相应测试。

## 任务 6：GitHub、部署与交付

- [ ] 完整检查变更、测试、构建和敏感信息；创建 Fork 内 PR 并附到当前任务，不自动合并原 PR。
- [ ] 对生产数据库、数据卷、加密密钥和部署配置做一致性备份；构建固定提交镜像，先隔离启动验证 migration、worker、现有面板记录和前端。
- [ ] 切换生产镜像；确认原客户记录完整，API 密钥默认未配、没有自动运行的客户任务。界面验证菜单、配置、预检错误提示及执行记录。
- [ ] 记录镜像、提交、测试和回滚方法，推送部署标签；保存界面截图和简明操作说明到 outputs。
