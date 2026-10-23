# 宝塔批量上传实现计划

> 按 subagent-driven-development 的实现、规格审查、质量审查流程执行。现有 linked worktree `work/spug-panel-tasks` 和分支 `feature/baota-panel-tasks` 继续使用，不改变主检出。

**目标：** 在面板任务内上传多个本机文件或目录到选定面板，单独目录优先于统一目录，覆盖前确认，逐文件持久记录。

**架构：** 文件先进入数据卷私有暂存区；一份已封存文件包供多个面板使用。每台仍使用现有 PanelTaskTarget 与面板锁，文件进度另存行记录，避免在任务 JSON 中重复存放全部文件。每一步只派发一个文件，先落下发意图再联网，断连或中断只核验，不自动重传。

**技术栈：** Django ORM/MariaDB、requests 签名 multipart 与流式摘要、React 16/Ant Design、现有 worker。

## 1. 私有暂存与路径规则

文件：新增 `spug_api/apps/baota/upload_store.py`、`upload_views.py`、`test_upload_store.py`；扩展 `models.py`、`urls.py`。

- [x] 先写并运行测试：路径 `../a`、绝对源路径、重复及文件/目录前缀冲突被拒绝；单独目录优先，空值继承，单独目录无效不兜底；跨用户引用、数量/容量超限及未封存包拒绝。
- [x] 新模型 `PanelUploadBundle`（owner/state/manifest_sha256/total_bytes/file_count）、`PanelUploadFile`（bundle/relative_path/size/sha256/state）、`PanelUploadBinding`（target/bundle）、`PanelUploadResult`（target/source/state/before/intent/result/resolutions）、`PanelUploadQuota`（全局配额锁）。私有磁盘使用随机标识，权限 700/600。
- [x] 提供 `POST uploads/` 接收 `{files:[{path,size}]}`；`POST uploads/<bundle>/files/<file>/` 接收 multipart `file`；`POST uploads/<bundle>/seal/` 冻结摘要；`POST uploads/<bundle>/discard/` 仅释放未绑定且不在接收中的草稿；权限复用 exec.panel.create。
- [x] 流式落盘并计算 SHA-256；20 MiB/文件、200 MiB/包、1000 文件、1 GiB 全局、400 MiB/用户、512 MiB 空间保留。配额分配持数据库锁；接收用尝试标识，失败保留可重试状态，封存后不能替换。
- [x] 清理未绑定 24 小时草稿和结束 7 天的源内容，运行、待核实、准备执行的绑定不可清理；保留元数据与摘要。文件级结果分页返回。
- [x] 运行 `..\..\spug-testenv\Scripts\python.exe manage.py test apps.baota.test_upload_store --settings=spug.test_settings --noinput`，退出 0 后提交。

目录合并契约：

```python
effective = individual.strip() if isinstance(individual, str) and individual.strip() else common.strip()
# 对 effective 进行规范绝对路径校验；非空 individual 无效时直接报错。
```

## 2. 宝塔文件传输

文件：扩展 `client.py`、`worker.py` 的 StopAwareClient；新增 `test_upload_transport.py`。

- [x] 使用本地 HTTP 夹具先证明现有客户端缺少二进制上传和摘要读回能力，断言签名位于 multipart 体、真实二进制内容和 basename 正确、重定向不发送第二次。
- [x] 添加 `PanelClient.upload_file(directory, filename, stream)`；自定义有界 multipart reader，沿用 TLS 校验、固定指纹、禁用代理/重定向、响应上限和总超时。原普通 request 行为不变。
- [x] 添加 `file_sha256(filename, maximum)`：经已审查的 `/download` 签名只读流计算大小与 SHA-256，不用 GetFileBody，不返回内容。下载超限、HTML、超时不能成功。
- [x] StopAwareClient 在这两个方法前检查停止事件；传输发送阶段也使用剩余 deadline。
- [x] 针对性测试与现有 HTTP 集成测试退出 0 后提交。

## 3. 上传任务与持久化调度

文件：新增 `operations/uploads.py`、`upload_runner.py`、`test_upload_tasks.py`；扩展 `operations/__init__.py`、`task_service.py`、`task_views.py`、`worker.py`。

