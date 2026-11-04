# 移除重复的网站与证书菜单

用户要求移除“批量执行 → 网站与证书”，证书日常入口为“监控中心 → SSL监控”。本次运行变更只有 `spug_web/src/routes.js` 中 `/exec/sites` 的 `hidden: true`，旧首页待办和历史书签兼容。原 API、数据、权限、监控和提醒开关不变。

- 前端 49 套／479 项、routes ESLint 与生产构建通过。发布回归 211 项（Windows 跳过 1 项 Linux 进程锁检查）；固定 routes-only profile 的 8 项合同先失败再通过。
- Chrome 临时配置与 loopback 模拟 GET 完成 8 条验收：菜单移除、既有导航排序、调整导航／恢复默认、SSL 监控可用、旧 URL 兼容、深色窄屏、零客户流量及零写请求。源码和主资产哈希与正式制品绑定。[报告](2026-11-04-hide-site-inventory-browser.json)、[菜单](hide-site-inventory-shots/01-batch-menu.png)、[SSL 监控](hide-site-inventory-shots/02-ssl-monitor.png)。
- 独立审查未发现阻断问题，确认导航先过滤隐藏项，profile 仅 UI 发布与回退、API 空覆盖和完整树保全接线完整。

已于 **2026-11-04 17:25（UTC+8）**发布应用 `340ead51eac670811f58b82ac9bc880e4a25e0f8`，替代 `61980491e2565c2c9b68f9a08717ebc69f2b308b`。新鲜空闲检查通过，配对备份为 `/opt/spug/backups/20261104-172559-before-upgrade-340ead5-a6a298bd`。

最终候选 Linux 隔离验证：后端 695 项（11 项条件跳过）、发布保护 211 项、到期发布 10 项、SSL 发布 34 项、post-ssl 镜像 6 项通过。[应用 CI](https://github.com/851855817/spug/actions/runs/41806638052) 与 [PR CI](https://github.com/851855817/spug/actions/runs/41806648593) 的后端、前端和 MariaDB 三作业全绿。

独立线上复核：69 项业务／模式／凭据摘要不变，SSL 六表原数据、监控与提醒开关保全；两份完整 API 树 247／287 文件逐字节不变，API 覆盖为空。配对备份和 UI 原始收据有效，9 服务正常，原保留进程身份一致，15 项健康检查正常，健康 timer active，公网／服务器 HTTPS 及内网 HTTP 均 200。原许可文件保留。详情见 [独立核验摘要](2026-11-04-hide-site-inventory-verification.json)。

没有向客户执行命令、巡检或证书任务，没有开启监控或提醒。保留 feature/operations-reliability 分支和草稿 PR #4，不合并主分支。
