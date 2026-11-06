# 账号资源权限的附加迁移与回退准备

此文件说明独立发布合同 `account-resources-v1`。发布入口为 `deploy/account-resources/host.py`，不套用旧 SSL 或只替换前端的发布配置。准备、绑定、镜像检查都不会发布生产。

## 精确附加范围

只允许新增以下 9 张表及对应外键／约束，不修改或删除既有业务字段：

| 应用 | 表 |
| --- | --- |
| account | account_resource_quota_lock |
| account | account_resource_policies |
| account | account_resource_ownerships |
| account | account_resource_grants |
| account | account_resource_group_owners |
| account | account_resource_audit |
| alarm | alarm_resource_scopes |
| notify | notify_resource_audience |
| deploy | deploy_resource_uploads |

`apps.account.resource_migration.ADDITIVE_MODELS` 是对应模型白名单。迁移生成测试要求仅出现这些模型的 CreateModel／AddConstraint；MariaDB 演练检查既有全部表的字段、约束与样例数据保留，旧 ORM 能继续读取原表。

迁移已经固定为 account、alarm、deploy、notify 各一个 `0002_account_resources.py`，共 9 个 CreateModel 和 8 个 AddConstraint。`migration-plan.json` 固定依赖、操作和源码 SHA，`production-baseline.json` 固定实际生产的 24 个迁移节点与 67 张表。隔离生成结果必须与这四份文件逐字节相符。生产只运行 `migrate --noinput`，不运行迁移生成。

## 旧数据影响清单

上线前影响清单通过独立只读查询或隔离副本核对；当前旧版尚无此命令和新增表，不能直接在旧版执行。以下新命令用于维护期间完成四份附加迁移后、显式初始化前，只读生成完整影响清单：

```sh
python manage.py resource_permissions > resource-permissions-impact.json
```

输出仅含账号／资源 ID、创建者、显式归属、既有组授权冲突、确认关联、净数量、计划目标影响与在途记录 ID；不包含 IP、账号名、口令、密钥、脚本或任务输出。该命令不创建策略、授权或拒绝审计。

全量备份、迁移验证、影响清单和业务空闲复核通过后，最终合同才可显式调用：

```sh
python manage.py resource_permissions --apply --actor-id <活跃管理员ID>
```

此步骤仅给尚无策略的旧普通账号建立“上限＝保留资源净用量”，包含停用账号；保持现有明确策略，重复运行不覆盖。既有 created_by 不变，不自动共享全站宝塔，不把组权限静默转换为资源授权。新账号仍默认 0。策略初始化和审计在同一事务内完成。

旧普通创建者未保存独立 SSH 密钥的主机单列影响 ID。只读清单不会取用或复制全局私钥，也不会验证客户连接。需要显式补充独立凭据后再使用。

## 配额事务与验证

所有资源图变更先锁定稳定配额锁，再在锁内重算受影响账号并按 ID 顺序锁账号行，最后验证净增量；分配、转移、删除和解除关联都复用该规则。账号编辑先获取同一锁，避免反向锁序。不在配额事务内进行网络连接。

本地 MariaDB 专用配置 `spug.mariadb_test_settings` 仅连接 127.0.0.1，数据库名称要求以 kops_resource_test 开头，配置从 `RESOURCE_TEST_DB_CONFIG` 读取。测试不读取生产数据库或客户接口。

验收包括两个连接抢最后名额、并发分配、解除关联与新增分配交错、反向归属转移、失败回滚和附加 DDL。SQLite 的通过结果不替代这些测试。

## 持久化拒绝审计

拒绝日志独立于数据库事务追加，避免额度回滚丢失记录。生产进程设置 `KOPS_RESOURCE_AUDIT_LOG=/data/logs/resource-access.log`，目录保存在持久卷；使用 `deploy/account-resources/resource-audit.logrotate` 的轮转配置。日志只记录 ID、动作码、时间及安全元数据。文件默认权限 0600，父目录新建为 0700。

