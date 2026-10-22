# 宝塔 Linux 面板官方 API 操作目录

整理日期：2026-10-22。此目录用于设计与接入评估，不代表这些操作已经在当前 Spug 中实现或通过客户面板验证。

已读取官方 API 索引列出的全部 107 个接口说明页，涵盖 19 类模块；整理为 373 条现行文档操作记录，另附 1 条旧版官方 PDF 面板更新记录。

同一请求路径可能通过多个 action 实现不同操作，因此操作记录数与接口说明页面数不同。参数和响应以每行链接到的官方说明为准。

| 分类 | 文档操作记录数 |
|---|---:|
| ACME 证书 | 8 |
| 计划任务 | 36 |
| 数据查询 | 1 |
| 数据库管理 | 35 |
| 项目部署 | 9 |
| Docker | 28 |
| 文件管理 | 37 |
| FTP 管理 | 12 |
| Java 项目 | 43 |
| 密码管理 | 1 |
| 反向代理 | 28 |
| 消息推送 | 4 |
| 网站管理 | 89 |
| SSH 安全 | 2 |
| SSL 证书 | 17 |
| 系统管理 | 20 |
| 后台任务 | 2 |
| 安全扫描 | 1 |
| SSH 终端 | 1 |

## 关键核对结果

- 现行官方参考声明基于 Linux 面板 v11.7.0、Debian 13 环境，且明确表示接口会随版本变化，不保证稳定。
- 官方概览要求双重 MD5 签名（request_time、request_token）；API 密钥独立于网页登录账户密码。
- 官方概览一般描述为 POST，但 Docker 部分接口页明确使用 GET，且存在 GET 修改操作；执行器必须逐操作记录方法，不能把 GET 自动当作只读。
- GetPHPVersion / GetSitePHPVersion / SetPHPVersion 分别用于查询已安装 PHP、查询网站当前 PHP、切换网站运行版本；它们不是 PHP 程序安装或升级接口。
- 本次读取的现行 107 个页面没有直接列出软件安装、卸载和 PHP 扩展安装接口。已另外从宝塔官方 11.7.0、13.1.0 更新包中找到 /plugin?action=install_plugin、/plugin?action=uninstall_plugin、/files?action=InstallSoft 的实现依据；这些属于官方程序中的内部接口，尚未完成真实面板联调，未混入本现行文档目录的统计。
- 旧版 PDF 第 4 页列出 /ajax?action=UpdatePanel；但官方 11.7.0/13.1.0 实现中，服务端 force 或 autoUpdate.pl 条件可能触发更新，check 参数不能保证无软件变更。预检禁止调用此旧入口。现代 /system?action=upgrade_panel 在不携带 force 时查询，执行分支按 force 参数是否存在区分，必须按版本适配。
- 官方 ACME 文档与更新包存在细节差异，例如 apply_dns_auth 在 13.1.0 实现中标注为 DNS 手动验证入口。DNS 自动验证的 auth_to 结构须按版本核实，不能仅按现行说明直接批量执行；第一版 SSL 默认使用 HTTP 文件验证。
- /system?action=upgrade_env 升级的是面板 Python 运行环境，应与面板升级及 PHP 升级分别展示。
- /task 提供队列查询，但队列为空不等于指定任务成功；应结合目标任务、最终软件/面板版本及服务状态验证。
- 部分查询会返回数据库密码、密钥或配置内容；任务输出需要按操作提取允许展示的字段，不能直接完整打印 API 响应。

## 文件

- 宝塔官方API-操作目录.csv：可用表格软件筛选的完整清单。
- 宝塔官方API-操作目录.json：同一目录的结构化副本。

## 官方来源

- https://docs.bt.cn/api/
- https://docs.bt.cn/api/llms.txt
- https://docs.bt.cn/user-guide/config/common/panel-api/
- https://www.bt.cn/api-doc.pdf
- https://www.bt.cn/api_demo_python.zip

未对任何客户面板发起修改、升级或命令执行。
