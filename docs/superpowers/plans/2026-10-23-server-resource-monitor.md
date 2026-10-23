# 服务器资源监控实现计划

> 使用 subagent-driven-development 实现职责独立模块，集成后进行规格与质量审查。用户已批准设计和默认 60 秒、最多并发 2 台的负载控制，无需重复确认。

**目标：** 在主机与宝塔列表按来源开启监控，默认关闭；监控中心用可关联的服务器卡片展示真实采样。

**架构：** 新增模块置于 apps.baota/resource_monitor，复用主机和宝塔的权限与凭据，不修改原主机表。持久化偏好、快照和两个短租约槽，网络请求在事务之外；服务端快照共用并校验配置摘要。前端最多一个采集请求顺序执行，服务器全局限制两台，隐藏／离开页面立即停止后续请求。

**技术栈：** Django ORM、Paramiko、现有 PanelClient、React 16、Ant Design 4。

## 文件与边界

- collector.py / probe.py：固定 Linux SSH 只读脚本、墙钟／输出限制，宝塔 GetNetWork 返回值白名单归一化；不读取数据库。
- models.py / service.py / views.py / urls.py：新偏好、快照和租约模型，权限、关联、采样与缓存 API。由 baota/models.py 导入模型类，使 updatedb 和隔离 syncdb 发现新表。只新增表，不更改已有表。
- spug_web/src/pages/server-monitor：卡片、筛选、刷新、合并设置和独立测试；components/ResourceMonitorSwitch.js 及列表状态 hook：列表开关。
- routes.js：监控中心含服务器监控与原监控任务，保留 /monitor 原路由。
- deploy/resource-monitor：基于当前运行镜像派生、测试与回退说明；前端仅本机构建。

## API 合约

前缀 `/api/baota/monitor/`。所有返回沿用 `{data,error}` 包装，任何网络采集都不能泄漏响应正文、凭据、配置和进程参数。

- `GET sources/?kind=host|panel` → `{sources:[{id,kind,name,address,enabled,group_ids,groups,linked_host_id}],hosts:[{id,name,address}]}`。sources 按对应来源 view 权限过滤；hosts 仅提供有权编辑／关联且有分组权限的主机，列表不开启监控也能查看状态。
- `POST settings/ {kind,id,enabled}` → 对应 source。enabled 必须布尔，沿用来源 edit 权限且主机分组过滤；只保存状态，不发送网络请求。禁用立即清空该源快照。
- `POST link/ {panel_id,host_id:null|整数}` → `{ok:true}`。关联需双方 view/edit 及主机分组权限。无权源完全不进入卡片；相同 IP 仅提示，用户显式选择并确认关联。
- `GET cards/` → `{cards:[{key,name,address,groups:[{key,name}],sources:[{kind,id,name}],snapshot:null|{status,message,sampled_at,attempted_at,metrics,source_kind},can_link,panel_id,linked_host_id}],default_interval:60}`。仅已开启且有来源 view 权限的记录参与分组。每个已开启主机与其可访问的已开启关联面板组成一张卡，主机 key 为 host:ID，其余为 panel:ID。指标采用 API 优先，失败后仅允许已开启 SSH 后备。无监控页权限不得读取 cards 或采样。
- `POST sample/ {key,interval:30|60|300}` → `{key,snapshot,retry_after,retry_reason}`。重新校验权限、开关和配置；已有缓存最短间隔 30 秒，interval 指定所需年龄，不绕过租约与退避。retry_reason 为 cache／collected 时成功采样年龄随新间隔计算，busy／backoff 必须遵守绝对等待时间。忙返回当前快照与重试秒数，不建线程／后台队列。

metrics 固定结构：`{cpu:{percent,cores},memory:{used_bytes,total_bytes,percent},load:[1m,5m,15m],network:{up_bytes_sec,down_bytes_sec},disks:[{path,used_bytes,total_bytes,percent}]}`。未知数值 null；字节为非负有限值；百分比 0～100；禁止将非法值修成看似正常的 0。最多 32 个磁盘。

## 任务 1：采集器（先测试）