宿主机 `/etc/logrotate.d/kops-resource-audit` 的实际日志路径从已确认的容器 `/data` 持久卷推导，不假定宿主机也使用 `/data`。当前为 `spug_spug-data` 本地 Docker 命名卷；发布合同固定其名称、可写挂载和实际目录，并另外核对 volume inspect 的本地驱动、挂载点、范围及空选项。拒绝来源漂移、子挂载遮蔽、路径穿越或符号链接。维护前保存既有轮转配置与属主／权限；配对备份完成后安装 root:root、0644 的精确配置，用 `logrotate --debug --state /dev/null` 校验，不执行轮转。每日轮转，保留 14 份、延迟压缩、0600 新文件；回退保留日志和此配置。已有同名配置内容不符或为符号链接时，预检拒绝进入维护。

## 候选绑定与证据

先提交最终发布工具，冻结完整候选 SHA，再用该候选构建前端。以下变量指向独立目录，`STAGE` 必须尚不存在；`HISTORY` 只包含已验证 SHA 的生产迁移源码，不含凭据或业务数据：

```sh
python deploy/account-resources/scope.py --output "$SCOPE"
python deploy/account-resources/prepare.py --history-root "$HISTORY" --output "$STAGE" --scope "$SCOPE"
python deploy/account-resources/stage.py --prepared "$STAGE" --scope "$SCOPE" --build "$BUILD" --catalog "$PUBLIC_CATALOG"
```

`scope.json` 固定相对生产的 157 个运行时文件（104 API、53 UI）、全量候选与旧 API 源码及发布工具 SHA。`stage.py` 要求候选和发布工具已提交且干净，生成精确热覆盖、完整候选镜像源码、完整旧版回退源码、45 个迁移文件／包标记、两个 Dockerfile 与所有文件摘要。不同 API 根原本存在的附加文件独立保存，不强迫两个根相等。公共 API 目录仅允许已观测路径 `apps/baota/data/official_api_catalog.json` 的精确 CRLF 原件 SHA `4ae982653156d0b46c1885fdba4d50c47cb58cc31188214ae4ad97b0d831ddf8`；无通用换行豁免。

历史迁移源码也分别记录两个根的实际存在状态与 SHA：运行目录 `/data/spug/spug_api` 包含全部 24 份已应用迁移，原始 `/opt` 目录只保留其中一份 SSL 迁移。预检精确验证这一区别，新增或缺失原件都拒绝；不在热覆盖时补写 `/opt` 的旧迁移。候选与关闭回退镜像均另外保留完整迁移历史，供未来容器启动使用。

真实 MariaDB 演练在本地独立容器完成；数据库仅允许 `baota-ci-db/baota_ci`，最终发布演练使用与现网一致的 MariaDB 10.11.19、utf8mb4 和 utf8mb4_unicode_ci。将最终 stage 的 `source/spug_api` 挂为工作目录，将 stage 挂为 `/prepared`，将其 `source/deploy/account-resources` 挂为 `/utilities`，用包含实际依赖的固定运行镜像执行 `/utilities/rehearsal.py`。演练先创建 67 表／24 迁移与非空合成旧数据，再增加 9 表／4 迁移，验证旧数据与历史、只读影响清单、普通／停用账号初始化、既有策略保留、幂等、旧历史 ORM 读取和不完整迁移拒绝。收集实际成功进程打印的完整 proof；`/tmp/resource-rehearsal.json` 位于 tmpfs 时会随容器停止消失。其中 `revision` 必须是最终 SHA、`provisional` 必须为 false。临时数据库不放在生产主机。

两个发布镜像从实际生产 `spug-baota:d48d73b` 构建，不能把页面 SHA 前缀当作旧镜像标签。固定旧镜像 ID 为 `sha256:752af29ea640270dc857178beebdde7a2ff8ac25ad1016b139f49e4e79388ce6`。逐个镜像执行以下检查；只挂探针，无源码挂载、网络或数据库：

现网 Compose 另固定为 `spug-baota:4739172`。它与当前容器的镜像来源分开校验；只替换这一条明确匹配的 Compose 镜像记录，数据库镜像和其他配置保持原件。

