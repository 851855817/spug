# 首页运维概览与导航排序实现计划

**目标：** 首页查看已开启监控、证书与服务器到期待办，左侧子栏目可在同组内保存个人排序。

**架构：** 复用 ServerMonitor 嵌入模式，Todo 使用四个既有只读查询；目标页白名单解析 URL；Sider 按权限过滤后应用账户浏览器顺序。无后端变化。

**技术栈：** React 16、Ant Design 4、Jest 真实组件测试；固定无迁移发布工具。

- [x] 监控嵌入测试：`<ServerMonitor embedded/>` 初始及 60 秒只有 GET，隐藏停止；单台采集仅目标，刷新缓存不 POST。实现 `server-monitor/index.js` 的嵌入属性与可见页缓存定时器，首页 `home/index.js` 放顶部，响应式待办／公告布局。
- [x] 待办测试：四类 GET 每类前三条与完整总数、日期、旧缓存／不完整、权限、单类错误／重试、可见页定时与过期响应。实现 `home/Todo.js` 和 `home/index.module.less`；例：证书链接 `/exec/sites?state=expiring&panel_id=1&q=example.test`，资产链接 `/host/expiry?state=within_30&q=192.0.2.1`。
- [x] 目标页测试：Inventory、ServerExpiry 根据已知 state／panel_id／q 初始化并显示筛选，忽略非法 URL。实现两个页面的惰性初始状态和搜索框默认值。
- [x] 导航测试：同组拖放保存、重挂载、Alt+箭头、恢复默认、账户隔离、存储失败、未知／重复／无权路径、跨组拒绝。实现 `layout/navigationOrder.js` 纯顺序工具与 `layout/Sider.js` 调整模式和 `layout/layout.module.less` 手柄样式。
- [x] 新增固定 `workbench-ui` profile，精确 UI 文件集合且 API 覆盖为空；延续完整镜像 API 摘要、双目录核对、配对备份及逆序恢复。先写发布保护测试并确认失败，再接入 package／check_image／prepare／publish。CI 纳入首页和侧栏文件。
- [ ] 定向测试先红后绿；`node node_modules/react-app-rewired/bin/index.js test --watch=false --runInBand` 全量；`python -m unittest discover -s deploy/upgrade-review -p 'test_*.py'`；`git diff --check`。独立只读审查，修复阻塞再复验。
- [ ] 文档与提交推送草稿 PR #4；正式 package 以上线 `597a36eeae7e3ae05c1106f68243ee0527e1a790` 为待核对基准，完整 CI、正式构建模拟浏览器验收与服务器隔离检查。重新核对实际版本、空闲、保全及新配对备份后发布；只读验收首页、不采集客户。记录真实发布与回退证据。

每一步先写可观察用户行为测试并确认预期失败，再最小实现、验证并记录。实现与发布在当前会话顺序执行，审查时使用只读 reviewer；延续已授权工作不另建开发分支或合并主分支。

## 本地验证进度

2026-10-26：监控、待办、URL 筛选、导航、发布保护均已先验证新增测试失败，再实现转绿。全量前端 27 套、339 项通过；发布保护 104 项通过，Windows 文件锁检查跳过 1 项，Linux 隔离准备时须补验。`git diff --check` 无错误。使用说明已更新；独立只读审查及正式构建、CI、浏览器与线上发布验证尚待完成。
