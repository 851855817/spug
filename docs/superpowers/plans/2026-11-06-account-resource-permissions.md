# 账号资源权限与服务器数量配额实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 逐任务实现；每段先运行失败测试，再实现并验证，进行规格和代码审查。复用现有 linked worktree，不合并主分支。

**目标：** 普通账号只在明确授权资源内操作，所有服务器入口遵守统一数量上限，确认关联的 SSH 与宝塔合并计数。

**架构：** 在 account app 增加附加策略、归属、授权和审计表，不覆盖既有 created_by 审计。共同的 access 模块提供新鲜账号权限、资源查询与并发配额事务；各领域在查询和远程下发边界调用它。所有页面统一使用相同后端范围。

**技术栈：** Django 4.2 / MariaDB，React 16 / Ant Design 4，Django TestCase / TransactionTestCase，Jest。

**状态：** 用户已批准规格；功能实现、两阶段审查及隔离完整测试已通过，精确发布准备进行中，尚未上线。规格：`docs/superpowers/specs/2026-11-06-account-resource-permissions-design.md`。

## 文件与共同接口

- 新建 `spug_api/apps/account/resource_models.py`：AccountResourcePolicy、ResourceOwnership、ResourceGrant、ResourceGroupOwner、ResourceAccessAudit，导入 account/models.py 注册；仅增加新表。
- 新建 `spug_api/apps/account/access.py`：新鲜授权与范围，`current_user(user, lock=False)`、`require(user, *codes, lock=False)`（所有 code 必须满足）、`scope(user, kind, manage=False)`（host/panel QuerySet）、`allowed_ids(user, kind, manage=False)`、`require_resources(user, kind, ids, manage=False)`（任一无权整批拒绝）、`group_ids(user, kind, manage=False)`、`require_groups(user, kind, ids, manage=False)`、`own_group(user, kind, id)`、`quota_usage(user)`、`quota_guard(user_ids)`（事务并发锁与增量校验）、`set_policy(admin, user, limit)`、`assign(admin, user, kind, ids, level)`、`transfer(admin, kind, id, user)`。
- owner override 独立于 created_by；无 override 时按原 created_by 认定。授权不沿关联扩散。数量在可见 host / panel 集合中按明确 linked_host 去重，0 不许新增，None 显式不限。
- 新建 `spug_api/apps/account/resource_views.py`：管理员策略/分配/转移/影响清单入口；普通账号只读自身生效权限与配额。修改 account/views.py、urls.py 加入字段与入口。
- 新建 `spug_api/apps/account/test_resources.py`、`test_resource_endpoints.py`：跨账号、并发、动作、影响清单测试。
- 修改 `spug_api/apps/host/{views,group,add,extend,utils}.py`、`apps/exec/{views,executors,transfer}.py`、`apps/file/{views,utils}.py`、`consumer/{consumers,utils}.py`：主机和全部 SSH 入口。
- 修改 `spug_api/apps/baota/{views,connection_views,login_views,task_service,task_views,worker}.py`、`apps/schedule/{views,panel_tasks,utils}.py`：面板与后台任务。
- 修改 `spug_api/apps/baota/resource_monitor/service.py`、`apps/baota/inventory/service.py`、`apps/ops/{service,views,worker}.py`、`apps/ops/expiry/{assets,service,reminders}.py`、`apps/monitor/ssl/{service,renewal,jobs,probe_worker}.py`、`apps/home/views.py`、`apps/notify/views.py`：跨页面、采集、通知。
- 修改 `spug_api/apps/{app,deploy,pipeline,monitor}/` 涉主机的入口与 worker：保留原应用/环境权限并叠加资源边界。
- 修改 `spug_web/src/pages/system/account/`、`system/role/{codes,presets}.js`、各领域菜单/动作权限：配额与授权表单、最终权限预览、剩余额度、细分权限。
- 新建 `docs/account-resource-permissions-guide.md` 与附加迁移/保全/回退说明；最终发布另建精确合同，不能放宽旧 SSL 合同。

## 任务 1：统一策略、归属、分配与配额

- [x] 写 `test_resources.py`，创建 A/B/admin 与各自主机、面板；验证 A 不可读取 B，read grant 不可管理，失活 superuser 拒绝，已关联同台净计数，未关联同 IP 不合并。

