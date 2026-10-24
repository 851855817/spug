# 运维作业与分批保护实现计划

> 当前会话按已授权范围执行，独立职责按 dispatching-parallel-agents 分工；各模块 TDD，集成后 requesting-code-review。

**目标：** 完成已选前四项的剩余三项，使标准巡检可汇总、失败可分类、批次可暂停熔断、结构化配置可对比。

**架构：** apps.ops 独立持久模型、只读标准作业、受限SSH探针与单协调worker；现有baota任务增加兼容旧记录的可选批次策略；两个入口复用原生界面和HTTP工具。

**技术栈：** Django/SQLite/MariaDB、Paramiko、React/Ant Design、Supervisor。

- [ ] 后端：apps/ops/{models,catalog,probe,service,worker,views,urls}.py、migrations、management command；失败测试覆盖权限、目标冻结、状态、分类、取消、熔断、版本和基线。
- [ ] 面板分批：baota模型迁移、task_service/worker/action边界，失败测试覆盖分批首台/并发保护/failed与uncertain/人工继续/历史兼容。
- [ ] 前端：exec/operations 目录和路由；目录、新建确认、运行汇总、失败筛选、继续/取消、基线对比/报告；TaskDetail增加可选分批参数及状态。
- [ ] 集成：测试配置、生产镜像复制、runopsworker固定进程、CI路径与MariaDB回归、平台健康固定服务更新。
- [ ] 验证：独立代码审查修复，全量后端/前端、生产构建、隔离运行镜像/迁移和生产保全。
- [ ] 发布：业务空闲核对、配对备份、新增迁移、受控进程发布，实际接口/浏览器验收，不执行客户任务；文档、Git记录、PR更新。

## 验证命令

后端：`python manage.py test apps.ops apps.baota.test_batches --settings=spug.test_settings --noinput`。前端：`node node_modules/react-app-rewired/bin/index.js test --watchAll=false --runInBand`。全部相关用例通过后执行生产build和隔离镜像检查。跨数据库的并发断言须在MariaDB运行，SQLite锁语义不能当生产证据。
