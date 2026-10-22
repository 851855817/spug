# 宝塔隔离面板验证记录

日期：2026-10-22。此记录持续随实现更新，静态源码依据见同目录接口调研记录。

## 隔离边界

服务器上的临时容器 `baota-task-lab`，无主机目录或生产数据卷挂载；512 MiB 内存、0.75 CPU，8888 仅映射主机回环地址 18888。使用官方完整面板包、13.1.0 更新包、官方 Python 3.13.14 bundle，按官方 BT-Panel 的步骤选择插件加载器。临时 API 密钥仅保存于容器，不写入源码或日志。

未执行完整安装器，也未开启面板的域名/IP 证书自动申请。HTTP 服务使用官方 `runserver.py`，仅作隔离协议实验。实际客户面板没有接收测试变更。

## 已观察结果

| 请求 | 观察 | 证据限制 |
|---|---|---|
| `/system?action=GetSystemTotal` | HTTP 200，返回 version=13.1.0、Linux 系统信息 | 连接测试依据；不表示变更接口已全部验证 |
| `/data?action=getData`，table=sites | HTTP 200，data 数组 | 初始无网站 |
| `/data?action=getData`，table=tasks | HTTP 200，data 数组；字段 id/name/type/status/addtime/start/end | 与 task_list 不是同一个表 |
| `/plugin?action=get_soft_list` | HTTP 200，条目在 list.data，返回分页信息及 versions | 顶层包含敏感字段，应用必须按允许字段提取 |
| `/system?action=upgrade_panel`，完全省略 force | HTTP 200，local.version=13.1.0、cloud.OfficialVersion.version=13.1.0、upgrade=0 | 仅执行检查，未执行面板升级 |
| `/plugin?action=install_plugin`，nginx 1.24.0 | HTTP 200、status=true，tasks 新增两条记录 | 初始仅证明入队；安装结果仍需验证 |
| `/site?action=AddSite` | 创建隔离 `.example.test` 纯静态网站，读回网站条目 | 使用容器已有 Debian Nginx 1.22.1，配置到宝塔预期目录；不是宝塔软件商店安装成功的证据 |
| `/site?action=GetRewriteLists` | 返回该网站 id/name/file 和 31 个模板 | site_type=PHP；路径由服务端解析 |
| `/site?action=SetRewriteLists` | 完整文本成功应用；故意无效的 Nginx 指令返回逐网站失败，读回保留上一条正确规则 | 两项真实断言通过；仅 Nginx 场景 |
| `/site?action=GetSSL` | 未开启 SSL 返回 HTTP 200、status=false、type=-1，仍有 domain/index 等状态字段 | 须按操作解释，不能一概视作认证失败；key/csr 不得透传 |
| `/site?action=multiple_basedir`、`GetDirUserINI` | 先关闭再开启，逐次读回 userini=false/true，断言通过 | 验证网站配置文件层面；未验证实际 PHP 请求的目录限制 |

## 发现的官方差异

- GetSystemVersion 方法不接受路由传入的参数，在本版本返回 500；连接检查使用 GetSystemTotal。
- 初始缺少 PluginLoader.so 时软件接口返回 404；按官方启动逻辑选用随包提供的架构加载器后正常。没有修改或绕过面板权限校验。
- 更新包解压后的 BT-Task 无执行权限；补齐官方启动器的 chmod 后才启动隔离队列。这也说明“入队成功”不能代替实际安装结果。
- 软件商店的 Nginx 安装任务在最小基础容器缺少编译工具时未安装成功；队列已处理也不能代替实际软件状态。为继续网站接口验证，独立配置容器已有 Nginx，未把这一步记为商店安装成功。

## 11.7.0 对照验证

另建 `baota-task-lab11`，256 MiB 内存、0.5 CPU、无主机或生产数据挂载，8888 仅映射主机回环地址 18889。使用同一官方完整包、11.7.0 更新包及官方 Python bundle；同样只用于协议实验。

