# 移除网站与证书菜单实现计划

目标：移除重复菜单，保留 SSL 监控和旧链接兼容。用户已明确授权，使用现有 worktree 和 feature/operations-reliability 分支内联执行。

- [x] 核对实际路由、导航过滤、首页待办旧链接，给 `/exec/sites` 增加 `hidden: true`；不修改其他运行文件。
- [x] 为 `hide-site-inventory-ui` 增加 routes 单文件 UI profile，复用完整 API 保全、UI-only 原始收据及回退；复用发布合同测试验证范围、镜像和回退，最终候选运行 post-ssl。
- [ ] 验证导航、SSL 页面、首页待办现有前端测试及全量回归，生产构建后以 loopback 模拟接口验证隐藏及恢复默认、SSL 入口与旧书签。
- [ ] 核对实际线上版本，干净提交打包；隔离 Linux 验证和 CI、全新业务空闲检查及配对备份后发布；独立核对完整 API、业务及 SSL 数据、服务健康和公网，更新文档与草稿 PR。

验证进度：前端 49 套／479 项与 routes ESLint 通过；发布保护 211 项，Windows 跳过 1 项 Linux 锁检查；新单文件 profile 的 8 项合同已先失败再通过。只读现网核对为 6198049、9 服务、69 项摘要、SSL 六表原态及业务空闲，正式发布前重新核验。
