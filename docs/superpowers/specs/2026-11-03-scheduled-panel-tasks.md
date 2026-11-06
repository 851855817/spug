# 任务计划执行宝塔任务

用户已确认：到点重新预检，通过后自动执行。新计划默认关闭，不激活任何客户计划作为验收。

## 用户流程

任务计划的任务内容新增「宝塔面板任务」。可以在原有宝塔表单创建预检，或选择已有预检；引用服务端保存的目标、操作和参数，不再次输入 API。展示固定面板、网站或软件与影响确认项，再设置一次性、间隔或 Cron。保存为未激活，激活时明确确认自动执行。宝塔计划不支持监控告警触发，间隔至少 300 秒；Cron 每次实际下发至少间隔 300 秒，密集触发只记录跳过，不补跑。

运行历史保留状态、具体原因和逐台结果，并能打开关联面板任务。面板计划的「重新预检」只读，不沿用原来 Shell 的立即执行测试语义。普通 Shell/Python 功能保持兼容。

## 存储与授权

不修改表结构。Task.interpreter=baota，command 保存版本化服务端定义；History.output 保存类型标记、运行阶段、原因和本次 PanelTask ID。前端不能提交这些服务端快照。API 返回单独的 panel_plan，可编辑字段仅 template_id、template_token、acknowledgements；command 返回空串。模板 token 绑定已保存的面板、API revision、参数和预检身份。

Task.targets 保持空列表，避免与原有 SSH 主机 ID 命名空间混用；面板目标只保存在类型化定义和 panel_plan.panels。操作参数中的目标软件版本、PHP 预期补丁固定；每次更新已安装事实，新的预期版本需要编辑计划。最短间隔从上次运行结束保守计算，覆盖慢速发送前核对与结果读回耗时。

所有计划读写仍须原有 schedule 权限；宝塔定义、模板与结果还须 panel.view，创建须 panel.create；激活须 panel.execute/create/view 和 schedule.edit，每项逐个检查（User.has_perms 是 OR 语义）。仅创建者或 panel.manage 可见宝塔计划。激活者作为执行授权账号，账号停用、权限撤销、计划禁用或修改后不得再下发新变更。关联面板任务归该授权账号，仍按原有任务可见性控制。

新建、修改与激活校验服务端模板及 API revision。只允许修改已停止且没有未结束运行的计划。禁止把 baota 定义当成 Shell 执行，含旧调度消息、旧工作消息和直接调用。

## 固定范围与每次预检

面板组在配置时展开并固定，后加成员不自动纳入。网站 all_sites 转为模板中具体 ID；记录网站稳定身份（ID、名称、目录、类型），新建网站的域名与目录按原参数固定。每次预检比较固定对象身份及 API 配置；不永久冻结旧证书到期值、软件当前版本或 PHP 当前补丁。未知确认项、目标变化、失败或不确定预检均停止。每次产生新的 PanelTask；只有全量预检通过才调用原有 start，使用本次新快照和已经明确授权的确认项。已经满足的项目记为正常，无需重复变更。

上传计划复用封存文件包，仍受原配额约束。存在计划引用时（含未激活计划）保留源文件，删除计划后按原清理规则释放；源文件不可用则停止。所有已支持操作均可通过模板选择，不接入任意 URL、API 或命令。

## 调度与恢复

回调只按 Task ID 重新读取定义。事务锁 Task 串行建立运行与 PanelTask 链接；同计划上次运行未结束不再创建变更，跳过触发不会覆盖在途运行。面板 worker 的本地协调器推进预检、开始与历史同步，避免占用 SSH 工作线程等待。每个变更阶段仍使用原 worker 持久化 intent、面板锁和读回逻辑。额外在发送前重新核对计划及授权；禁用后允许已有请求读回，阻止后续发送。

失败或 uncertain 自动停用计划，保留逐项事实和原面板锁；不自动重试、不重发、不自动接管不确定任务。关闭计划不把已受理请求标成取消成功。worker 重启可恢复已链接运行；不补发错过时点。协调器操作幂等，任务创建与链接在同一数据库事务提交。每轮只处理有界数量本地记录，不连接客户接口。

接受变更的边界是已持久化的发送意图。发送意图按 schedule → PanelTask 锁顺序与禁用串行；实际变更请求发送前还会重新检查授权。禁用不能撤回已接受或已发送的请求。普通面板「开始」接口拒绝计划关联任务，只有协调器完成范围核对并保存 running 阶段后才允许发送。通知独立于面板协调器：原通知渠道有单独调度工作，先登记尝试再发送，不因超时自动重试。

## API 约定

- GET /api/schedule/panel_templates/：items 模板摘要；GET ?id=N：单个已就绪模板，含 template_id、template_token、operation、operation_label、panels（id/name）、scope（每面板的目标文字）、required_acknowledgements（code/label）和 acknowledgements。
- POST /api/schedule/：interpreter=baota 时提交 panel_plan 三字段，其余 name/type/trigger/trigger_args/desc/rst_notify 沿用；targets、command 由服务端生成。返回原有空成功响应。
- GET /api/schedule/：宝塔行 command=''，panel_plan 包含上述摘要；敏感内部定义不返回。
- PATCH /api/schedule/：激活宝塔计划额外 confirm_panel=true；保存时已确认的 acknowledgement 必须仍覆盖本次要求。
- POST /api/schedule/N/：宝塔计划只创建新的只读预检，返回 {panel_task_id, mode:'preflight'}。
- GET /api/schedule/N/?id=H：原结果结构增加 kind='baota'、panel_task_id、phase、message、status；outputs 逐面板包含 name/state/output。只接受属于该计划的历史 ID。

## 验收和发布

隔离 SQLite/MariaDB 与模拟面板覆盖授权、全量预检、范围固定、并发重入、禁用/撤权、失败停用、重启、上传保留、结果归属和 Shell 兼容。浏览器模拟确认入口、激活确认、只读预检和历史详情。新增独立且精确的发布 profile，重新核对现网版本、空闲、全量保全摘要、配对备份及可回退性后才可发布；无法安全发布则保持正常线上版本并说明。
