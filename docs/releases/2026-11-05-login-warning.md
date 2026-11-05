# 登录成功后直接进入后台

用户要求去掉截图中的真实 IP“安全警告”弹窗。认证成功后直接沿用原 `doLogin` 进入首页或原目标页，避免每次登录再点“知道了”。生产前端仅修改 `spug_web/src/pages/login/index.js`，删除该提示分支及无用引用。密码、MFA、LDAP、服务端 IP 绑定与失败提示保留；没有伪造 IP 标记、关闭校验或修改代理。

验证结果：旧代码登录回归 6 项中 4 项因弹窗阻塞失败，修改后 6 项全通过；完整前端 50 套／485 项、登录页 ESLint、生产构建通过。构建仅有其他既有页面的 Hook 依赖告警。发布保护 219 项通过，Windows 条件跳过 1 项；新单文件 `login-warning-ui` 的 8 项发布合同先失败再通过。独立只读审查未发现阻塞问题。

生产构建使用临时 Chrome 配置，在 loopback 模拟接口完成 14 条验收：无真实 IP 标记时直接进首页、LDAP 请求类型及原目标跳转、MFA 未验证不创建会话且缺验证码不重复提交、验证成功直接登录、错误认证仍停留登录页、深色窄屏。仅有 5 次模拟登录 POST 和已识别的缓存 GET，零客户请求。源码及主资产哈希记录在[浏览器报告](2026-11-05-login-warning-browser.json)；[登录后首页](login-warning-shots/02-home-without-warning.png)、[验证码仍必填](login-warning-shots/03-mfa-still-required.png)。测试后已停止本轮临时 fixture。

已于 **2026-11-05 10:29（UTC+8）**发布应用 `8e874e3a24eb93b40fca8841ae406097004a1da9`，替代 `340ead51eac670811f58b82ac9bc880e4a25e0f8`。正式发布前重新核对现网版本、9 服务、业务空闲与资源余量，配对备份为 `/opt/spug/backups/20261105-102939-before-upgrade-8e874e3-ad543504`。只发布 UI，API 覆盖清单为空，无数据库迁移或权限变更。

最终候选在服务器隔离环境通过后端 695 项（11 项条件跳过）、发布保护 219 项、到期发布 10 项、SSL 发布 34 项及 post-ssl 镜像 6 项。[应用 CI](https://github.com/851855817/spug/actions/runs/41955161298) 与 [PR CI](https://github.com/851855817/spug/actions/runs/41955166073) 的后端、前端和 MariaDB 三作业全部通过。

独立发布复核通过：69 项业务／模式／凭据摘要不变，SSL 六表原数据和开关保全；两份完整 API 树 247／287 文件逐字节不变，配对备份和 UI 原始收据有效，9 服务和 15 项健康检查正常，原保留进程身份一致、健康 timer active。公网 HTTPS、服务器 HTTPS、内网 HTTP 均为 200，正式主资产与浏览器验收资产哈希一致，原许可文件保留。见[独立核验摘要](2026-11-05-login-warning-verification.json)。

没有执行客户巡检、证书任务或远程命令，没有调整监控／提醒开关。继续保留 `feature/operations-reliability` 与[草稿 PR #4](https://github.com/851855817/spug/pull/4)，不合并主分支。
