# 运维增强发布记录 · 2026-10-24

应用代码版本：`2a2eb3621b8f5d83f62554017feb10a7a55c84ce`。开发分支：`feature/operations-reliability`。[草稿 PR #4](https://github.com/851855817/spug/pull/4) 基于现有 `feature/server-resource-monitor` 分支，未合并主分支。

## 交付范围

本轮完成已确认的四项：平台自身健康与受控恢复、巡检汇总与失败分类、分批执行与失败熔断、标准作业库与配置差异。平台健康第一阶段的历史记录见 [原发布记录](platform-health-deployment-2026-10-24.md)。

- **系统管理 → 平台健康**：15 项检查、9 个固定 Supervisor 服务；宿主机只读报告每 5 分钟采集。仅可确认启动允许列表内已经停止的服务，不自动恢复。
- **批量执行 → 运维作业**：仅超级管理员使用，目标为主机管理内的 SSH 主机，提供 Linux、宝塔 PHP CLI、Web 三种固定只读作业；支持递归分组、冻结目标、逐台结果、失败分类和 JSON 报告。
- **分批保护**：运维作业与现有面板任务支持每批 1–10 台、失败阈值、批后暂停与人工继续；结果不明时不自动重发。旧面板任务默认空策略，保持历史执行方式。SSL 两阶段任务只在只读核验后允许明确确认当前证书部署阶段，不顺带放行其他目标。
- **配置基线**：从成功采集创建不可变基线，仅比较同作业、版本与参数的固定字段；区分相同、变化、缺失与未知。首版不比较任意文件，不自动修复配置。

完整用途、操作步骤与限制见 [运维作业使用指南](operations-center-guide.md)。

## 上线验证

已在现有 Spug 实例发布，HTTP 返回 200，15 项平台健康检查均正常，9 项服务均 RUNNING。新增 `spug-ops-worker` 独立处理运维作业。

- 发布前确认队列与业务空闲，暂停 nginx、API、WebSocket、面板工作进程与调度器维护，完成备份、增量迁移和资源发布后恢复。
- 应用容器 ID、重启次数保持不变；原生执行工作进程、监控工作进程及 Redis 的 PID 保持不变。
- 前后核对旧业务字段、凭据可解密状态及密钥文件摘要，结果一致。既有面板任务 `execution_policy` 均为空，新运维四表均为空。
- 只读接口核对三种作业、空记录与基线、非超管读写拒绝、新增字段及 15 项健康结果。
- 浏览器确认新菜单与三个标签页、空执行记录、PHP 7.4 和 8.0–8.5 选择、无目标禁止提交、下发前确认弹窗及健康 `0 / 15`。确认弹窗已取消，未创建测试作业。
- **未向客户服务器下发巡检、升级或配置变更；未写入生产演示数据。**

## 自动验证证据

| 验证 | 结果 |
| --- | --- |
| 本地后端全量 | 运行 420 项，5 项按运行条件跳过，无失败 |
| 服务器隔离运行时全量 | 运行 420 项，3 项按运行条件跳过，无失败 |
| 前端全量 | 21 个套件，202 项全部通过 |
| Linux 宿主机 CLI | 22 项全部通过；本机 Windows 原生符号链接检查按环境跳过 |
| 镜像接线 | 4 项全部通过，兼容回退镜像的旧服务与迁移启动方式检查通过 |
| MariaDB 事务回归 | 117 项全部通过，包含运维并发领取、面板分批、上传和 SSL 阶段保护 |
| 迁移演练 | 复制现网迁移历史至临时 MariaDB，旧测试记录升级后保留，无待生成模型变化 |
| 固定探针 | 隔离 Linux、Web 与 PHP CLI 实际执行通过；PHP `display_errors` 的 `stderr/stdout/2/off` 均正确分类 |
| 生产前端构建 | 通过，仅保留原有 `AutoDeploy.js` Hook 依赖警告 |
| GitHub Actions | 应用提交的 push 与 PR 两次检查全绿，backend、frontend、mariadb 均通过 |

隔离容器限制单 CPU、256 MiB，不挂载生产业务数据卷。迁移演练只复制迁移源码，不复制生产配置、凭据或业务行；临时数据库与网络已清理。后端保留依赖 Paramiko 的 TripleDES 弃用警告。

服务器验证日志：`/opt/spug/operations-2a2eb36-checks.log`。应用提交 CI 记录：`40087315895`、`40087312554`。

## 发布与备份

发布日志：`/opt/spug/operations-2a2eb36-release.log`。发布包：`/opt/spug/operations-2a2eb36.tar.gz`，SHA-256：

```text
a1440fe7731a012b90b53c866f5f2ca0fcefbee94d4850a638196cc0e647b641
```

成对私有备份：

```text
/opt/spug/backups/20261024-185309-before-operations-2a2eb36/
```

含 `database.sql.gz`、`spug-data.tar.gz`、`SHA256SUMS`、原 Compose、Supervisor、宿主机诊断脚本及前后保全摘要、`publication.json`；文件和目录权限为 600/700。gzip/tar 结构检查通过，并在上线后手工执行 `sha256sum -c SHA256SUMS`，两份均为 OK。**摘要核对不代表已进行备份恢复演练。**

容器内 API 与静态入口备份分别位于：

```text
/data/spug-api-backups/2a2eb3621b8f5d83f62554017feb10a7a55c84ce/
/data/spug-ui-backups/2a2eb3621b8f5d83f62554017feb10a7a55c84ce/
```

新镜像 `spug-baota:2a2eb36`，短 ID `f37969be6740`；Compose 已指向该镜像，保障未来重建包含新功能。当前容器沿用原 `spug-baota:d48d73b` 容器，代码与资源已热更新。兼容回退镜像 `spug-baota:operations-rollback-2a2eb36`，短 ID `32adc411fa22`，用于保留新增 schema 时回退旧功能。

本项目沿用运行时生成迁移的方式，本次已应用：

```text
/data/spug/spug_api/apps/baota/migrations/0005_paneltask_execution_policy.py
/data/spug/spug_api/apps/ops/migrations/0001_initial.py
```

## 回退边界

本轮未执行生产回退。发布工具和失败恢复机制见 [部署工具说明](../deploy/operations/README.md)。这些工具针对本实例，不能作为任意实例的一键安装或恢复器。

新增表与字段必须保留；禁止反向迁移、删除运维记录或用旧模型自动生成删字段迁移。兼容回退镜像使用 `migrate-only` 启动方式，避免重建时按旧模型生成破坏性迁移。

人工维护前需确认任务空闲、互斥状态和实际 schema。若迁移结果无法确认，保持入口关闭并核实数据库；不能仅恢复入口就宣称回退完成。数据库和数据卷恢复是独立维护操作，应先核对成对备份与后续业务写入，不由本工具自动执行。
