# 面板任务整合发布记录

2026-11-04 14:24（UTC+8）发布应用 `e6ee4e3a5831ff79b9aebe56123ddbb3e7b34839`，此前版本为 `0e5edaf2dbb639a185e0def29291b469641c5d10`。沿用 `feature/operations-reliability` 与草稿 [PR #4](https://github.com/851855817/spug/pull/4)，不合并主分支。

## 使用入口

左栏“批量执行 → 面板任务”，顶部“任务类型”选择：

- **宝塔操作**：保留宝塔 API 操作、上传、冻结预检、执行确认和执行记录。
- **主机巡检**：原运维作业，包括 Linux 基础巡检、PHP 配置检查和 Web 服务检查；“执行记录 → 查看结果”显示正常／异常、原因和检查数据。

原 `/exec/operations?run_id=...` 自动转到 `/exec/panel?type=inspection&run_id=...` 并恢复结果。网站／SSL 链接继续保留参数。巡检仍仅超级管理员可用，宝塔查看、创建和执行权限保持独立；进入页面与切换类型不创建任务。

离开宝塔任务时关闭继续／证书部署／取消确认框，旧回调不能提交，实际提交前重新检查动作权限。该问题由独立审查复现，新增五条回归先失败后通过。

## 验证

- 前端 **49 套／477 项**通过，目标 ESLint 通过，生产构建成功；保留两处既有 Hook 提示。
- 最终制品 Linux 隔离后端 **682 项**通过（11 条件跳过），发布保护 **197 项**、到期保护 **10 项**、SSL 发布保护 **34 项**通过，候选 `post-ssl` 镜像检查 **6 项**通过。所有检查使用隔离数据库或模拟接口。
- 实际 Chrome 临时配置完成 **10 条**验收，覆盖唯一侧栏入口、类型 URL、原巡检结果、前后导航、旧链接、SSL 预填和深浅主题／窄屏。全部请求为 loopback 模拟 GET，未提交任务；[报告](2026-11-04-panel-workspace-browser.json)、[巡检页面](panel-workspace-shots/02-inspection-create.png)、[深色结果](panel-workspace-shots/05-dark-compact-result.png)。报告的应用版本与静态文件哈希已与最终发布包核对。
- [应用 CI](https://github.com/851855817/spug/actions/runs/41784811110) 和 [PR CI](https://github.com/851855817/spug/actions/runs/41784816981) 的前端、后端与 MariaDB 三作业均成功。独立审查无剩余阻断项。
- [候选隔离收据](2026-11-04-panel-workspace-isolation.json) 与 [独立上线核验](2026-11-04-panel-workspace-publication.json)。

## 发布与保全

固定 `panel-workspace-ui` profile 只允许 routes、panel/index、panel/Workspace、panel/TaskDetail、operations/index 五处 UI 变化；镜像没有 API 覆盖，数据库与迁移不变。精确校验候选及原镜像完整 API 文件树、静态文件收据、既有进程、compose 与迁移历史。

发布前重新核对版本、业务空闲、资源及现有 SSL 数据，建立新数据库与文件配对备份：`/opt/spug/backups/20261104-142450-before-upgrade-e6ee4e3-67422c02`。69 个保全摘要项发布前后一致，两个 API 文件树（247／287 文件）保持一致；现有 4 个 SSL 来源、11 条证书、2 条任务及原开关／提醒保留。9 服务、15 项健康检查、健康定时器、内网和公网 HTTPS 200 均经独立复核。

全部六张 SSL 表纳入摘要。仅规范化空令牌、无任务绑定且已过期的 `worker` 租约时间，避免无任务时每 10 秒更新时间导致假变化；租约行、其他字段和所有业务数据仍校验。在途作业、有效租约或 15 分钟内即将到期的周期采集会阻止发布，不修改用户监控开关。真实隔离内存数据库中六表均非空的审查验证：空闲 tick 摘要稳定，业务内容变化仍能检出。

首个未发布候选因归档缺导航源码导致静态断言失败，补齐测试源码后重新打包并完成上述全部检查；未覆盖或停止当时正常运行的版本。独立上线核验处理构建中中文字符串转义后通过，未修改应用。

本次没有执行客户升级、安装、续签、远程命令或主动采集，也未修改客户监控／提醒设置。已知 Python 3.6.8 的 SSL Nginx 解析兼容问题属于另一项修复，本次未捆绑或声称修复。原版权与许可证保持。

使用说明：[运维作业指南](../operations-center-guide.md)、[平台使用手册](../spug-platform-user-manual.md)。直接打开：[主机巡检](https://47.104.88.147/exec/panel?type=inspection)。
