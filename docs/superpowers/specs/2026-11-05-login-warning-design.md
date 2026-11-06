# 移除登录成功后的真实 IP 提示弹窗

用户明确要求去掉截图中的“安全警告”。来源为登录页面：成功响应 `has_real_ip` 为假时弹出 advisory，点击确定才调用 doLogin。它不执行密码或来源验证。只移除该成功提示分支及失去用途的 Modal/docsUrl 引用，成功后直接进入原目标页。

保持服务端认证、MFA、IP 绑定、失败提示、登录字段与原跳转规则；不篡改 `has_real_ip`，不关闭 IP 校验，不修改代理配置、API、权限或数据库。普通登录与 LDAP 使用同一成功路径，MFA 优先处理，未验证前不保存会话。

运行范围仅 `spug_web/src/pages/login/index.js`，沿用现有 worktree、分支及草稿 PR。增加登录行为回归，采用隔离模拟登录接口验证；不使用真实账号密码。发布为 login-warning-ui 单文件 UI profile，完整 API 保全、post-ssl 检查、新鲜版本/业务空闲检查、新配对备份及 UI 兼容回退。
