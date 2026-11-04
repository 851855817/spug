# SSL 监控发布记录

应用候选：`440b0a5da191fb46e586347889fb4f6186a29cd3`。发布前基准：`48c305dbbd656fa9cc2ecbbf9be749fb390bf3fc`。沿用 `feature/operations-reliability` 和草稿 PR #4，不合并主分支。

## 功能

- 监控中心 `/monitor/ssl`：统计卡、域名列表、来源/分组/状态筛选、精确 UTC+8 日期、有效期、源站/公网证书对照和具体异常。
- 宝塔与 SSH 来源独立开启，默认关闭监控与提醒，默认每 6 小时检查；本地刷新不会连接客户。个人提醒按证书周期去重，支持忽略和恢复。
- 续签仅复用已有宝塔、acme.sh 或 Certbot 配置，先只读预检，明确确认后才提交；发送前复查授权，按服务器串行，不自动安装或重发不确定意图。
- 支持取消未执行预检、30 分钟失效、断线后只读核验及已证实本地和公网新证书的终态释放；重载回执缺失仍单独标明。
- 含未展开 include 的宝塔网站可用源站 SNI 观察结果监控，监控页不直接续签，提供既有面板任务预检入口；通配符不能直接续签，未知 hook 不接管。

## 验证证据

- 本地完整后端 681 项通过（13 条件跳过）；最后 SSL 专项 101 项通过（1 MariaDB 专用跳过）。前端 48 套/464 项通过，目标 ESLint 无错误，生产构建成功（两处原有 hooks 提示）。
- 独立功能、变更边界和发布代码审查完成。SSL 发布失败路径 33 项通过。
- 最终候选构建在全新临时 Chrome 中完成 17 项模拟验收：[报告](2026-11-04-ssl-monitor-browser.json)、[浅色](ssl-monitor-shots/01-porcelain-overview.png)、[深色](ssl-monitor-shots/05-midnight-teal-overview.png)。仅访问 loopback，不使用真实客户接口。
- [push CI](https://github.com/851855817/spug/actions/runs/41768883477) 和 [PR CI](https://github.com/851855817/spug/actions/runs/41768891441) 的前端、后端和 MariaDB 全绿。
- 匿名下载候选源码 1120 文件与该 Git 提交逐字节一致，原 LICENSE 保留。

## 服务器隔离演练与发布状态

正在使用专用六表附加迁移发布流程，尚未切换现网。真实隔离演练发现并修复兼容镜像缺少 CI 专用 settings 的夹具问题；失败尝试保留，未覆盖运行中的应用。只有完整 Linux/MariaDB、迁移和兼容回退演练成功，重新核实现网与空闲，并建立新配对备份后才允许发布。

不以真实客户续签、扫描或远程命令验收，不开启客户监控或提醒。使用说明见 [SSL 监控指南](../ssl-monitor-guide.md)，维护步骤见 [专用发布器](../../deploy/ssl-monitor/README.md)。