- GetSystemTotal 读回 11.7.0；完全省略 force 的升级检查读回本地 11.7.0、正式版本 13.1.0、upgrade=1，没有执行升级。
- 创建隔离 `.example.test` 静态网站并读回；使用容器已有 Debian Nginx，与 13.1.0 实验边界相同。
- GetRewriteLists 返回网站身份及 31 个模板。完整文本应用后读回一致；故意无效的 Nginx 指令被拒绝，原规则保留，两项断言通过。
- multiple_basedir 关闭、开启后分别读回 userini=false、true；GetSSL 未配置状态同样返回 status=false、type=-1。

上述结果证明这几项请求及读回结构在两个版本上相符，不代表其他操作或所有发行版已经完成实测。

## Spug 实际适配代码的 HTTP 集成

将开发中的 PanelClient、resources 和操作适配器只读打包到隔离容器内，使用临时面板 API 密钥在容器内部访问回环地址，没有使用生产 Spug 数据库或客户面板配置。每个变更调用之前先独占创建、写入并 fsync 本地意图记录，重复执行脚本会被拒绝。

11.7.0 与 13.1.0 均已通过以下实际代码断言：

- get_resources 完成真实分页，分别读到 288、295 个软件条目，读取网站、静态 PHP 和 Nginx 类型；仅输出允许字段和数量。
- 防跨站关闭、开启返回 success，并读回实际开关。
- 创建第二个隔离网站返回 success，读回网站、域名、目录和 PHP 类型。
- 正确伪静态规则返回 success；无效规则返回 failed、rollback_verified=true，读回摘要与上一条正确规则一致。

软件目录随面板云端变化，条目数量是当次观察值。上述脚本先于最后适配器审查运行；最终固定提交还需执行回归测试。

## 本地签名 HTTP 联调

在本机回环地址启动受控状态面板，用实际 PanelClient 发送签名 POST，服务器校验时间戳与双 MD5 签名后将参数交给无凭据协议夹具。八类九个操作码分别完成预检、下发、读回；SSL 申请和续签均单独经过签发与部署阶段。

在 `76e1186` 回归时共 160 个 HTTP 请求、11 个变更请求，每次 dispatch 最多一次变更，reconcile 未产生变更。软件安装只有在夹具同时变更实际安装版本与任务结束状态后通过；扩展同时核对配置与模块加载。输出和预览未含预置敏感标记、私钥或 PEM。该夹具最初未模拟宝塔签发时的自动证书同步，不能用于证明 SSL 变更仅影响所选网站；这一差异需修正适配器及测试后重新验证。

这是受控协议集成，不是宝塔生产环境或真实 CA 签发证据。原始 TLS、断连、慢速响应和请求不重发由连接测试另行覆盖。

## 当前运行限制

软件生命周期实验另向 13.1.0 隔离容器下发一次 Redis 7.2.16 安装，观察到 tasks 中 `安装[redis-7.2]` 从 0 变为 -1，容器内编译继续进行。随后远程 SSH 握手持续超时，尚未核实最终实际安装版本，也未执行卸载，因此不记为安装成功。11.7.0 实际升级实验的脚本上传失败，未下发升级请求。生产 Spug 镜像仍未切换。

## 资源事件与恢复

用户通过云控制台重启服务器后，2026-10-22 17:01（UTC+8）SSH 恢复。前一次启动的内核日志记录 `baota-task-lab` 所属 cgroup 内存不足，编译进程 `cc1` 被 OOM 杀死；同一时段还出现 journald watchdog 重启。测试可能造成资源压力，但现有日志不能单独证明主机失联的全部原因。

两个实验容器均已停止，设置 `restart=no` 及 `pids-limit=96`，不再在此生产主机运行编译实验。重启后的 Spug、数据库容器健康，七个原有 Supervisor 服务运行正常，应用 HTTP 返回 200；原镜像未切换。本地 SSH 通道恢复到 `127.0.0.1:18080`。后续自动化验证使用本地进程或 GitHub Actions 的隔离环境。

## 尚待验证

PHP 扩展、卸载、SSL 签发/续签/部署和真实升级尚未在该隔离面板完成。后续结果应逐项追加，不将模拟测试或源码阅读记作真实操作成功。
