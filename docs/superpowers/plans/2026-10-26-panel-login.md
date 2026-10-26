# 宝塔一键登录实现计划

**目标：** 宝塔列表“打开”通过已保存 API 配置获取官方一次性登录票据。

**架构：** Django 专用 POST 接口核对独立权限和配置；React 预先打开空白页并校验返回地址再导航。复用有界 API 客户端，新增固定范围发布配置。

**技术栈：** Django、React、Ant Design、现有签名客户端与增量发布器。

- [x] 在 `spug_api/apps/baota/test_login.py` 写行为测试：权限组合与撤销、POST 限制、缺失配置、真实本地 HTTP/TLS 签名请求、严格票据校验、配置变更和错误脱敏。先确认接口未实现而失败。
- [x] 新增 `spug_api/apps/baota/login_views.py`，并在 `urls.py` 加入非事务登录路由，先后检查新鲜权限与配置，返回 `{'url': base + '/login?tmp_token=' + token}`；登录后端 9 项转绿。
- [x] 在 `spug_web/src/pages/baota/index.test.js` 添加弹窗、权限、重复点击、失败关闭和非法返回地址测试，再实现列表按钮与 `system/role/codes.js` 独立权限。页面 16 项通过。
- [x] 为 `deploy/upgrade-review/{package,check_image,publish}.py` 加入固定 `panel-login` 文件集合、新模块缺失前态和准确收据数量，先补发布测试。发布工具 92 项通过；Linux 实际覆盖恢复与失败流程通过。
- [x] 更新使用文档；运行后端回归、前端测试和构建，独立代码审查无待修复项。提交、推送同一分支并更新草稿 PR #4。完整 CI：后端 524（9 条条件跳过）、MariaDB 240、前端 288 及构建通过。
- [x] 新鲜核对现网版本、业务空闲、保全摘要和资源；打包、隔离准备、配对备份、受控发布并核对公开入口。实际应用 `f352e94`，九服务与健康检查 15 项正常，63 项业务／模式／密钥保全不变。

浏览器补测发现仅 meta referrer 策略不能阻止实际导航的来源头；改为明确的 `noreferrer` 链接并重新构建、审查与测试。正式构建在真实浏览器、本地模拟 API 下点击一次：1 次票据请求、1 次目标请求，来源头和 Cookie 均未发送，`opener` 为空。未登录客户真实面板。

完整证据见[发布记录](../../releases/2026-10-26-panel-login.md)。
