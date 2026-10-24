# 平台健康与恢复设计

用户已要求直接开发上一轮推荐的运维增强能力。本子项目先完成推荐顺序第一项，其他子项目独立交付；新增页面位于系统管理 → 平台健康，仅超级管理员可访问。沿用现有 linked worktree，分支 feature/operations-reliability。

## 数据与行为

GET /setting/health/ 返回脱敏快照：checked_at 为 UTC ISO 时间，status 为 ok/warning/error/unknown，checks 为数组。每项固定 id、name、status、summary、advice；受管理进程另含 service、process_state 和 recoverable。顶层 limitations 解释进程运行不等于业务任务成功；不查询客户服务器。前端 API 前缀 /api/。

检查项：SELECT 1 数据库连通；Redis PING 与三类已知执行队列长度；Supervisor 八项固定服务状态；应用持久数据盘空间和 Inode；宝塔加密密钥与指纹文件一致性（只返回结论）；宿主机诊断报告的新鲜度。探针在独立子进程执行，单探针超时 4 秒，最多三个并发，超时和不支持返回 unknown/error，异常详情不出接口。缓存至多 15 秒，不持久保存凭据或异常原文；手工检查读当前信息，界面明确时间。

POST /setting/health/ 接收 {action: "start", service: "固定服务名"}。仅允许 nginx、spug-api、spug-monitor、spug-panel-worker、spug-scheduler、spug-worker、spug-ws；拒绝 redis、运行中/状态未知/正在启动的进程。对已停止或失败进程先读取新状态，再 supervisorctl start 固定参数，超时不重发，返回待核实提示。操作写本地受限 JSONL 审计，仅含操作者 ID、服务、时间、结果。锁避免并发启动请求。UI 有说明和执行前确认，未知状态不提供恢复按钮。无需数据库迁移。

## 独立诊断

服务器脚本仅处理部署宿主机的 spug、spug-db 容器、127.0.0.1:8080 和固定 Supervisor 服务；JSON 输出包含容器状态、磁盘/内存和最近备份清单完整性。正常诊断不改服务、不读取或回显环境变量/密钥。明确区分备份文件存在、摘要核验、恢复演练。可显式选择启动停止的容器或服务，不支持删除、重建、自动重启健康服务或覆盖数据恢复。部署文档提供仅 SSH 可用时的路径和命令。主机脚本独立于 Web API 和数据库认证。

## 验证与发布

后端验证鉴权、非固定参数拒绝、停止/运行/未知状态边界、并发启动、子进程超时、磁盘阈值、密钥缺失/变化、错误脱敏和时间。前端验证无权限、查询失败、刷新时间、确认与恢复按钮边界和报告导出。宿主机工具使用本地假 Docker 命令或注入执行器覆盖诊断与恢复，不操作客户面板。

完成后独立代码审查、全量相关测试、生产构建、隔离运行镜像验证；部署保存原代码和资产，核对业务数据和密钥指纹，核对八服务、HTTP 和新接口。仅平台自身受控发布，不对客户执行脚本、升级或恢复操作。