```python
def test_link_does_not_grant_access(self):
    Preference.objects.create(panel=self.other_panel, linked_host=self.own_host)
    self.assertNotIn(self.other_panel.pk, allowed_ids(self.a, 'panel'))
    self.assertEqual(quota_usage(self.a)['used'], 1)
```

- [x] 运行下方 `apps.account.test_resources` 的绝对路径测试命令，确认因策略尚不存在失败。
- [x] 建立上述附加模型与共同接口。scope 先确认活跃、非删除账号；权限每次从 DB 与角色读取，不能信任旧对象。只有管理员能分配、改归属、设置不限；拒绝审计不保存请求正文或秘密。

```python
with quota_guard([user.pk]):
    host = Host.objects.create(created_by=user, **validated_values)
```

- [x] quota_guard 先按账号 ID 排序 select_for_update User 行，记录 before，用事务内 after 检查；after 超限且净增加时回滚。关联、转移和分配锁住所有受影响账号。不得跨网络请求持有配额锁。
- [x] 测试绿色，提交 `feat(auth): add resource ownership and unified quota policy`。

## 任务 2：主机、分组、导入导出、SSH 与文件

- [x] 增加真实 API 测试：A edit/delete/verify B 主机、伪造组 ID、导入超额、global key 借用、下载 token、终端撤权均拒绝。
- [x] 运行该测试确认现状越权，再接入 require_resources / require_groups 与细分权限。

```python
require(request.user, 'host.host.edit')
require_resources(request.user, 'host', [form.id], manage=True)
```

- [x] 主机导入先解析完整合法记录并在 quota_guard 内整批创建；普通账号不得默认借全局 key，显式密码验证可建立独立凭据。组读只返本人组/有权资源组和必要祖先，组写仅本人组或管理员。
- [x] SSH 命令/文件与 WebSocket 在握手、每次入站动作、每次输出及保活时重读权限；不信任前端主机 ID、已有文件票据或队列历史。
- [x] 运行领域回归、审查、提交 `feat(auth): scope host SSH and file operations to account resources`。

## 任务 3：宝塔、动作授权、任务与计划

- [x] 测试 A 列表/连接测试/登录 B 被拒；任务混合目标整体拒绝；查看与 execute 不附带升级、卸载；排队后撤权不发客户端请求。
- [x] 接入面板 CRUD quota_guard，组归属和操作字典 `exec.panel_action.<operation>`；task_service selected_panels 接收 user；visible/owned 在序列化前核对全部目标。

```python
require(user, 'exec.panel.execute', 'exec.panel_action.' + task.operation)
require_resources(user, 'panel', list(task.targets.values_list('panel_id', flat=True)), manage=True)
```

- [x] Worker 客户端代理在每次只读/写请求前重新校验任务 owner、动作和全部目标；计划创建、启用、tick、before_mutation 同规则。上传绑定和重试继承相同约束。
- [x] 测试绿色、领域回归、审查后提交 `feat(auth): authorize panel actions and scheduled dispatch at runtime`。

## 任务 4：监控、SSL、库存、运维、到期与首页

- [x] 新增各领域 A/B 的列表、统计、筛选、采集、续签、结果和通知测试，先运行确认现存仅管理员或全局范围不符合。
- [x] 将硬编码 superuser gate 改为具体功能权限与来源范围；后台源持有者仍须在运行时有权。SSL source.owner 不是资产 owner；不得借此自动移交资源。

```python
hosts = scope(user, 'host')
panels = scope(user, 'panel')
```

- [x] 所有快照、证书、提醒及首页统计从已限定来源生成；关联只用于可见来源去重，不暴露不可见另一侧。对他人的全局来源开关仅管理授权可写。个人隐藏/静音保持独立。
- [x] 运维 run 只展示本人且全部目标有权，worker 每次 SSH 前核对；到期 metadata 跟进负责人不决定资产权限。
- [x] 领域回归、审查、提交 `feat(auth): isolate monitoring SSL inventory and operational results`。

## 任务 5：应用发布、流水线、旧监控与所有遗留入口