该基础镜像有 8 个服务段，现网后来增加了 `spug-ops-worker`。候选和回退镜像都先核验精确的旧 8 服务集合，再补入与仓库既有 `ops-worker.ini` 一致的公开配置，最后核验完整 9 服务；任何额外或其他缺失服务均拒绝构建。回退仅 Redis 可自动启动，不能因原镜像缺少该段而漏关执行器。

```sh
docker build -t "spug-baota:$SHORT_SHA" -f "$STAGE/Dockerfile" "$STAGE"
docker build -t "spug-baota:resource-rollback-$SHORT_SHA" -f "$STAGE/rollback.Dockerfile" "$STAGE"
docker run --rm --network none --read-only --tmpfs /tmp --memory 160m --entrypoint python3 \
  --mount "type=bind,source=$PROBE,target=/probe.py,readonly" \
  "$IMAGE" /probe.py "$MODE" "$IMAGE_ID"
```

`PROBE` 为已冻结的 `image_probe.py`；`MODE` 分别为 baseline、candidate、rollback，`IMAGE_ID` 来自该标签的 `docker image inspect`。候选和回退均真正导入安装目录的 Django、HTTP 路由与 ASGI；候选另导入九个资源模型。结果返回完整 API 文件树、真实 Supervisor 启动设置、入口迁移计数及候选审计路径，不能用布尔声明代替。

`checks.json` 保存 `release_sha256`、`stage_files_sha256`、`baseline_image_id`、`image_id`、`rollback_image_id`、完整 `rehearsal` 和 `image_facts`。后者包含上述三个探针结果，键名为 baseline、candidate、rollback。`stage.validate(stage, revision)` 逐项重算热覆盖／保留／回退文件集、历史迁移、Dockerfile、UI、工具及演练绑定，比较实际镜像文件树增量和关闭的回退启动设置。

上传后，在执行任何上传工具之前，使用独立受信的本地代码和本地保存的最终 `stage-files.json` 核对远端全部文件 SHA；同时核对外部持有的 `scope.json` SHA。尤其要核对将被执行的 `source/deploy/account-resources/*`、`source/deploy/upgrade-review/publish.py`、`source/deploy/inventory/release_safety.py` 与 scope 的发布工具 SHA。不能先导入上传的 `stage.py` 或 `host.py` 来证明其自身可信。随后只从已核验的 staged source 入口执行：

```sh
python3 /opt/spug/account-resources-$SHORT_SHA/source/deploy/account-resources/host.py \
  "$REVISION" "$ACTIVE_ADMIN_ID" "$TRUSTED_SCOPE_SHA256"
```

## 发布和兼容回退

重新核对现网 SHA、业务空闲、来源开关、计划、凭据可解密性和全部旧业务保全摘要；创建代码／数据库／密钥配对备份。附加迁移、初始策略和回退必须先在同版本隔离副本演练。

维护先暂停健康检查，持久化入口迁移 HOLD 与八个进程的 `autostart=false`，再停止 nginx、API、WebSocket、旧 worker、旧 monitor、panel worker、ops worker 和 scheduler；仅 Redis 保持原 PID。配对备份完成后，依次安装审计轮转、覆盖 API、运行四份迁移、验证附加结构、显式初始化普通旧账号、覆盖 UI、更新未来 compose，最后启动七个后端与 nginx。成功复验后恢复八进程的持久自动启动和原健康检查状态。

回退保留附加表和审计，不做反向 DROP，不修改资源创建者。先证明业务在关闭入口后未变化、全部附加表／迁移完整，再恢复各根的原件；只额外保留四个迁移文件。未来 compose 指向完整旧源码及完整迁移历史的关闭回退镜像。当前入口继续 HOLD、八服务继续关闭；容器普通重启也不能自动执行迁移或重新开放旧权限代码。DDL 仅部分完成、初始化不符或出现新业务时，不尝试降级恢复，保持维护关闭并记录失败步骤。备份目录记录实际发布与恢复结果。
