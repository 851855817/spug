# 登录提示移除计划

目标：认证成功后直接进入后台，不再显示真实 IP advisory。用户已授权此具体调整，使用已建分支内联落实设计、实现与验证技能。

- [x] 登录行为回归先验证旧提示阻塞成功跳转；覆盖 has_real_ip=false/缺失/true、MFA、失败及 LDAP，模拟数据不涉及真实凭据。
- [x] 仅在 pages/login/index.js 去掉 Modal.warning 分支和无用 import，保留原 doLogin 与 MFA 条件；运行前端完整回归和 ESLint。
- [x] 新增 login-warning-ui 固定单文件 UI profile，复用完整 API 树保全、UI-only 原始收据/回退及 post-ssl；发布合同先失败再通过。
- [ ] 生产构建、临时 Chrome loopback 模拟登录验收、独立审查及 CI；新鲜核对线上版本和业务空闲、配对备份后发布，独立核验服务/完整 API/69 项业务摘要/SSL 六表/公网。
- [ ] 保存发布证据、说明和 Git 记录，更新草稿 PR，不合并主分支。

验证：旧实现 6 项中 4 项因警告阻塞失败，删除分支后 6 项全通过；前端 50 套 485 项、登录页 ESLint 通过；发布 219 项（Windows 跳过 1 项），新 profile 8 项先失败再通过。独立只读审查确认认证/MFA/LDAP/IP 绑定不变和发布保全接线完整，无阻塞问题。
