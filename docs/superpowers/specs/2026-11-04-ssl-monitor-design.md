# SSL 证书监控与续签

用户已同意：监控中心新增 SSL 监控，参照截图的浅色统计卡片和域名列表；宝塔和仅 SSH 服务器均自动发现，复用宝塔、acme.sh 或 Certbot 续签，缺少工具时提供安装配置指导入口。先检查、确认后变更、执行后检查部署结果。

## 范围与默认值

入口 `/monitor/ssl`，首版与资产检查台一致仅活跃超级管理员可用，不给已有岗位隐式增加远程权限。宝塔、SSH 来源配置独立于资源监控；默认关闭，选中来源并明确开启后才联系客户。每个来源默认每 6 小时检查，允许 1/6/12/24 小时，手工检查冷却 60 秒。页面自动刷新仅读本地数据。来源变化或账号停用会停止后续采集/变更，不将旧缓存伪装最新。

后台以数据库队列及租约协调，复用现有 scheduler 的独立有界作业，不增加服务进程。最多一个发现/检查任务在途，续签同服务器串行；源站文件数量、大小、include 深度、域名数量、输出和总耗时均限制，超过限制明确失败，不把截断快照当完整数据。检查失败保留上次结果并标旧。重启不自动重发已持久化的续签意图；不确定项只允许读回核验。

## 发现与证书事实

宝塔读取既有网站/域名 API 和公开证书摘要，不持久化 PEM/私钥/API 密钥。仅 SSH 使用固定只读 Python 3 探针，读取 Nginx/Apache 的固定配置根及有界 include，识别 server_name/ServerName/ServerAlias、SSL 监听和公开证书路径，不执行配置，不递归扫描磁盘，不读私钥。缺少 Python、不可解析动态配置、容器内部配置、未知 Web 服务均具体报告覆盖不足；不承诺列出机器上任何位置的孤立证书。

宝塔标准 include 的目录 API 经源码核对含目录历史/下载令牌清理副作用，且枚举预算不足，不用于自动只读巡检。主配置与固定公开证书已核对但 include 未遍历时，允许向已保存面板公网地址进行固定解析的源站 SNI 握手；实际证书与面板副本匹配可建立到期监控事实，不能建立部署路径已完整验证的结论。此类条目不在监控页直接续签，提供既有宝塔任务预检入口；公网域名握手仍另行检查。仅通配符条目不接受续签，使用同证书具体域名完成独立核验。

每条记录以来源＋站点身份＋域名＋TLS 端口定位，保留同域名不同源站；复用已确认主机关联标注，不按相同 IP 随意合并。域名 IDNA 规范化，正则/default_server/wildcard 不能冒充可握手域名，通配符可以显示本地证书但不对 `*.example` 发握手。

返回本地部署证书的签发/到期精确 UTC 时间、SHA-256 指纹、SAN，以及域名公网 SNI TLS 的独立结果。公网检测固定解析结果并拒绝回环、私有、链路本地及特殊地址，不通过 URL/HTTP 跳转，不请求网页；只握手，不发送业务数据。允许访问已管理来源本身不等于允许任意配置中的内网目的地。连接/握手/DNS 由独立子进程总预算兜底，过期/自签/域名不符仍保留能解析的公开证书元数据，并明确验证失败；握手成功不能等同证书可信。CDN 公网证书与本地证书不同单独标识。

## UI 与提醒

上方为域名总数、正常、10 天内到期、已过期、检查失败统计，可点击筛选；搜索域名/来源，按服务器/分组/状态筛选，临期优先，列支持现有拖拽。列表显示域名、服务器、状态、签发、到期、剩余天数、简短原因、续签/详情，遵循全站十一套主题。签发/到期按 UTC+8 展示，到期判断使用精确时刻；未知/未配置/旧数据不算正常。

提醒在 30/10/7/3/1 天及已过期阶段去重；仅有效检查产生，旧缓存/检查失败产生诊断而非伪造到期。独立 SSL 提醒 API 按当前账号支持不再提醒/恢复，到期日期或证书指纹变化为新周期。监控页和顶栏/首页可查看，不向全局 Notify 发送可能泄漏客户域名的信息。外部渠道与无人确认自动续签不在本版范围；现有定时宝塔任务保持。

## 续签

宝塔复用原 `ssl_renew` PanelTask：选择条目创建只读预检，确认面板同步影响后调用原开始；引用原任务 ID，可查看逐台状态，任务成功后重新检查本地与公网证书。