- [x] 搜索所有 `Host.objects`、`Panel.objects`、`has_host_perm`、`get_host_perms`、`is_supper` 与所有原始目标 JSON，建立调用审计清单。
- [x] 旧应用/环境授权与资源授权同时满足；历史混合任务整体拒绝、统计先限定；模板仅本人或管理员公共只读；凭证仅所属/显式授权可用。
- [x] API、下载、票据、worker、计划和终端相互调用同一策略，不新增绕过路径。无后台账号的远程调用必须从具体任务来源解析 actor，不能虚构管理员。
- [x] 完整后端测试、独立规格审查与质量审查，提交 `fix(auth): close remaining cross-account server entry points`。

## 任务 6：账号表单、细分角色与生效权限预览

- [x] Jest 行为测试：0/限额/不限值正确提交；编辑不清空分配；权限树包含独立升级、续签、SSH、监控与 SSL 权限；只读共享不能显示写按钮。
- [x] 账号表单显示已用/上限与只读/管理资源授权；角色沿用五类岗位模板且显式最小授权。普通账号看到当前剩余额度。
- [x] UI 读取后端 effective scope；不能把前端禁用按钮当安全校验。
- [x] 运行全部 Jest 和 build，提交 `feat(ui): expose account quotas resource grants and granular permissions`。

## 任务 7：旧数据影响、附加迁移与 MariaDB 并发验收

- [x] 管理命令默认只读输出账号 ID、既有创建者、组授权、确认关联、净计数、计划/在途任务影响，不含凭据。显式 apply 仅在精确发布合同中调用。
- [x] 旧普通账号保留明确权限，不自动共享全站宝塔；建立初始额度为净已用数，保留 created_by；附加表与旧 app 可兼容回退，不能移除审计。
- [x] 隔离 MariaDB TransactionTestCase 用两个独立连接同时争最后 1 名额，断言只能创建 1 条，另一条明确 quota error。测分配/解绑/转移的多账号锁与事务回滚。
- [x] 校验全部权限矩阵并写入使用文档与进度，提交 `test(auth): verify migration impact and concurrent server quotas`。

## 任务 8：完整验收、PR 与发布

- [x] 全套后端、前端、build 与部署保护；使用模拟客户接口的 A/B/admin 浏览器验证，禁止真实升级、命令、续签、主动采集或开关来源。
- [ ] 独立审查两阶段通过；Git 精确提交按顺序推送，更新既有草稿 PR #4，不合并。
- [ ] 只读重新核实现网 SHA、在途业务、数据库模式/密钥/数据摘要；新建配对备份和明确回退；新发布 profile 只允许本功能准确变化，隔离迁移和回退演练。
- [ ] 只有全部成功且业务空闲才按已授权发布；否则保持正常线上并明确未上线状态。发布后复核账号/角色/来源开关/计划和服务，不能真实触发客户任务验证。

任务 3、5、7 的后端最终组合保存为 `1a76760`，前端为 `51caf11`。测试、审查、浏览器和迁移实演的数量与限制见对应进度记录；上面的开发步骤已完成不表示生产迁移或发布已执行。

## 本地测试命令

PowerShell，在 `spug_api` 目录使用：

```powershell
& 'C:/Users/K/Documents/Codex/2026-10-22/mu-q/work/spug-testenv/Scripts/python.exe' -B manage.py test apps.account.test_resources --settings=spug.test_settings --noinput
$testModules = @(rg --files apps libs consumer | Where-Object { [System.IO.Path]::GetFileName($_) -match '^tests?.*[.]py$' -and $_ -notmatch 'settings|test_urls' } | ForEach-Object { $_.Replace([char]92,[char]46).Replace([char]47,[char]46).Substring(0, $_.Length - 3) })
if ($testModules.Count -lt 40) { throw 'Test discovery incomplete' }
& 'C:/Users/K/Documents/Codex/2026-10-22/mu-q/work/spug-testenv/Scripts/python.exe' -B manage.py test @testModules --settings=spug.test_settings --noinput
```

前端在 `spug_web`：

```powershell
$env:CI='true'
& 'D:/nodejs/node.exe' node_modules/react-app-rewired/bin/index.js test --watchAll=false --runInBand
$env:NODE_OPTIONS='--openssl-legacy-provider'
$env:GENERATE_SOURCEMAP='false'
& 'D:/nodejs/node.exe' node_modules/react-app-rewired/bin/index.js build
```

测试不得读取生产数据库。最终记录实际命令、数量、跳过原因和证据，不能复用旧 SSL 功能的测试结论。
