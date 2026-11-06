# SSL 过期证书逐条隐藏实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 在当前隔离 worktree、feature/operations-reliability 分支逐项实施；独立代码审查，不合并主分支。

**目标：** 过期证书按账号逐条隐藏、可查看和恢复，换证后自动显示，保留采集与提醒设置。

**架构：** 复用 UserSetting 的 ssl_hidden_certificates JSON 字典，证书 ID 映射当前 certificate_cycle。SSL 列表在分页和统计前按本人可见性筛选；专用本地 POST 携带 hidden 布尔值与 cycle，锁账号和证书，防止换证后的旧请求生效。不改客户接口、续签权限和 schema。

**技术栈：** Django、MariaDB／隔离 SQLite、React、Ant Design、Jest、生产构建与 loopback 模拟浏览器。

## 1. 后端偏好与接口

文件：spug_api/apps/monitor/ssl/{service,views,urls,tests}.py。

- [x] 在 SSLWorkflowTests 中添加独立账号、逐条隐藏恢复、周期变化、正常证书拒绝隐藏、旧 cycle 请求拒绝、坏参数、GET 无写入、提醒保持、筛选与分页的失败测试。
- [x] 运行 `manage.py test apps.monitor.ssl.tests --settings=spug.test_settings --noinput`，确认新测试因功能缺失失败。
- [x] service 增加受限个人偏好读取及 `set_visibility(user, certificate_id, data)`；输入精确为 `{hidden: bool, cycle: str}`。只有存在、当前来源可查看且状态为 expired 的证书可新增隐藏；恢复只清本人条目。用户行锁串行保存偏好，证书行锁和周期检查保证失效请求不隐藏新证书。列表 query 增加 hidden=0/1，响应行附加 cycle/hidden；hidden_count 按当前搜索／来源／分组范围计算。
- [x] views／urls 注册 `certificates/<int:certificate_id>/visibility/`，复用 SSLView 的活跃管理员及正文边界。GET 无写入，无客户调用。
- [x] 完整 SSL 测试通过后提交。

## 2. 页面交互

文件：spug_web/src/pages/ssl-monitor/{index.js,index.test.js,index.module.less}。

- [x] 先写失败测试：过期行隐藏、默认不显示隐藏项、已隐藏视图恢复、换证可见、POST cycle、失败维持、账号切换迟到响应及重复点击。
- [x] 执行 `D:/nodejs/node.exe node_modules/react-scripts/bin/react-scripts.js test --watchAll=false --runInBand src/pages/ssl-monitor` 确认失败。
- [x] 行操作增加“隐藏／恢复显示”，以本人账号引用和锁保护保存；列表顶部增加“查看已隐藏（数量）”复选框，切换／保存后回第一页。旧数据等待新版响应时默认 hidden=false，隐藏按钮仅对有有效 cycle 的 expired 行出现；恢复入口不依赖当前状态。失败保留数据并显示错误。隐藏与提醒分别刷新，各自保存。
- [x] 加宽操作列并允许换行，深浅色复用既有 token；空列表说明与隐藏视图区分。
- [x] SSL 前端和全站测试通过后提交。

## 3. 发布合同与审查

文件：deploy/upgrade-review/{package,prepare,check_image,publish}.py、test_ssl_visibility_release.py、README.md；deploy/expiry/apply-expiry.py。

- [x] 添加严格 profile ssl-certificate-visibility：仅既有三 API 模块 service/views/urls，UI 仅 index.js/index.module.less；所有旧摘要必须存在。helper 精确允许 monitor/ssl/views.py 与 urls.py，保全其余完整 API 树、PHP／健康模块。
- [x] 发布合同测试先红后绿，覆盖缺项／越界拒绝、完整树比较、真实双根覆盖／恢复、第二根部分失败恢复和配对备份。
- [x] 运行完整后端、全站前端、发布保护测试；提交后独立审查，修复重要反馈。更新指南及草稿 PR。

## 4. 候选、上线、收尾

- [x] 从重新核实的现网版本制作制品，隔离数据库与客户模拟接口运行服务器回归，生产构建完成浏览器验收：隐藏／恢复、分页、重载持久化、换证、错误、深浅色和窄屏。阻止所有客户网络请求。
- [x] GitHub CI 全绿，重新检查业务空闲、资源、69 项摘要和现网版本；建立新配对备份并使用兼容回退工具发布。若活动业务阻止则保留正常服务，不强行清锁或修改开关。
- [x] 独立核验 API/UI 收据、其他完整 API 树、用户数据／偏好、健康及 HTTPS。只读本地缓存检查上线响应具有可见性字段，不替用户隐藏真实证书，也不创建客户巡检或续签。
- [x] 保存验证证据与发布记录，提交推送原分支，更新现有草稿 PR，告知入口和使用方法。

当前进度：后端717项（Windows跳过13）、全站前端495项通过；发布保护原245项通过（跳过1），独立审查发现实际打包入口的tuple展开错误，补完整CLI先复现后修复，8项专用profile测试通过。当前现网f72d157，9服务正常、69项对象、业务空闲和资源余量通过新预检。浏览器及服务器候选验证仍须完成。

收尾：ceba780 已于 2026-11-06 10:48（UTC+8）上线。生产构建17条浏览器验收、CI全部通过；最终服务器隔离717／246／10／34／6测试组通过。独立线上核验69项摘要、两份完整API树、用户设置、原始收据与新配对备份通过，9服务15健康、公网200正常。没有替用户隐藏任何实际证书或创建客户任务。详见 docs/releases/2026-11-06-ssl-visibility.md。
