# SSL 监控实现计划

> 面向 AI 工作者：使用 subagent-driven-development 实现独立单元，按 test-driven-development 先失败再实现；独立审查并记录每轮证据。

**目标：** 在监控中心集中监控宝塔和 SSH 主机网站证书，提供到期提醒与受控续签。

**架构：** monitor.ssl 独立附加模型＋数据库队列，现有 scheduler 运行有界单实例作业；传输/探针与授权/数据分离，前端复用站内主题和表格。

**技术栈：** Django、MariaDB、Paramiko、cryptography、Python 标准 TLS、React、Ant Design。

规格：`docs/superpowers/specs/2026-11-04-ssl-monitor-design.md`。用户已认可两类服务器＋ACME 方案，按规格中的低频默认值推进，不重复请求实现许可。

- [x] 1. 实现 `spug_api/apps/monitor/ssl/{collectors,remote_probe,tls}.py` 与 `test_collectors.py`：固定目录和 include 有界解析、公钥元数据、独立子进程预算、公网 TLS、ACME/Certbot 安全订单发现及固定续签执行。先运行缺少能力的测试失败，再实现；纯探针临时目录模拟，无客户连接。
- [x] 2. 实现 `spug_api/apps/monitor/ssl/{models,service,jobs,renewal,views,urls}.py` 与 `tests.py`，修改 monitor models/urls 和 schedule scheduler：默认关闭、定时队列、版本 CAS、授权、失败保留旧快照、去重提醒、续签预检/确认/意图/读回。测试 `../../spug-testenv/Scripts/python.exe manage.py test apps.monitor.ssl.tests apps.monitor.ssl.test_collectors --settings=spug.test_settings --noinput`（在 spug_api）。隔离测试创建模型表，不使用生产库。
- [x] 3. 实现 `spug_web/src/pages/ssl-monitor/` 页面与测试，修改 routes、首页和顶栏最小提醒接线：统计卡、证书表、筛选、来源开关、任务结果、续签确认与工具指导。`D:/nodejs/node.exe node_modules/react-app-rewired/bin/index.js test --watchAll=false --runInBand`。浏览器模拟无需真实账号/接口。
- [x] 4. 规格和代码质量独立审查，修复实际发现的问题；更新 `.github/workflows/baota-checks.yml`、`deploy/baota/Dockerfile` 精确接线及指南。完整回归、build、Linux 和 MariaDB 并发验证。
- [x] 5. 新增 `deploy/ssl-monitor/` 附加迁移发布方案和失败回退回归，隔离演练；先核对现网版本/空闲，完成新配对备份后才发布。独立验证新表全空/默认关闭、63旧保全对象不变、九服务与健康、公开源码，更新原草稿 PR 和 Git。

## 接口分工

探针负责 `discover(kind, source)` → `{items:[{domain,port,site_id,site_name,cert_path,certificate,renewal}],warnings:[]}`；certificate 仅 `{fingerprint,domains,not_before,expires_at}`，renewal 含 engine/supported/reason/summary 和仅内部计划；`check_tls(domain,port)` 返回验证结论与公开元数据；`renew_host(host,plan)` 固定命令，仅接受已验证探针生成的计划。探针无数据库副作用，凭据仅内存/子进程管道。工作流负责将计划留在服务端、摘要冻结、重读及实际发送授权。前端只用规格列出的公共 API 字段。

## 执行记录

开始：沿用 `feature/operations-reliability`，本地 HEAD 36d311f，上一轮已验证线上应用 48c305d；发布前仍须重新核实。未修改客户监控状态或执行任何真实任务。

## 实现与独立审查（2026-11-04）

探针、六表工作流、页面、来源开关、个人周期提醒和受控续签已实现。审查修复 Apache On/OFF、固定 rewrite include、Certbot 订单证书匹配、来源幂等与显式重新授权、预检取消和 30 分钟失效、通配符禁止直接续签，以及断线后只读核验终态释放。发送前重新授权覆盖原宝塔 Worker 及公开下载边界。

