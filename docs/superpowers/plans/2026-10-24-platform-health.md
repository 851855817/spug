# 平台健康与恢复实现计划

> 执行方式：当前会话执行；独立页面与宿主机诊断可按 dispatching-parallel-agents 分派；完成后按 requesting-code-review 审查。

**目标：** 超级管理员能看到平台分项状态、下载脱敏报告，并能明确启动已停止服务；后台不可用时仍能通过 SSH 诊断。

**架构：** 后端服务固定探针与有界子进程；React 独立页面；宿主机标准库 CLI。复用 AdminView，不新增模型，源端无客户操作。

**技术栈：** Django 4.2、Python 标准库、Redis 客户端、Supervisor、React 16/Ant Design 4。

## 任务与文件

- [ ] 1. 基线：现有 PanelForm 前端用例和 test_site_ssl_summary 后端用例，确认独立测试配置；缺少本地运行依赖时仅修复测试环境。
- [ ] 2. 后端测试先行：spug_api/apps/setting/test_health.py；以 SimpleTestCase/RequestFactory 验证 AdminView、service 输入、状态恢复、超时和脱敏，纯函数验证磁盘/密钥。
- [ ] 3. 后端：health.py 负责快照和受限操作；health_probe.py 负责独立数据库/Redis/Supervisor探针；health_views.py 负责管理员请求。setting/urls.py 与 spug/test_urls.py 增加健康入口。
- [ ] 4. 页面：spug_web/src/pages/system/health/index.js 及样式和测试；routes.js 加 /system/health，使用 admin 权限；checks 表格和时间，明确报告失败，不沿用旧绿色状态。
- [ ] 5. 宿主机诊断：deploy/platform-health/spug-health.py、tests/test_host_health.py、README.md；独立诊断、固定对象启动和机器可读输出，不解析客户数据。
- [ ] 6. 验证：先运行新增失败用例再实现；本地后端 manage.py test apps.setting.test_health --settings=spug.test_settings；前端 node node_modules/react-app-rewired/bin/index.js test --watchAll=false --runInBand；宿主机 python -m unittest discover -s deploy/platform-health/tests。
- [ ] 7. 独立审查，补有意义的边界用例，执行所有 Baota 回归、前端测试与生产构建。CI 增加健康模块与宿主机工具路径和测试。
- [ ] 8. 创建独立功能提交和 draft PR；打包镜像与运行代码，保存可回退版本并核对保全摘要，部署平台自身，验证新接口和页面、八服务与既有业务数据一致，补部署记录和使用说明。

## 关键断言

```python
assert recover_service('redis', actor_id=1)['status'] == 'refused'
assert start_called is False  # RUNNING/UNKNOWN/STARTING 状态
assert read_key_check()['summary'] not in (original_key, key_digest)
assert len(fixed_services) == 8
```

服务检查仅证明 Supervisor 看到进程运行；队列长度是等待数，不能据此声称没有正在运行或远端仍在执行的任务。所有启动超时都要求重新检查实际状态，不自动再发一次。
