# 网站证书、备份与巡检体验实现计划

> 当前会话使用 subagent-driven-development 执行相互独立的后端资产采集、巡检界面与资产界面任务；根代理负责接口整合、审查与发布。用户已授权全部三项，不再停下询问执行方式。

**目标：** 实现网站证书聚合、备份检查和清晰可定位的巡检结果。

**架构：** apps.baota.inventory 独立白名单读取器、快照表和租约；手动采集、GET仅数据库；React复用可拖动表格、现有权限和HTTP。运维作业的执行逻辑不变，只增强结果展示。

**技术栈：** Django、SQLite/MariaDB、宝塔签名 API、React/Ant Design。

- [ ] 资产后端：新增 inventory/{models,readers,service,views,urls,tests}.py，baota/models.py 注册及urls.py接线。先测非超管拒绝、快照空态/失败保留、状态/筛选分页、过期/config revision失效、租约互斥与释放、未知字段不暴露，再实现。备份读取对照固定源码，限制时间和数量，不能返回任意原文。
- [ ] 巡检体验：修改 operations/{index,Records,RunDetail,helpers}.js 和index.test.js；先测记录“查看结果”、URL恢复、创建后定位、异常统计/筛选、处理建议，再实现。
- [ ] 资产界面：新增 pages/panel-inventory/{index,helpers,index.test}.js，routes.js接线，面板任务入口带选择上下文但不触发预检/执行。验证空态、手动顺序采集、停止后续/过期响应、筛选分页、状态与边界文案。
- [ ] 集成审查：独立规格及代码审查、全量后端和前端、生产构建、MariaDB互斥测试，修复后补有意义的回归。
- [ ] 发布：记录当前版本/空闲状态与配对备份；新增迁移、兼容回退镜像、受控API和资产发布，核对业务/密钥摘要，浏览器不向客户下发采集验收，更新指南、发布记录与GitHub PR。

后端命令：`../spug-testenv/Scripts/python.exe manage.py test apps.baota.inventory --settings=spug.test_settings --noinput`（从spug_api使用实际相对路径）。前端：固定Node执行`react-app-rewired test --watchAll=false --runInBand`。每个实现任务先观察行为测试失败，再实现、运行通过；最终不重复无变化的测试。