宝塔默认 include 不能经只读且有界的 API 完整枚举，采用受管公网源站 SNI 观察证书支持监控，保持该类续签不受权，界面提供既有面板任务预检入口。acme.sh 只接管精确绝对 Nginx 检查/重载命令，未知 hook 继续拒绝；无自动安装或无人确认续签。

验证：完整后端 681 项通过（13 跳过），最后 SSL 两模块 101 项通过（1 MariaDB 专用跳过）；前端 48 套/464 项通过；SSL 发布失败路径 32 项通过。目标 ESLint 无错，生产 build 通过（两处既有 hooks 警告）。独立功能审查无剩余阻塞 P1/P2；发布审查修复候选及兼容镜像必须均使用冻结 migrate，禁止重启时 updatedb 生成迁移。

Chrome 全新临时配置使用 loopback 模拟接口验收 17 条，确证 porcelain 与 midnight-teal 实际主题，保留截图与网络报告；未联系客户。现网只读重新核实仍为 48c305d、业务空闲、63 保全对象；尚未发布 SSL 功能。下一步：提交并更新原 PR、CI、真实 Linux/MariaDB 隔离附加迁移及兼容回退演练，全部通过后新配对备份发布并独立核验。

服务器隔离演练已实际生成并应用 `monitor/0002_ssl_monitor`，非空旧数据与六张空表核对通过；候选及兼容镜像已构建。首次构建因逐层落盘耗时中断后保留失败目录并用缓存重跑。兼容源导出验证发现正式镜像未包含 CI 专用 settings，已补仅向隔离副本复制两份测试 settings，原运行配置不覆盖；新增回归先失败再通过，SSL 发布测试增至 33 项。此阶段始终未改变现网。实现提交 60f6f4b 的 push/PR 两组 CI 三个作业均全绿，1119 个匿名源码文件逐字节一致；修复提交须重新演练后再发布。

440b0a5 隔离演练通过六表迁移、非空旧数据保全、兼容启动、两份镜像迁移入口与九服务接线，MariaDB 54 项全部通过、Linux 682 项通过（11 跳过）。后续旧发布保护 185 项发现制品缺少 `spug_web/public` 原许可静态文件，发布被阻止。已补完整受版本管理的 public 目录及导出行为回归，34 项 SSL 发布测试通过、旧发布 185 项通过（Windows 跳过 1 条），不修改旧测试或削弱保全条件。此缺陷只影响验证制品，无线上写入；重新打包并完整演练。

最终应用提交 `0e5edaf2dbb639a185e0def29291b469641c5d10` 完整隔离演练通过，冻结迁移为 `monitor/0002_ssl_monitor`。MariaDB 54 项无跳过；Linux 682 项（含 11 跳过）无失败；发布相关测试分别为健康 22、资产 7、到期 10、原发布保护 185、SSL 34，全部无失败。候选及兼容回退镜像完整文件树与三份镜像 ID 已绑定收据，非空旧数据和六张空表对照通过。最终 push／PR CI 各三作业全绿；匿名源码 1122 个文件与该提交一致；Chrome 17 项模拟验收通过。

12:12（UTC+8）重新核实现网与空闲，建立新配对备份 `20261104-121208-before-upgrade-0e5edaf-187a0356` 后自主发布成功。独立只读核验：旧数据／表结构／迁移／凭据摘要一致，精确 API／UI 文件一致，原保留进程不变，九服务和 15 健康检查正常，内网及公网 HTTP 200，健康定时器 active。六新表全空、全部来源和提醒关闭，scheduler 空跑无写入；没有以真实客户发现、远程命令或续签验收。上线和隔离核验摘要、最终浏览器证据见 `docs/releases/2026-11-04-ssl-monitor.md`。后续按用户在来源设置中的主动选择开始监控；本轮不扩大权限或开启客户任务。
