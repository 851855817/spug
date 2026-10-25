# 宿主机平台诊断与受限启动

`spug-health.py` 只依赖宿主机 Python 3 标准库、Docker CLI 和 Linux `/proc`。不导入 Django，不需要数据库账号；Web、API、数据库不可用时，仍可通过 SSH 检查平台本身。默认只读，不创建报告或锁，不连接客户服务器。

## 安装与 SSH 诊断

将本目录的脚本复制到宿主机，保持目录由可信管理员管理。例如从受控发布目录安装：

```sh
sudo install -d -m 700 /opt/spug/platform-health
sudo install -m 700 deploy/platform-health/spug-health.py /opt/spug/platform-health/spug-health.py
sudo python3 /opt/spug/platform-health/spug-health.py
```

SSH 示例（替换服务器别名）：

```sh
ssh your-spug-host 'sudo python3 /opt/spug/platform-health/spug-health.py'
```

运行账户需有读取 `/opt/spug/backups` 和调用本机 Docker 的权限；这些权限只留给宿主机管理员。**不向 Web 容器挂载 Docker socket，不授予 Web 用户 Docker/sudo 权限，也不让 Web API 执行本脚本。** Web 只能读取下文复制进去的脱敏报告。脚本无需增加 Docker socket、远程 Docker API 或任何网络服务。

固定检查范围：

| 检查项 | 范围 |
| --- | --- |
| 容器 | 仅 `spug`、`spug-db` 的 State.Status、State.Health.Status、State.OOMKilled、RestartCount |
| Supervisor | 仅 `nginx`、`redis`、`spug-api`、`spug-monitor`、`spug-ops-worker`、`spug-panel-worker`、`spug-scheduler`、`spug-worker`、`spug-ws` |
| HTTP | `http://127.0.0.1:8080/api/account/login/`；仅检查状态码，忽略代理设置、不跟随重定向、不读取正文 |
| 磁盘 | `/opt/spug` 所在文件系统的可用空间和 Inode；容器持久数据卷由 Web 侧探针另行检查 |
| 内存 | `/proc/meminfo` 的 MemTotal、MemAvailable |
| 备份 | `/opt/spug/backups` 下最近一次符合命名与配对文件约束的备份尝试 |

不读取 Docker 环境、完整 inspect 配置、数据库记录、原始日志、异常正文、密钥或客户数据。Docker 命令不用 shell，单次超时 4 秒，标准输出上限 16 KiB，标准错误不进入报告。磁盘/内存剩余比例低于 15% 为 warning，低于 5% 为 error；磁盘同时考虑空间与 Inode。

JSON 包含 `checked_at`（UTC）、`status`、`checks`、`limitations`。每条检查只含 `id`、`name`、`status`、`summary`、`advice`；状态为 `ok`、`warning`、`error`、`unknown`。退出码：0=ok，1=warning/unknown，2=error。非零退出码仍会输出完整安全 JSON，不能据此丢弃报告。进程 RUNNING、容器健康或 HTTP 200 都不能证明业务任务成功。

## 配对备份检查

部署的配对备份包含同一目录内的 `database.sql.gz`、`spug-data.tar.gz`、`SHA256SUMS`。目录名须以 `YYYYMMDD-HHMMSS` 开头，后缀只允许字母、数字、点、下划线、连字符，例如 `20261024-120000-before-platform-health`。不把 metadata/UI 备份当作数据库与完整数据卷的配对备份。

按符合该格式的目录名排序，选择含任意一个配对文件的最新尝试；新备份不完整时明确 warning，不回退到旧备份假装最新备份完整。默认只确认三个文件均为非空普通文件，报告明确标记 **hashes are not verified**。这不是备份恢复验证。

可显式进行流式摘要核验：

```sh
sudo python3 /opt/spug/platform-health/spug-health.py --verify-backup
```

清单只接受两行 SHA-256，文件名必须恰为 `database.sql.gz` 与 `spug-data.tar.gz`，不允许绝对路径、`../`、`./`、额外文件、重复项或任意脚本。清单最多 4 KiB；逐文件按 1 MiB 块哈希，总预算 120 秒。拒绝符号链接、硬链接和非常规文件；Linux 下使用目录描述符与 `O_NOFOLLOW` 限定读取位置。不会解压、导入、执行或恢复任何内容。

摘要匹配只能说明文件与当前清单一致，不代表备份来源可信、压缩内容有效、业务一致、密钥可解密或恢复演练成功。真正恢复需在独立隔离环境验证，不能直接覆盖生产数据。

## 手动启动单一已停止目标

仅在 SSH 管理员已诊断原因并明确选择目标后使用：

```sh
sudo python3 /opt/spug/platform-health/spug-health.py --start spug
sudo python3 /opt/spug/platform-health/spug-health.py --start spug-db
sudo python3 /opt/spug/platform-health/spug-health.py --start spug-panel-worker
```

