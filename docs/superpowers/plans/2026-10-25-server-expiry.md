# 服务器到期管理实现计划

> 面向 AI 代理：使用 subagent-driven-development 执行职责明确的任务，并在规格审查后进行质量审查。用户已授权实现和部署，执行方式不再重复确认。

**目标：** 提供可用的统一资产到期管理和默认关闭、每日一次的管理员专属站内提醒。

**架构：** 新模块 `apps.ops.expiry` 解析 SSH／宝塔及已确认关联，使用附加表保存人工日期、负责人、审计、提醒和个人已读状态。API 只读本地数据，scheduler 每天 09:00 执行数据库检查；独立前端入口复用可拖动表格。

**技术栈：** Django 4.2、MariaDB／SQLite、APScheduler、React、Ant Design、Jest。

## 接口约定

全部接口前缀 `/api/ops/expiry/`；请求严格校验，所有响应由现有 `json_response` 包装，前端 http 接收解包内容。每次核对数据库中的活跃、未删除超管。

- `GET options/` → `{owners:[{id,name}], groups:[{key,name,parent_key}], timezone, schedule:'09:00', check:{status,last_attempt_at,last_success_at,message}}`。分组键为 `host:ID`／`panel:ID`。
- `GET assets/`，参数 `page`、`page_size`（默认20，最多100）、`q`、`source`（host／panel）、`group`（键，含子组）、`owner_id`、`state`（expired／within_7／within_30／valid／unset／invalid／exempt）、`followup`（pending／contacted） → `{items,total,page,page_size,summary,unread_count,check,timezone}`。7／30 天筛选及统计为非过期累计，未知日期不算正常。
- 资产行 `{key,name,address,sources:[{kind,id,name}],groups:[{key,name}],expires_on,original_expiry,date_source,days_remaining,state,owner:{id,name,available}|null,followup,reminder_enabled,exempt,note,version}`。version 为服务端不透明令牌，覆盖元数据和日期／关联输入；无元数据时提醒关闭。关联资产始终规范为 host:ID。
- `POST assets/<key>/`，体 `{version,manual_expiry:null|'YYYY-MM-DD',owner_id:null|ID,followup,reminder_enabled,exempt,note}` → 更新后资产行；manual_expiry=null 表示回到原记录，独立宝塔则未登记。禁止旧版本覆盖。
- `POST assets/<key>/renew/`，体 `{version,expires_on,note}` → 更新后资产；新日期必须晚于今天且晚于原生效日期（若合法），写人工覆盖并重置跟进 pending，保留提醒开关与负责人。
- `GET assets/<key>/history/`，分页 → `{items:[{id,event,actor_name,created_at,detail}],total,page,page_size}`。detail 仅允许变更白名单字段；JSON 展示前端以字段标签排版。
- `GET reminders/`，分页及 `unread=1`／`current=1` → `{items:[{id,asset_key,name,address,expires_on,stage,created_at,unread,current}],total,page,page_size,unread_count}`；历史名称／地址为有限脱敏白名单，current 重新比对规范资产和当前日期来源。
- `POST reminders/read/`，体 `{ids:[ID]}`（最多100），按当前管理员存储已读；GET 不产生提醒、不更改已读。

## 任务 1：后端资产、写入、提醒与调度

文件：新增 `spug_api/apps/ops/expiry/{__init__,models,dates,assets,service,reminders,views,urls,tests}.py`，修改 `apps/ops/models.py` 注册模型、`apps/ops/urls.py` 和 `apps/schedule/scheduler.py`。

- [x] 先编写行为测试：资产关闭监控仍合并、原值不明来源、人工覆盖、权限重新查询、日期格式和边界、来源及分组筛选、owner 停用、并发版本、历史保留。
- [x] 运行 `work/spug-testenv/Scripts/python.exe manage.py test apps.ops.expiry.tests --settings=spug.test_settings --noinput`，确认因功能缺失失败。
- [x] 实现规范资产与白名单日期解析；附加元数据、审计表，乐观版本并在事务内重查／锁定关联、主机、用户和元数据。不暴露凭据字段。
- [x] 先写提醒行为测试，再实现阶段去重、个人已读、漏跑仅当前阶段、续费新周期、关联变化暂停旧元数据和新提醒、默认关闭、无外呼及定时接线。记录检查开始／结束／失败，数据库并发生成须唯一约束。
- [x] 运行目标测试，补 MariaDB 条件并发测试，审查规格再审查质量。

日期计算接口例子：

```python
assert classify(date(2026, 10, 25), date(2026, 10, 25)) == ('within_7', 0)
assert reminder_stage(-1) == 'expired'
assert reminder_stage(5) == '7d'
assert reminder_stage(31) is None
```

## 任务 2：前端管理页和入口

文件：新增 `spug_web/src/pages/server-expiry/{index,Editor,History,Reminders,index.test}.js`；修改 `spug_web/src/routes.js` 及 `pages/host/index.js`。保留 `/host` 原入口，主机页顶部加“到期管理”，新页 `/host/expiry` 挂隐藏路由，避免把主机管理强改子菜单。

- [x] 编写失败测试并运行 `node node_modules/react-app-rewired/bin/index.js test --watch=false --runInBand src/pages/server-expiry/index.test.js`。
- [x] 实现本地只读加载、摘要、分页筛选、旧响应隔离和无权限状态。表格使用 `ResizableTable resizeKey="server-expiry"`。
- [x] 实现编辑日期／恢复原记录、负责人、备注、跟进和开关；开启提醒时确认“每日09:00、仅管理员站内”；续费须未来且晚于原日期。
- [x] 实现历史详情、个人未读列表与标记已读；只允许明确点击写入，失败保留上下文，重复提交禁用；标题和空态说明站内提醒触达范围。
- [x] 目标前端测试通过后运行全前端测试与生产构建，沿用 `NODE_OPTIONS=--openssl-legacy-provider`。

## 任务 3：集成、说明和部署工具

文件：修改 `.github/workflows/baota-checks.yml`；新增 `deploy/expiry/{prepare-expiry,publish-expiry}.py`、相应验证与说明；新增使用指南和发布记录。

- [x] CI 纳入 expiry 测试及 schedule 镜像覆盖，镜像／回退模型注册需验证。新增发布以线上8a64ca1为基础，保留库存、租约、作业、基线、用户与提醒；不得沿用空库存假设。
- [x] 先检查资源与服务，用受限隔离运行时和 MariaDB 演练现网迁移历史到附加表，种旧库存与既有作业行并核对前后不变。
- [x] 规格审查通过后完成独立质量审查，修复并再次验证；更新文档、提交及推送草稿 PR #4，等待检查结果。
- [x] 生产配对备份、空闲检查、停止必要写入入口，增量发布与迁移，保留原数据与兼容回退，恢复九服务并验证 HTTP／UI revision／平台健康。
- [x] 浏览器只读验收真实主机与面板到期空态，不改客户日期、不启用提醒作演示、不调用客户API；保存截图和验证记录。

## 验收门槛

- [x] 所有日期、权限、合并、人工覆盖、提醒去重、个人已读和并发冲突测试通过。
- [x] 全后端回归、前端测试和 build，独立迁移与回退检查通过。
- [x] 线上九服务正常、健康页正常、新入口可用；使用文档和发布记录已保存，开发记录在现有分支与 PR。

实际发布与测试证据见 `docs/releases/2026-10-25-server-expiry.md`。应用提交 `5e6f50d` 已上线，后续文档和只读验收工具提交不改变运行代码。
