# 宝塔隔离面板验证记录

日期：2026-10-22。此记录持续随实现更新，静态源码依据见同目录接口调研记录。

## 隔离边界

服务器上的临时容器 `baota-task-lab`，无主机目录或生产数据卷挂载；512 MiB 内存、0.75 CPU，8888 仅映射主机回环地址 18888。使用官方完整面板包、13.1.0 更新包、官方 Python 3.13.14 bundle，按官方 BT-Panel 的步骤选择插件加载器。临时 API 密钥仅保存于容器，不写入源码或日志。

未执行完整安装器，也未开启面板的域名/IP 证书自动申请。HTTP 服务使用官方 `runserver.py`，仅作隔离协议实验。实际客户面板没有接收测试变更。

## 已观察结果

| 请求 | 观察 | 证据限制 |
|---|---|---|
| `/system?action=GetSystemTotal` | HTTP 200，返回 version=13.1.0、Linux 系统信息 | 连接测试依据；不表示变更接口已全部验证 |
| `/data?action=getData`，table=sites | HTTP 200，data 数组 | 初始无网站 |
| `/data?action=getData`，table=tasks | HTTP 200，data 数组；字段 id/name/type/status/addtime/start/end | 与 task_list 不是同一个表 |
| `/plugin?action=get_soft_list` | HTTP 200，条目在 list.data，返回分页信息及 versions | 顶层包含敏感字段，应用必须按允许字段提取 |
| `/system?action=upgrade_panel`，完全省略 force | HTTP 200，local.version=13.1.0、cloud.OfficialVersion.version=13.1.0、upgrade=0 | 仅执行检查，未执行面板升级 |
| `/plugin?action=install_plugin`，nginx 1.24.0 | HTTP 200、status=true，tasks 新增两条记录 | 初始仅证明入队；安装结果仍需验证 |

## 发现的官方差异

- GetSystemVersion 方法不接受路由传入的参数，在本版本返回 500；连接检查使用 GetSystemTotal。
- 初始缺少 PluginLoader.so 时软件接口返回 404；按官方启动逻辑选用随包提供的架构加载器后正常。没有修改或绕过面板权限校验。
- 更新包解压后的 BT-Task 无执行权限；补齐官方启动器的 chmod 后才启动隔离队列。这也说明“入队成功”不能代替实际安装结果。

## 尚待验证

新建网站、已有规则覆盖及失败恢复、PHP 扩展、卸载、SSL 签发/续签/部署和真实升级尚未在该隔离面板完成。后续结果应逐项追加，不将模拟测试或源码阅读记作真实操作成功。