- [x] 先写失败用例：未确认不能开始、统一/单独目录映射、只读预检零上传、真实意图先落盘、取消只影响未发项、超时不重发、重启不重发、内容不符不成功。
- [x] 注册操作 `file_upload`，参数为 `{bundle_id,target_dir}`。首次请求支持公共 `params` 和部分 `params_by_panel` 目录覆盖；服务端合并并冻结最终映射。重试接受已冻结的逐台参数，但重新检查包归属和有效性。
- [x] 绑定文件包与目标，预检检查版本、所有父目录与目标类型，拒绝已知链接或类型冲突；使用实际 `upload_files_exists` 返回结构记录原大小/时间。每台只返回一个摘要 child，文件行独立保存。
- [x] worker 对 file_upload 使用专用逐文件处理器，共用现有 task→target 锁序、PanelLock 和 2 面板并行上限。上传前核对 API revision、确认项、冻结路径、源文件摘要及当前目标状态；上传后大小和 SHA-256 读回一致才成功。
- [x] 发生投递歧义保存 uncertain 并持有锁。人工只读核验不能重发；管理员可逐文件提供原因/依据判定，已有通用任务不改变。恢复中断时仅 dispatching 文件转 uncertain，未派发文件保持未发送。
- [x] `upload_overwrite` 加入明确确认项；取消、重试、人工判定和审计与文件状态同步；定期私有暂存清理不联网。
- [x] 全量 `manage.py test apps.baota --settings=spug.test_settings --noinput`，SQLite 专项跳过项另以现有隔离 MariaDB 工作流验证。

## 4. 简洁上传表单和结果

文件：新增 `spug_web/src/pages/exec/panel/UploadForm.js`、`UploadResults.js` 及对应测试；扩展 `TaskForm.js`、`TaskDetail.js`、`helpers.js`。

- [x] 先测目录优先级及表单提交：统一 `/common`、A 单独 `/site-a`、B 留空，服务端请求完整保留两层设置；无最终路径、非绝对单独路径和文件超限不能提交。
- [x] 新操作展示多个文件或目录选择、数量/大小、统一目录和逐台覆盖输入；逐台显示最终目录及来源。目录保留根目录和内部层级。暂存、封存后生成预检；期间禁用改变面板、操作和文件的控件。
- [x] 结果默认展示目录、上传进度、非零状态数量。文件明细默认折叠且分页，异常消息和逐文件人工判定保留；不默认显示摘要、时间戳等技术记录。生成新预检与确认开始保持分离。
- [x] 前端模块测试、lint、生产构建通过后提交。构建仅本机执行，不在低配置生产机编译前端。

## 5. 整体验证、部署与 GitHub 记录

文件：更新部署记录与用户说明，发布脚本/一次性验证材料放本地 work，截图放 outputs。

- [x] 规格审查后再质量审查，修复全部 Critical/Important；验证真实上传协议使用回环/隔离目标，不向客户网站写开发测试文件。
- [x] 构建正式镜像并运行现有 3 项离线镜像检查及新模块测试。检查生产未结束任务与锁；必要时等待现有任务完成，不清锁、不删除或重发。
- [x] 数据库、完整卷/密钥、Compose 和环境成对备份并校验，再应用新模型、切换镜像，核对全部历史数据/密钥摘要、8 个服务、HTTP 200、healthy。新表不能被旧 updatedb 自动删除，回退需保留新模型与只 migrate 的兼容入口。
- [x] 浏览器验证公共目录、逐台优先、文件／目录选择并保存截图；覆盖确认和紧凑文件明细由隔离测试验证。对既有客户不提交测试上传任务。
- [x] 提交推送 GitHub，更新 PR2 说明、部署版本与限制，不合并 PR，最终给用户入口及实际验证结果。

## 完成记录

2026-10-23 已按计划完成并部署实际代码 2350559；最新证据见 deploy/baota/DEPLOYMENT-PANEL-TASKS.md。完整后端发现使用显式 test*.py 模块列表，兼容 apps 命名空间；CI 单独执行 MariaDB 专项。浏览器只验证文件／目录选择与路径规则，覆盖确认及逐文件执行由隔离测试覆盖，未向客户网站写测试文件。发布前数据副本完成新／回退／新演练，正式历史及密钥摘要一致；PR2 更新且不合并。
