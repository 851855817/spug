# SSL 监控发布记录

已上线应用：`0e5edaf2dbb639a185e0def29291b469641c5d10`。发布前基准：`48c305dbbd656fa9cc2ecbbf9be749fb390bf3fc`。沿用 `feature/operations-reliability` 和草稿 [PR #4](https://github.com/851855817/spug/pull/4)，不合并主分支。

## 功能

- 监控中心 `/monitor/ssl`：统计卡、域名列表、来源/分组/状态筛选、精确 UTC+8 日期、有效期、源站/公网证书对照和具体异常。
- 宝塔与 SSH 来源独立开启，默认关闭监控与提醒，默认每 6 小时检查；本地刷新不会连接客户。个人提醒按证书周期去重，支持忽略和恢复。
- 续签仅复用已有宝塔、acme.sh 或 Certbot 配置，先只读预检，明确确认后才提交；发送前复查授权，按服务器串行，不自动安装或重发不确定意图。
- 支持取消未执行预检、30 分钟失效、断线后只读核验及已证实本地和公网新证书的终态释放；重载回执缺失仍单独标明。
- 含未展开 include 的宝塔网站可用源站 SNI 观察结果监控，监控页不直接续签，提供既有面板任务预检入口；通配符不能直接续签，未知 hook 不接管。

## 验证证据

- 最终制品在服务器隔离运行 Linux 后端 682 项测试（含 11 条件跳过），MariaDB SSL 54 项测试，均无失败。前端 48 套/464 项通过，目标 ESLint 无错误，生产构建成功（两处原有 hooks 提示）。
- 独立功能、变更边界和发布代码审查完成。SSL 发布失败路径 34 项通过。
- Linux 发布保护回归：平台健康 22、资产 7、到期 10、原发布保护 185、SSL 发布 34 项，全部无失败；[隔离演练收据摘要](2026-11-04-ssl-monitor-isolation.json)。
- 最终候选构建在全新临时 Chrome 中完成 17 项模拟验收：[报告](2026-11-04-ssl-monitor-browser.json)、[浅色](ssl-monitor-shots/01-porcelain-overview.png)、[深色](ssl-monitor-shots/05-midnight-teal-overview.png)。仅访问 loopback，不使用真实客户接口。
- [push CI](https://github.com/851855817/spug/actions/runs/41770561416) 和 [PR CI](https://github.com/851855817/spug/actions/runs/41770569156) 的前端、后端和 MariaDB 全绿。
- 匿名下载候选源码 1122 文件与该 Git 提交逐字节一致，原 LICENSE 保留，见 [源码核对](2026-11-04-ssl-monitor-source.json)。

## 服务器隔离演练与发布状态

2026-11-04 12:12（UTC+8）开始实际发布，使用独立六表附加迁移流程。此前两轮隔离演练分别发现兼容镜像缺 CI settings、制品缺受版本管理的 public 源码，均已修复并增加回归；失败尝试保留，未覆盖当时运行中的应用。最终完整演练验证冻结迁移 `monitor/0002_ssl_monitor`、非空旧数据、六张新空表、兼容回退启动及两份镜像的 migrate-only 入口。

发布时重新核实现网版本与业务空闲，创建数据库和 `/data` 配对备份 `20261104-121208-before-upgrade-0e5edaf-187a0356` 后切换。独立只读复核确认：原业务／模式／迁移历史／凭据保全，完整 API 和静态文件与制品一致，原要求保留的进程不变，九个服务运行、15 项健康检查正常，健康定时器恢复，内部和公网 HTTPS 均返回 200；见 [上线核验摘要](2026-11-04-ssl-monitor-publication.json)。

六张新表仍为空，全部 SSL 来源及提醒关闭，默认 scheduler 空跑不产生记录。来源设置中由管理员自行选择宝塔或 SSH 主机开启；不会代替用户扫描或配置客户服务器。公开入口：[SSL 监控](https://47.104.88.147/monitor/ssl)。

不以真实客户续签、扫描或远程命令验收，不开启客户监控或提醒。使用说明见 [SSL 监控指南](../ssl-monitor-guide.md)，维护步骤见 [专用发布器](../../deploy/ssl-monitor/README.md)。
