# 宝塔一键登录实现计划

**目标：** 宝塔列表“打开”通过已保存 API 配置获取官方一次性登录票据。

**架构：** Django 专用 POST 接口核对独立权限和配置；React 预先打开空白页并校验返回地址再导航。复用有界 API 客户端，新增固定范围发布配置。

**技术栈：** Django、React、Ant Design、现有签名客户端与增量发布器。

- [ ] 在 `spug_api/apps/baota/test_login.py` 写行为测试：权限组合与撤销、POST 限制、缺失配置、真实本地 HTTP/TLS 签名请求、严格票据校验、配置变更和错误脱敏。执行 `../spug-testenv/Scripts/python.exe spug_api/manage.py test apps.baota.test_login --settings=spug.test_settings --noinput`，确认新接口未实现而失败。
- [ ] 新增 `spug_api/apps/baota/login_views.py`，并在 `urls.py` 加入非事务登录路由，先后检查新鲜权限与配置，返回 `{'url': base + '/login?tmp_token=' + token}`；运行上述测试转绿。
- [ ] 在 `spug_web/src/pages/baota/index.test.js` 添加弹窗、权限、重复点击、失败关闭和非法返回地址测试，再实现列表按钮与 `system/role/codes.js` 独立权限。运行页面 Jest 测试。
- [ ] 为 `deploy/upgrade-review/{package,check_image,publish}.py` 加入固定 `panel-login` 文件集合、新模块缺失前态和准确收据数量，先补发布测试。运行 `python -m unittest discover -s deploy/upgrade-review -p 'test_*.py'`。
- [ ] 更新使用文档；运行后端回归、前端测试和构建，按 requesting-code-review 做独立代码审查并修复实质问题。提交、推送同一分支并更新草稿 PR #4。
- [ ] 新鲜核对现网版本、业务空闲、保全摘要和资源；打包、隔离准备、配对备份、受控发布并核对公开入口。若发布门不满足，保留当前正常服务并记录准确状态。
