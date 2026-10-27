# 固定范围增量发布

这是已审查发布的专用工具，不是通用部署面板。全程由管理员在部署主机运行，不能作为客户批量任务模板导入。应用运行时只允许以下固定配置：

| 配置 | 允许覆盖 | 额外要求 |
| --- | --- | --- |
| `php-update`（默认） | `apps/baota/operations/php_update.py` | 保持原 PHP 发布合同；历史清单缺少配置时按此解释 |
| `host-report` | `apps/setting/health.py` | 基准 Git、暂存源码、准备镜像和实际两份 API 根目录的 PHP 模块摘要一致 |
| `operations-ui` | 仅运维比较页面的 UI 构建 | 前端运行差异仅允许 `RunDetail.js`；不覆盖任何 API。固定保全 PHP 策略与健康模块，新旧镜像完整 API 文件摘要一致，实际两份 API 根在发布前后保持 |
| `task-results-ui` | 运维作业、面板任务详情与项目、上传结果、原生命令／分发输出、任务计划详情、流水线输出共八个固定 UI 文件 | 精确校验八个运行文件集合，拒绝子集、额外 UI 或 API 变更；无 API 覆盖，沿用完整 API 保全、配对备份与恢复检查 |
| `inventory-filters` | `apps/baota/inventory/service.py` 及库存页面 UI | 必须恰好一份既有 listing 模块和 `spug_web/src/pages/panel-inventory/index.js`；旧模块摘要必须存在，额外固定保全 PHP 策略与健康模块。无迁移，沿用 API／UI 原始收据、完整运行文件核对、新配对备份及兼容回退 |
| `workbench-ui` | 首页、导航排序、监控嵌入与两个目标页，共九个固定 UI 文件 | 精确校验 `package.py` 的 `WORKBENCH_UI` 集合，拒绝子集、额外文件及 API 变更。API 覆盖清单为空，沿用完整 API 树保全、新配对备份与仅 UI 的兼容回退 |
| `panel-login` | `apps/baota/login_views.py`、`apps/baota/urls.py`，及宝塔列表／角色权限 UI | 新登录模块基准必须不存在，URLs 必须有旧摘要；保全 PHP 与健康模块。无迁移和权限数据更新。回退按原始收据删除新增模块并恢复 URLs，两份 API 根恢复完整原态 |

配置不能接收任意文件列表。未知配置、混合模块、模型／调度／设置变化会拒绝；纯前端配置也拒绝其他前端运行文件和空的目标改动。后端测试更新只供隔离回归，不进入镜像或覆盖线上文件。若本次修复超出该范围，应另行设计发布方案。

## 三个阶段

1. **本机打包。** 确认实际线上 revision，再从清洁的已审查分支运行。工具检查相对基准的运行时差异、重新构建 UI、规范化文本换行并产生 API／UI／辅助脚本清单和归档。不要手工修改暂存目录。

   ```sh
   python deploy/upgrade-review/package.py ACTUAL_PREVIOUS_40HEX --node /absolute/path/to/node --profile host-report
   ```

   归档位于仓库外层 `upgrade-NEW7.tar.gz`，摘要记录在同层 `upgrade-release.json`。健康配置只覆盖健康模块，PHP 摘要用于保全而不进入覆盖收据。

   发布本轮基线比较修复时，使用 `--profile operations-ui`。它的两份 API 覆盖清单必须为空，固定两模块保全内容从实际基准 Git 读取；生成的 Dockerfile 只复制 UI。

   发布本轮任务结果精简时，使用 `--profile task-results-ui`，同样只复制 UI，不调用 API 覆盖或恢复助手。

   发布首页监控、到期待办与导航排序时，使用 `--profile workbench-ui`。九个运行文件为 `pages/home/{index.js,Todo.js,index.module.less}`、`pages/server-monitor/index.js`、`pages/panel-inventory/index.js`、`pages/server-expiry/index.js`、`layout/{Sider.js,layout.module.less,navigationOrder.js}`，均相对 `spug_web/src/`。无模型或迁移，不调用 API 覆盖或恢复助手。回退恢复旧 UI；浏览器中的个人导航偏好可保留，旧版会忽略，新版也只应用实际获权路径。

   发布库存统计快捷筛选时，使用 `--profile inventory-filters`。镜像只新增覆盖既有 listing 模块及 UI；真实两份 API 只允许清单中的 listing 文件变化，其他所有运行文件必须与发布前快照一致。回退同时恢复 listing 与 UI，不反向迁移或改写缓存。旧前端仍可调用原筛选状态；回退后重新加载页面即可清除新状态选择。

2. **服务器隔离准备。** 将归档复制到 `/opt/spug/`，使用同一提交的 `prepare.py` 执行。必须填写实际新提交和本机归档摘要。准备脚本不修改生产业务数据。

   ```sh
   python3 /opt/spug/prepare.py NEW_40HEX ARCHIVE_SHA256
   ```

   工具核对归档路径／内容边界和基准镜像标签，无网络构建新镜像，核对实际镜像 API／UI 摘要，在只读、受限、无网络且不挂载生产数据的环境运行回归。回执绑定完整 revision、归档摘要及不可变镜像 ID。准备失败时保留现有服务，修正、提交后重新制作归档，不修改已校验产物来绕过检查。

   `operations-ui`、`task-results-ui` 和 `workbench-ui` 额外以不可变 ID 分别扫描基准与新镜像的 API 根，要求全部文件集合及摘要相同；沿用发布器的字节码、精确根日志排除规则，配置、近似日志名和额外文件仍受保护。准备回执保存基准镜像 ID 和完整 API 树摘要，结束前重新核对基准标签。不能用仅两个模块相同替代此完整检查。