SSH 探针识别现有 Certbot 和 acme.sh 证书订单与真实部署路径，不执行 `source`/`eval` 解析配置。仅支持能唯一映射到当前网站公开证书的配置；读取/固化摘要、工具身份、域名集合和明确部署/重载方式。未知 hook、自定义命令、动态路径、多个订单、未部署/不匹配、缺少工具均阻止变更并给处理建议。不安装或覆盖现有客户端；缺少工具时入口展示 acme.sh/Certbot 官方安装配置指导与重新检测按钮，安装另须明确操作确认。

续签使用固定 Python 调用及参数数组，只针对所选订单，不调用全量 cron、不强制续签、不接受前端命令。确认页列出来源、域名、证书位置、客户端、验证方式、会执行的固定服务重载及所有影响。重读账号、来源和配置，持久化意图后才下发；SSH 断线/超时保持待核实，不自动重试。成功须证书指纹/有效期更新、部署路径一致及已知服务重载成功；域名公网仍旧证书时明确显示公网未生效，不能报整体完成。默认不修改原验证方式或 DNS 凭据；DNS 验证仅复用现有已配置订单。

## 模块与 API 合同

新包 `apps/monitor/ssl/`，models 在既有 monitor.models 显式导入，新增独立表，不修改原 Detection 字段。`collectors.py` / `remote_probe.py` / `tls.py` 管有界发现与传输；`service.py`、`jobs.py`、`renewal.py`、`views.py`/`urls.py` 管授权、本地列表、队列和续签。所有异常输出固定安全原因；路径公开可显示，不回传原配置、密钥、证书原文或 transport 错误。

API 前缀 `/api/monitor/ssl/`：

- GET `sources/` 返回 `{items:[{key:'host:1'|'panel:2',kind,id,name,address,groups,enabled,reminders,interval_hours,revision,last_attempt_at,last_success_at,status,message}]}`，GET 不创建偏好。
- POST `sources/` `{key,revision,enabled,reminders,interval_hours}` 显式配置，返回同源摘要。未配置 revision 为空串；乐观锁防丢失更新。
- GET 根：`q,source,state,page,page_size`；返回 `{items,total,stats:{total,normal,expiring,expired,error},sources,active_jobs}`。证书行 `{id,source_key,source_name,domain,port,state,message,not_before,expires_at,days_remaining,sampled_at,stale,local_certificate,public_tls,renewal:{engine,supported,reason,summary},latest_job_id}`。
- POST `checks/` `{source_keys:[...]}` 仅队列，返回 `{jobs:[...]}`；不从视图同步执行 SSH/TLS。
- GET `jobs/`（page/page_size）和 `jobs/<id>/` 返回阶段、状态、原因与安全结果，不返回内部计划。
- POST `renew/` `{certificate_id}` 仅创建续签预检，返回 job；POST `renew/<id>/start/` `{revision,acknowledgements:[...]}`；POST `renew/<id>/verify/` `{}` 只读。Job 公共结构 `{id,kind,state,phase,message,revision,certificate_id,panel_task_id,acknowledgements,summary,result,created_at,updated_at}`。确认项 `{code,label}`，前端开始只提交 code。
- GET `reminders/` `muted=1,page,page_size` 返回 `{items,total,unread_count}`；行 `{id,certificate_id,domain,source_name,stage,expires_at,message,muted}`。POST `reminders/<id>/mute/` `{muted:bool}` 仅本人。

## 验收与发布

隔离 SQLite/MariaDB 和模拟接口覆盖日期边界、TLS 证书可信/不可信、域名验证、DNS 换绑、配置注入、include 越界/循环、证书/私钥隔离、工具/路径/钩子识别、账号撤销、重复触发、并发续签、意图崩溃、失联读回、个人提醒隔离。浏览器用模拟数据核对截图风格、统计筛选、失败原因、开关和续签确认。真实客户不用于验收，不预开启监控和提醒。

这是新增附加表的功能，不能套用无迁移的旧 profile。先重新核实现网；使用专用精确源文件清单、原63项业务/模式/密钥保全、新表空态与附加迁移演练、配对备份、原九服务和保留新表的兼容回退；无法证明安全则保留正常线上服务并继续独立测试。沿用原分支、草稿 PR #4，不合并主分支。
