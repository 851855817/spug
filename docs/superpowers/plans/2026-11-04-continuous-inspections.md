# 连续巡检实现计划

**目标：** 点击直接巡检所有目标，逐台报错并继续，不再配置或手动放行批次。

**架构：** 保留既有模型与 Worker。service 负责显式 continuous 策略及兼容旧 manual；worker 使用既有持久化记录逐台推进；runner 只返回受控详细诊断。按 writing-plans / executing-plans 内联实现，完成后独立审查。

- [x] 后端先写失败测试：`apps/ops/test_continuous.py` 验证连续成功／失败／未知到 completed、旧 manual 保持暂停、恢复不重发、取消和授权边界；请求 `execution_mode='continuous'`，期望 pending 自动耗尽、每目标一次。
- [x] `service.py` 定义 `CONTINUOUS_BATCH_SIZE=50`、`CONTINUOUS_FAILURE_THRESHOLD=51` 与 `is_continuous(run)`；create 校验 execution_mode，拒绝 continuous 混合批次字段，run_view 返回模式。`worker.py` 连续恢复跳过未知并继续剩余目标，finish 保存 ProbeError.message。
- [x] `runner.py` 以固定诊断码描述连接阶段与返回码；构造受控 message，最多 200 字符。覆盖认证、DNS、TCP、超时、PHP 路径缺失、python 缺失、JSON 无效、无指标、过大输出；原始输出与异常永不序列化。
- [x] 前端先失败测试并修改 `RunForm.js`：去掉数值框与 Modal，确认执行直接 POST `{job,host_ids,group_ids,params,execution_mode:'continuous',confirmed:true}`；请求期间 pending 去重。`RunDetail.js` 异常展示现有分类建议与后端具体 message，历史数据兼容。
- [x] 运行 `python manage.py test apps.ops.tests apps.ops.test_continuous --settings=spug.test_settings --noinput`（隔离环境）；前端目标及完整回归、ESLint、生产 build。不得调用真实客户接口。
- [x] 追加固定 `continuous-inspections` 发布 profile，仅三处既有 API overlay＋两处 UI。先验证范围／完整树／回退测试，使用已上线 SSL 基线 post-ssl 检查和六表保全、规范化已释放 worker 时间、即将采集门禁。
- [ ] 独立审查、Git 提交推送、CI、最终制品 Linux 回归和模拟浏览器；重新核实现网与空闲，建立新配对备份，发布后独立核对 API 允许覆盖及完整 UI／业务摘要／9 服务／15 健康。更新指南、发布记录和原草稿 PR，不合并主分支。

## 实施验证（发布前）

- 后端全量 695 项，13 项条件跳过；其中主机巡检 40 项、1 项 SQLite 并发跳过。前端 49 套／479 项全通过，修改文件 ESLint 通过。
- 发布回归 203 项、1 项平台条件跳过；SSL 发布回归 34 项通过。连续 profile 精确覆盖三个既有 API 文件和两个 UI 文件，没有模型／schema 变更；前态缺失、范围漂移及部分发布回退已覆盖。
- 独立代码审查已完成，修正 PHP 退出码 127 和阶段超时的错误建议，回归先失败再通过；未发现未解决的阻断问题。
- 新鲜只读预检确认线上仍为 e6ee4e3，9 服务正常、69 项摘要可读、SSL 六表非空数据保留、业务空闲且内存／磁盘有余量。正式发布仍会重新检查，不能复用本次空闲结论。
- 待办：生产制品构建、模拟浏览器、远端隔离验证、重新预检与配对备份、正式发布、独立复核及 PR 记录。未向客户下发巡检。