3. **受控发布。** 完整 CI 和独立审查通过后，重新核对实际版本、资源和业务空闲。运行同一归档内的发布器：

   ```sh
   PYTHONDONTWRITEBYTECODE=1 python3 /opt/spug/upgrade-NEW7/source/deploy/upgrade-review/publish.py NEW_40HEX ACTUAL_PREVIOUS_40HEX
   ```

   发布器获取独占锁，重新检查归档、镜像、九服务、完整业务／模式／密钥和源码摘要。只停止六项必要入口，保留原容器及 Redis／旧 worker／monitor；完成新的数据库和完整数据卷配对备份后才覆盖。后续核对 API／UI、Compose、业务摘要和 HTTP，恢复原健康 timer 状态。旧备份或旧空闲检查不能代替本次检查。

   纯前端配置同样使用新配对备份和维护门，额外重新核对基准镜像 ID；只调用 UI 发布／恢复辅助脚本，不调用 API 覆盖或生成 API 覆盖收据。两份实际 API 根与各自发布前的完整快照相同，配置差异不会被新镜像的默认配置覆盖。

## 恢复和证据

新产物发布器在 `failure=preflight` 时另给出固定的 `preflight_check`，便于在仍正常的服务上定位下一项只读核查。旧归档可能只有总阶段，不补写或修改旧产物。

| `preflight_check` | 核查内容 |
| --- | --- |
| `revision` | 当前 HTTP 可达性及实际 UI revision 是否仍为预期基准 |
| `compose` | 原配置是否可读取、只包含一处预期镜像引用并通过 Compose 校验 |
| `services` | 原九项服务是否全部运行；先调查既有异常 |
| `business-idle` | 完整业务／模式／密钥摘要能否取得及核对，是否有在途任务、计划、锁或对外操作 |
| `process-identity` | 原容器、保留进程、supervisor 与健康配置能否完整核对 |
| `runtime-files` | 两份实际 API／UI 完整文件快照能否读取 |
| `runtime-baseline` | 已读取的版本、API 保全及静态文件是否满足本次基准与可恢复覆盖合同 |

这些名称仅定位检查阶段，不证明某一具体原因。响应不会包含异常、命令输出、凭据或客户信息；任何拒绝都须调查后重新完整核查。该字段不会自动重试、开放入口或放宽任何门；成功和其他阶段失败不携带这个字段。上轮首次拒绝的具体原因仍未复现，不能用新的离线分项测试推断生产原因。

备份保存于 `/opt/spug/backups/` 的本次专属目录，包含原 Compose／supervisor／健康配置、保全前态、数据库和数据卷。`SHA256SUMS` 只列固定的两个压缩备份，其他配置另有收据。发布结果及部分覆盖恢复按完整原始收据逐项校验，不能只看进程启动成功。

若新入口可能已经接收业务，回退前须重新关闭入口并证明原空闲窗口；任何新任务、业务／模式／密钥漂移或恢复证据缺口都会阻止自动降级和重新开放。不要盲目切换旧镜像或直接恢复旧数据库。生产整库恢复不在此工具的自动流程内，备份摘要通过也不代表整库恢复已演练。

导入暂存发布器做只读核查时也应设置 `PYTHONDONTWRITEBYTECODE=1`，避免生成未归档的 `__pycache__`，导致后续每字节绑定正确拒绝。发布日志和保全原始数据保留在受限部署目录，不上传公共仓库。

离线保护测试：`python -m unittest discover -s deploy/upgrade-review -p 'test_*.py'`。Linux 独占锁实进程测试在 Windows 条件跳过，最终须在 Linux 准备／CI 通过。

## SSL 续签诊断补丁

`ssl-renew-diagnostics` 配置仅接受既有 `apps/baota/operations/certificates.py` 和 `spug_web/src/pages/exec/panel/TaskItem.js` 的运行变更。保留 PHP 更新及平台健康模块的基线摘要，验证证书模块的旧摘要及全部 API 文件树；不包含模型、迁移或权限修改。准备、配对备份、API/UI 原始收据、业务空闲门和兼容回退均沿用现有发布流程。测试：`python -m unittest discover -s deploy/upgrade-review -p 'test_*.py'`。

发布空闲检查保留完整的 SSL 续签失败预览：仅当任务及目标均为 prepared、全部冻结子项与结果均为 failed、执行意图／人工判定为空、审计为空或只有唯一完整创建预检事件，且无远程结果或待核验请求时视为无待执行业务。所有任务仍纳入保全摘要；存在 ready 子项、缺失历史或其他非终态任务仍拒绝发布，任何并发提交均由阶段间摘要一致性检查阻止。
