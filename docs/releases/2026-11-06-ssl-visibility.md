# SSL 过期证书逐条隐藏

应用 `ceba780a386aabcbed3b8f1344be96e98690fb0a` 于 2026-11-06 10:48（UTC+8）上线，替代 `f72d157`。入口：**监控中心 → SSL 监控**。

过期证书的操作列新增“隐藏”。隐藏后从当前账号的默认列表、统计和分页中移除；勾选筛选区“查看已隐藏”可查看并“恢复显示”。同一域名在不同服务器的记录可以分别处理。个人设置保存在原 UserSetting 表，刷新、重新登录和更换浏览器后保留，其他账号不受影响。

隐藏绑定当前证书指纹和到期时间，后续巡检发现换证后自动显示。请求携带当前周期并在本地事务中检查，防止旧页面请求隐藏新证书。隐藏不删除证书，不关闭巡检，不改变“不再提醒”；提醒仍可独立设置。

## 验证

后端新增回归先复现缺少接口及服务行为，前端先复现四项缺少隐藏交互。修复后完整本地后端 717 项（Windows 条件跳过 13 项）、前端 495 项通过。发布保护 246 项（Windows 条件跳过 1 项）通过。独立审查发现打包入口 tuple 展开问题，补完整 CLI 三 profile 回归先复现、再修复；8 项专用发布测试和独立复核均通过，无遗留阻断。

生产构建使用临时 Chrome 配置及 loopback 模拟接口完成 17 项验收：逐条隐藏／恢复、保留另一过期记录、刷新持久化、账号隔离、换证自动显示、提醒独立、失败保留、统计数量、深浅色和横向滚动。4 次写入均为模拟个人显示设置，客户请求为零。截图经人工核对，UI 源文件及主资产摘要与正式制品一致。

服务器隔离后端 717 项（11 条件跳过）、发布保护 246 项、到期发布 10 项、SSL 发布 34 项、post-ssl 镜像 6 项通过。[应用 CI](https://github.com/851855817/spug/actions/runs/42151998346) 和 [PR CI](https://github.com/851855817/spug/actions/runs/42152005080) 的后端、前端及 MariaDB 全绿。

## 发布及保全

`ssl-certificate-visibility` 精确覆盖既有 SSL service.py、views.py、urls.py 和列表 index.js、index.module.less，三个旧 API 摘要必须存在；无新模型、迁移、权限或客户操作。真实 helper 仅精确新增两个 SSL 路由／视图模块许可，覆盖双根发布／恢复和第二根部分失败恢复。

重新核实现网、业务空闲和资源余量后建立新配对备份 `/opt/spug/backups/20261106-104807-before-upgrade-ceba780-8200a483`。独立线上核验 69 项业务／模式／凭据摘要、20 条证书、59 个历史任务、4 条个人提醒偏好、8 条提醒、4 个来源均保全。两份 API 树 247／287 文件只覆盖上述三模块，其余逐字节一致；原始 API/UI 收据和配对备份有效。9 服务、15 健康项、健康 timer、公网 HTTPS 与内部 HTTP 正常，正式资源与浏览器验收摘要相同，原许可保留。

上线只读本地列表确认可见性字段和隐藏查询已生效，没有替用户隐藏真实记录，没有创建客户巡检、续签、重载或改写配置。原已过期 4 条仍由用户自行选择隐藏。保留 feature/operations-reliability 分支与草稿 PR #4，不合并主分支。

[线上核验摘要](2026-11-06-ssl-visibility-verification.json) · [浏览器报告](2026-11-06-ssl-visibility-browser.json) · [过期行按钮](ssl-visibility-shots/01-hide-expired-action.png) · [已隐藏及恢复](ssl-visibility-shots/02-hidden-certificate-restore.png) · [深色布局](ssl-visibility-shots/03-dark-restore-column.png) · [使用指南](../ssl-monitor-guide.md)