每次仅接受两个固定容器名或九个固定 Supervisor 服务名之一，包含独立运维作业进程 `spug-ops-worker`。宿主机 CLI 可显式启动 Redis；Web 恢复操作的允许范围由 Web 后端另行限制，不包含 Redis。

脚本通过 `/opt/spug/.platform-health.lock` 独占锁防止自身并发启动，并在锁内读取新状态：

- 容器只允许 `created` 或 `exited`；拒绝运行中（包括 unhealthy）、paused、restarting、removing、dead 和未知状态。
- 服务要求应用容器运行，且服务为 `STOPPED`、`EXITED` 或 `FATAL`；拒绝 RUNNING、STARTING、STOPPING、BACKOFF 与未知状态。
- 只调用固定参数的 `docker start <容器>` 或 `docker exec spug supervisorctl start <服务>`，随后重新核实状态。超时、错误或尚未 RUNNING 都提示核实；不会重发命令。

锁不约束其他管理员直接操作 Docker/Supervisor；应避免同时手工维护。若 SSH 进程被强制终止留下锁，先核实已无该操作和相应命令在运行，再由管理员处理锁。正常诊断不受锁影响。

不提供 restart、启动所有服务、自动修复、容器删除/重建、Compose up、数据恢复或备份覆盖操作。脚本不会创建定时器。

## 向平台页面提供脱敏报告

报告写入是显式动作；使用同目录临时文件、600 权限和原子替换：

```sh
sudo python3 /opt/spug/platform-health/spug-health.py --write-report
```

产物固定为 `/opt/spug/platform-health.json`。可显式追加 `--publish-report`，在写入成功且重新确认应用容器运行后，仅将该 JSON 复制到固定容器路径：

```sh
sudo python3 /opt/spug/platform-health/spug-health.py --write-report --publish-report
```

`--publish-report` 必须与 `--write-report` 同用；写入失败时不会复制旧文件。容器未运行或状态未知时不复制，也不启动容器。等价的宿主机手工复制命令为：

```sh
sudo docker cp /opt/spug/platform-health.json spug:/data/platform-health.json
```

若需要同时校验摘要，可组合 `--verify-backup --write-report`。即使检查返回非零退出码，报告仍可能已成功写出，先检查文件，不要通过 `&&` 假设只有全部健康才有报告。写入失败会在标准输出追加安全错误项。

复制只发布脱敏诊断数据，不授予 Web 额外权限。Web 后端读取固定 `/data/platform-health.json`，只信任约定字段；报告超过 10 分钟会标记过期。部署时可由宿主机管理员每 5 分钟运行 `--write-report --publish-report`，定时执行只允许采集与发布报告，不能包含 `--start`。复制时间不等于检查时间，必须以 `checked_at` 为准。脚本本身不安装定时任务、不自动恢复。

Web 侧报告校验增量要求 `checks` 为 1—32 项，每项具有唯一且受限的 id 与合法状态；内部状态按宿主机生产器的 `error > warning > unknown > ok` 聚合，并与报告总状态一致。矛盾或畸形报告返回 unknown，不输出原字段或异常文本。该校验保留最小报告兼容，不证明检查项完整或报告来源可信。具体修复版本及是否上线以[夜间进度](../../docs/superpowers/plans/2026-10-25-overnight-progress.md)为准，不能把仓库新增规则当作现网已经具备。

## 安装只读采集定时器

本目录提供 `spug-platform-health.service` 和 `.timer`；每 5 分钟运行一次，最多 75 秒、128 MiB 内存和 20% CPU，不包含 `--start`，不自动恢复服务。报告中 warning/error 对应的退出码 1/2 由 service 视为已完成检查；实际健康状态仍以报告内容为准。

```sh
install -m 644 deploy/platform-health/spug-platform-health.service /etc/systemd/system/
install -m 644 deploy/platform-health/spug-platform-health.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now spug-platform-health.timer
systemctl start spug-platform-health.service
```

停用采集：`systemctl disable --now spug-platform-health.timer`。独立诊断脚本仍可手工使用；停用不会清除报告，页面按原检查时间判断是否过期。

## 本地测试

无需 Docker、数据库、网络或目标服务器。测试通过注入执行器、临时备份和真实的本地 Python 子进程验证边界：

```sh
python3 -m unittest discover -s deploy/platform-health/tests -v
```

覆盖只读默认、固定命令与 inspect 字段、状态拒绝、锁、超时不重试、异常脱敏、阈值、配对文件、最新不完整备份、清单路径攻击、链接限制、摘要不匹配、原子报告写入与固定报告发布。原生符号链接测试在未授权 Windows 账户上会跳过；可运行的链接元数据与硬链接测试仍覆盖拒绝逻辑。
