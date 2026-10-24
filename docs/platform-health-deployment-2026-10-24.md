# 平台健康发布记录 · 2026-10-24

代码版本：`a8af512d648c55a9f9852851de861cbf9e0e5a99`；开发分支 `feature/operations-reliability`；[草稿 PR #4](https://github.com/851855817/spug/pull/4)，基于现有服务器监控开发分支，不合并主分支。

## 发布结果

已部署至现有 Spug 服务。入口：[系统管理 → 平台健康](http://127.0.0.1:18080/system/health)，仅超级管理员可用。

- 使用本机构建的前端资产，在服务器隔离容器测试后替换 API 固定模块和静态入口；无需数据库迁移。
- 仅重启 `spug-api`。应用容器、重启次数及面板任务、批量执行、监控、调度四类工作进程 PID 保持一致，八项服务均 RUNNING。
- 发布前后业务表摘要、面板密码/API 密钥可解密状态、密钥与指纹文件的摘要一致。
- 已安装只读宿主机采集定时器，每 5 分钟发布脱敏报告；不包含自动启动或恢复动作。手工核验最近配对备份的两个 SHA-256 摘要成功，不代表进行过恢复演练。
- 真实接口 14 项检查全部正常；无权限读写、非允许服务请求和运行中工作进程的启动请求均被拒绝。
- 浏览器确认菜单入口、14 项状态、手动刷新时间和 JSON 报告下载正常；页面显示正常、需要关注 `0 / 14`。

镜像分层源为之前已发布的 `spug-baota:6b5b3c8`，新镜像 `spug-baota:a8af512`，ID `sha256:5707e6bd8bc8`（短 ID）。Compose 已指向新镜像，以保证未来重建时保留本功能；当前运行容器沿用原容器，只热更新应用代码和资产。

发布包：`/opt/spug/platform-health-a8af512.tar.gz`，SHA-256 `1ae23a47b086469979212c411d661149ffd971e72545c0af2bb49b3fc163f1f4`。发布日志：`/opt/spug/platform-health-a8af512-release.log`。服务器端发布脚本：`/opt/spug/publish-platform-health.py`。

## 验证记录

| 验证 | 结果 |
| --- | --- |
| 前端全部测试 | 20 个套件、181 项通过 |
| 健康后端专项 | 18 项通过，包括 UTC 时间兼容、密钥换行、并发恢复、缓存、权限、超时和审计失败 |
| 生产运行时隔离回归 | 367 项通过，2 项按运行环境跳过；未挂载生产数据卷，测试数据库为临时 SQLite |
| 宿主机 CLI | Linux 22 项全部通过；本机 Windows 原生符号链接 1 项跳过，其他链接拒绝测试通过 |
| 镜像接线验证 | 3 项通过 |
| GitHub Actions | backend、frontend、mariadb 均通过 |
| 生产构建 | 通过；保留现有 `AutoDeploy.js` Hook 依赖警告 |
| 线上检查 | HTTP 200、14 项正常、定时器 active、业务/密钥保全一致 |

独立审查发现并修复了 Python 3.9 对 `Z` 时间格式兼容、检查快照与恢复操作竞争、密钥尾部换行导致误报三个问题，均补回归验证。

## 回退资料

回退是人工维护操作，当前没有执行回退。先核对无用户任务正在发布或执行，再按需操作，避免与其他管理员同时维护。

- 宿主机发布前 Compose、业务保全摘要及发布结果：`/opt/spug/backups/platform-health-a8af512/`，权限 600/700。
- 两份 API 原文件备份：容器 `/data/spug-api-backups/a8af512d648c55a9f9852851de861cbf9e0e5a99/`。
- 原静态入口与清单：容器 `/data/spug-ui-backups/a8af512d648c55a9f9852851de861cbf9e0e5a99/`。旧散列资源保留。
- 暂停宿主机采集：`systemctl disable --now spug-platform-health.timer`，再停止相应 service。
- 恢复 API：通过容器内本次固定模块发布器的 `restore` 模式还原 `setting/urls.py`，移除本次新增三个模块，再受控重启 `spug-api`。
- 恢复静态入口：从上面的两份编号目录，将 `index.html`、`asset-manifest.json`、`ui-release.json` 分别还原到 `/opt/spug/spug_web/build` 与 `/data/spug/spug_web/build`。
- 恢复宿主机备份的 Compose 文件，使未来重建回到此前镜像；**不执行容器重建或数据还原**。

完整使用说明见 [平台健康指南](platform-health-guide.md)。本文保留第一阶段 `a8af512` 的历史发布结果。后续版本 `2a2eb36` 已交付巡检汇总、分批执行和配置差异，健康检查扩展至 15 项；当前发布与回退资料见 [运维增强发布记录](operations-center-deployment-2026-10-24.md)。