- [x] 编写 test_resource_collector.py：宝塔 9.2/11.7/13.1 响应的 CPU list、mem MB、网络 KiB/s、disk size 单位字符串；异常、NaN、布尔、超大值拒绝。SSH 脚本在本地 Linux 夹具运行，不接触客户机器。
- [x] 运行 `python manage.py test apps.baota.test_resource_collector --settings=spug.test_settings --noinput`，确认缺失功能失败。
- [x] 实现 collect_panel(panel)、collect_host(host) → metrics。宝塔固定 GetNetWork 一个查询 timeout(3,8)；SSH连接／认证／banner与读取共享总预算 15 秒，输出最多 64KiB，无命令插值、无安装／写文件。CPU和网络用两次短间隔采样；内存 /proc/meminfo；磁盘用限时固定本地 df 查询。
- [x] 测试通过并记录实际测试范围，提交独立文件。

## 任务 2：偏好与采样服务（先测试）

- [x] 新增三个模型 ResourceMonitorPreference、ResourceMonitorSnapshot、ResourceMonitorLease，独立新表；偏好 host 或 panel 恰一项约束，默认 enabled=False，panel 可 linked_host；模型导入经 apps.baota.models。
- [x] 测试默认关闭、bool严格校验、view/edit组合权限、主机组授权、开启不联网、禁用后无卡片也不可直接采样、关联双方权限／取消关联、隐藏关闭源、API优先与SSH后备、缓存和配置摘要、失败退避、过期租约／令牌释放、最多两个全局采集、上次未完成不重叠。
- [x] 执行失败测试，再实现 API。偏好配置变更更换 revision；快照读取与写入都重新校验 revision/凭据摘要，禁用或凭据更改时旧结果不得返回。租约原子条件更新，释放需匹配令牌，60秒租约大于一次全采样墙钟预算。网络不在数据库事务内。
- [x] 部分失败返回安全短信息，旧数据可带 stale 状态但时间不更新冒充成功。失败间隔 60、120、240、300秒封顶；所有读者共用。只保存指标，不保存远端原始对象。
- [x] 执行 SQLite 和隔离 MariaDB 关键测试，提交。

## 任务 3：页面与开关（先测试）

- [x] 编写页面测试：关闭的来源不展示；空态指向两处开启开关；初始和刷新顺序采样；默认60秒及30/300选择；隐藏/离开/暂停后无后续请求；旧响应不能覆盖新开关／筛选；失败卡片与null进度；开关禁用权限、持久化回读；关联提交需明确确认。
- [x] 实现 ResourceMonitorSwitch 和来源 hook，一处列表统一加载 sources，行只发 settings POST，不在每行发GET。原列表刷新需刷新监控状态，卸载后忽略异步回调。
- [x] 实现卡片网格，group/source/status过滤与名称/IP检索；隐私隐藏IP开关；时间和状态明确，磁盘展开；无虚假预填数据。合并设置弹窗从有权限来源列表选择主机或解除关联，相同地址只是建议。
- [x] 入口 routes.js 监控中心新增子菜单。运行页面单测、模块lint和本地生产构建，检查桌面与窄屏。

## 任务 4：集成、审查、部署

- [x] 核对设计逐项覆盖，独立规格审查后独立代码质量审查；修复关键问题，运行后端全模块、前端全套和生产构建。
- [x] 创建新草稿 PR，base feature/baota-panel-tasks，附加到当前任务；不合并。
- [x] 基于当前正式 spug-baota:5437d77 或实际运行镜像做派生镜像，复制新模块与本机前端，不在2核服务器编译依赖。记录源码提交与产物SHA。
- [x] 新表迁移只在应用代码可发现的 baota 内生成；隔离克隆验证新增表与关闭默认值，验证兼容回退保留表。发布前备份数据库、卷、密钥和Compose；有运行任务时不强制中止。
- [x] 发布后检查原记录、密钥解密、任务锁与状态、全部服务和HTTP，核对所有偏好默认关闭。浏览器展示真实空态和列表开关；只在独立夹具验证有数据卡片，不擅自开启客户采样。
- [x] 文档记录真实测试与部署边界、截图、备份和回退方法，推送开发记录。
