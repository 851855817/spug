# SSH SSL 巡检缺陷修复计划

目标：修正已复现的 SSH SSL 配置误判、首次失败状态和错误原因丢失，保留既有采集边界。

执行方式：当前已隔离的 feature/operations-reliability 工作区内联修复；按既有设计和用户要求推进。

- [x] 在 `test_collectors.py` 添加带结束锚点的普通 location 正则、固定探针错误码传递与未知异常脱敏回归；在 `tests.py` 添加新来源待检查、首次失败原因和公开任务结果回归。运行 `../../spug-testenv/Scripts/python.exe manage.py test apps.monitor.ssl.tests apps.monitor.ssl.test_collectors --settings=spug.test_settings --noinput`，确认新增断言因缺陷失败。
- [x] 修改 `remote_probe.py` 的 location 判断和已知错误映射，`collectors.py` 与 `service.py` 复用固定映射；空摘要不判为配置变更。仅修改必要运行代码，无模型和权限变动。重跑 SSL 套件并检查证书动态选择、越界和未知错误继续拒绝。
- [x] 为发布工具添加精确 `ssl-ssh-discovery` 三模块 profile 和真实 overlay／回退测试，保全 PHP、健康及其余完整 API 树。运行发布保护测试和完整后端回归。
- [ ] 更新 `docs/ssl-monitor-guide.md` 中的 SSH 要求、操作步骤和已知限制，提交分支、推送并更新草稿 PR #4。
- [ ] 使用当前现网版本建立新候选，重新核对业务空闲、配对备份、隔离候选测试和兼容回退；通过后发布并只读核验，不发起客户采集。若空闲或回退检查失败，保留现网并明确记录阻塞。

本地验证：新增回归修复前出现 16 个断言失败、1 个解析异常；修复后 SSL 110 项通过（Windows 条件跳过 1 项），补充动态证书边界 1 项通过。完整后端 704 项通过（Windows 条件跳过 13 项），发布保护 232 项通过（Windows 条件跳过 1 项）；独立审查新增 profile 7 项通过且无阻塞发现。仅模拟接口和临时文件树，未联系客户。
