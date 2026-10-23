# 2026-10-23 服务器监控发布记录

已部署源码 `d48d73b4dcbd2634e01b2d94423d4c0d36c358e5`，分支 `feature/server-resource-monitor`，基础分支 `feature/baota-panel-tasks`。开发记录保留于[草稿 PR #3](https://github.com/851855817/spug/pull/3)，未合并。

## 验证结果

- 本机后端 345 项：341 通过，4 项因 MariaDB／Linux／运行时条件跳过；前端 16 套件、130 项全部通过。修改模块 ESLint 无错误和告警，`git diff --check` 通过。
- [GitHub Actions](https://github.com/851855817/spug/actions/runs/39924296340) 三项 job 全部通过。固定 Linux 运行时后端 345 项，343 通过、2 项环境条件跳过；隔离 MariaDB 事务／并发 116 项全部通过，包括监控全局双槽和同目标互斥；前端 130 项及生产构建通过。
- 本机生产构建成功。保留原 `AutoDeploy.js` 的 `isAdmin` effect 依赖告警，未修改无关代码；Node／Paramiko 原有弃用提示不影响上述结果。
- 规格审查、代码质量审查通过。跨权限来源集合的失败计数、300→30 秒间隔切换、空列表自动发现均完成先失败后通过的回归。新增 HTTP 契约区分缓存采样年龄与失败／忙等待。
- 独立本机 HTTP／SSH 夹具验证实际子进程、认证、截止与回收；WSL BusyBox 实跑固定 Linux 探针。UI 夹具验证正常／失败／过期／待采集、双来源合并、磁盘展开、隐藏地址及桌面／窄屏，无客户连接。

## 发布产物

- 归档：`spug-monitor-d48d73b.tar.gz`，1,577,814 字节。
- SHA-256：`6c1be42726333dafd3324950ecf907fec3a537d2870c29c3e1b3691b02ea762e`。
- 正式镜像：`spug-baota:d48d73b`，镜像 ID `752af29ea640270dc857178beebdde7a2ff8ac25ad1016b139f49e4e79388ce6`。
- 兼容回退镜像：`spug-baota:monitor-rollback-d48d73b`，镜像 ID `2ba90b74ed94ba4d7c5debf3942a04e06bbcb4f5833a830b9d0af5d3285677f8`。
- 正式主资源：`main.27959b10.chunk.js`、`main.3e833d1b.chunk.css`。
- 服务器发布目录：`/opt/spug/releases/monitor-d48d73b`；执行日志为其中的 `release.log`。

前端仅在本机构建。服务器基于 `spug-baota:5437d77` 派生并复制源文件、静态文件，没有编译前端或安装依赖。该基线与旧运行容器 `2350559` 的 baota 后端 Python 文件总指纹一致，只有固化已热发布界面的镜像层不同。

## 迁移与上线验收

服务器先通过只读空闲预检，冻结入口后再次确认无用户任务、队列、PID、传输或客户外联。配套数据库、完整数据卷／密钥及 Compose／环境备份保留于权限受限的 `/opt/spug/backups/20261023-162729-before-resource-monitor`，校验和通过；不将备份内容或凭据提交 Git。

在禁止外联的临时 Docker 网络与克隆数据上，完成“新版 → 兼容回退版 → 新版”：三张表均存在、默认关闭；仅在克隆建立的禁用偏好标记经过两次切换保持，清理后恢复空表。每步原有记录和密钥指纹一致。演练使用受限临时资源，正式应用暂停期间执行，结束后清理克隆容器／卷／网络。

正式切换后，八个既有服务运行，容器 healthy、重启计数 0，HTTP 页面与主 JS 资源可访问；原宝塔、主机、模板、任务、设置及密钥指纹一致，原密码／API 密钥可解密。再次运行 `check.py empty` 确认无监控偏好、快照或租约；没有擅自开启客户监控或采样。

浏览器已确认 `/monitor/servers` 的空态与两处开启入口、两张列表的关闭开关以及原 `/monitor` 任务页。发布后资源快照：约 517 MiB 可用内存，2 GiB swap 未使用；此值仅代表当时的空闲状态，不构成开启监控后的负载保证。

## 界面证据

正式页面保持默认关闭：

![正式服务器监控空态](screenshots/monitor-empty.png)

以下为独立测试夹具，地址为文档保留网段，非客户监控数据：

![独立测试卡片](screenshots/monitor-cards-fixture.png)

## 回退

本次配套兼容回退镜像已实测。需要回退时先按空闲检查流程等待运行任务结束，将 `/opt/spug/docker-compose.yml` 的应用镜像切为 `spug-baota:monitor-rollback-d48d73b`，再以原卷、原环境运行 `docker compose up -d --pull never --no-deps spug`。八服务、HTTP、原数据指纹均需再次验收。保留新表和配置，不以旧镜像自动生成删表迁移，不自动覆盖数据库。重新启用新版使用正式镜像恢复。
