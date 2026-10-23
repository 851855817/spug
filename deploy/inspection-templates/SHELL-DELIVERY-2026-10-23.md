# 原生 Shell 模板补充记录

2026-10-23补充8项：系统版本与时间、CPU负载与内存、磁盘空间与Inode、进程TOP20、监听端口、失败服务、宝塔PHP CLI版本和FPM进程。名称以Shell·开头，平台解释器为sh，正文独立运行Bash，不依赖Python。既有12项Python正文哈希全部不变。

- 实现提交：`85b88bca7b24af02ebfd094e72914f5c8b1544d7`。
- [Linux CI](https://github.com/851855817/spug/actions/runs/39914735716)：Python3.8/3.12两组测试驱动器各20项通过、无跳过，包括原生Shell恶意参数拒绝、查询失败不泄漏stderr、有界输出、超时终止。所有源片段和生成Shell正文通过ShellCheck；本机ShellCheck0.11.0同样通过。代码审查无Critical/Important导入阻断。
- 全包SHA256：`099b7f8a56395d9fa445b5089f6f0a4aaa49626fe17e7dd9b1363f8d21555b33`。导入器SHA256：`fd42b988de7be216156236f83b3d9490f6dc2dd764e36245886ae34fe490a47b`。
- 预览后按 `--expect-count 8 --interpreter sh --apply` 导入，仅新建ID13–20。导入前12模板，导入后20。执行历史前后均为2；导入器不调用执行器，也未执行客户主机。
- 模板备份：持久卷内 `/data/spug/backups/inspection-templates/20261023T065902189913Z-before.json`，私有目录/文件；不提交备份内容。非敏感ID与哈希见 [导入结果](shell-import-record-2026-10-23.json)。
- 再次只读预览create=[]、skip=全部8项。浏览器模板名称筛选Shell·可见共8项；保留现有Python模板。

边界：Linux、Bash、GNU timeout/coreutils/awk及相应工具必须已安装。PHP安装目录须受信任，Shell路径检查不能消除有权限本机攻击者并发替换的竞态。输出有界且只保留ASCII；即使没有截断提示，也不能把快照当成无限完整清单。进程存在不代表网站健康。本次没有增加Shell版公网TLS检查或PHP配置解析，详见 [说明](README.md)。
