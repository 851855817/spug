# 通用巡检、网站与 PHP 模板

这批模板依据用户指定范围整理，导入只创建模板，不选择主机、不创建执行任务。它们是参考公开命令文档和检查思路的独立加固实现，不是把第三方仓库整包搬进生产环境。

## 模板清单

| 分类 | 模板 | 参数 / 依赖 |
| --- | --- | --- |
| 通用巡检 | 系统版本与运行时间 | uname、uptime；时区含在此模板 |
| 通用巡检 | CPU负载与内存 | /proc/meminfo |
| 通用巡检 | 磁盘空间与Inode | df；只看本地文件系统 |
| 通用巡检 | 进程资源TOP20 | procps ps；不输出命令参数 |
| 通用巡检 | TCP与UDP监听端口 | ss；不做端口扫描 |
| 通用巡检 | 失败的系统服务 | systemd；不读日志、不重启 |
| PHP检查 | 宝塔CLI版本 | PHP_VERSION=all / 7.4 / 74 |
| PHP检查 | 安全相关配置 | 同上；10项布尔配置白名单 |
| PHP检查 | 已配置扩展 | 同上；仅php.ini声明名称 |
| PHP检查 | FPM进程 | procps ps；全部版本 |
| 网站与证书检查 | HTTPS可用性 | DOMAIN=公网域名；HTTPS 443 HEAD / |
| 网站与证书检查 | TLS证书到期 | DOMAIN=公网域名；不足30天告警 |

执行方式：在 Spug「批量执行 → 模板管理」中选模板，再选择主机。使用 Spug 原有 SSH 执行通道，要求主机已配置 SSH，并安装 Python 3.8+；仅有宝塔 API 记录不能运行这些 SSH 模板。无自动安装依赖。PHP 参数只允许固定分支：5.4–5.6、7.0–7.4、8.0–8.5。

## 公开来源及固定版本

- [tldr-pages/tldr](https://github.com/tldr-pages/tldr/tree/a81be9a1ed669a2275bf13af7d12d6416cf1a7eb)：参考 df、ps、ss、systemctl、uptime、php 等命令文档。文档作者为 tldr-pages team 及 contributors，许可 [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/)。改动是独立实现 Python 包装、固定参数、过滤输出、增加时限；未沿用文档中的安装、删除、重启、完整参数或 phpinfo 示例。
- [Matty9191/ssl-cert-check](https://github.com/Matty9191/ssl-cert-check/blob/906d34b9e2c495bbed82edcb19404a01f7196b8f/ssl-cert-check)：参考远程证书到期检查的用途。原项目 GPL-2.0-or-later；本模板未复制其代码，使用 Python 标准库重新实现窄范围检查，无目录扫描、邮件或 PKCS12 密码参数。
- 同时审查了 [HariSekhon/DevOps-Bash-tools](https://github.com/HariSekhon/DevOps-Bash-tools/tree/18fc6443eb49793b2abf345da553caabd9dd2ad8) 与 [docker-library/healthcheck](https://github.com/docker-library/healthcheck/tree/40afbf64d69cf933af0da4df6383958a29113601) 的选定文件。前者涉及共享依赖、安装和桌面操作，后者是停止维护的容器参考示例，均未原样导入。

来源文件 SHA256 和完整审查范围见 [source-manifests.json](source-manifests.json)，风险结论见 [审查记录](../../docs/research/2026-10-23-inspection-scripts.md)。未对上述仓库全部文件作安全保证。

## 风险控制与结果边界

- 无 shell 拼接、eval、下载执行、软件安装、文件清理、重启、配置修改、计划任务或凭据参数；子进程使用固定命令和净化环境。
- 不读取 `.env`、私钥、完整日志或完整 PHP 配置，不输出环境变量及进程命令参数。可见数据仍包括系统信息、端口和进程名称，应按运维数据管理。
- 常规子命令6秒、PHP版本命令3秒、DNS独立进程8秒、网络读写8秒；模板设60秒总预算。文件256KiB、命令输出48KB上限，展示另有限长。内核不可中断IO等系统故障仍可能推迟终止，不承诺任何环境下的实时硬截止。
- PHP文件来自固定宝塔目录，通过目录FD拒绝软链接和特殊文件。自定义目录或软链接安装会被拒绝。`php -n -v` 会启动受信任的本机 PHP 程序，跳过INI和动态扩展；不能证明FPM/网站的版本或健康状态。
- PHP配置是文件值，额外INI、FPM池和站点覆盖可能改变运行结果；扩展声明不等于已安装/已加载。不会将未设置值直接判成安全。
- 网站检查只接受公网域名，校验全部DNS地址后绑定数值地址连接，拒绝私网、回环、链路本地、保留、组播及过渡地址；严格验证证书链和主机名；不跟随跳转、不回退HTTP、不访问正文或登录页面。只检查DNS返回的首个地址，单地址失败不代表域名所有节点均失败。
- HEAD请求会产生网站访问日志，可能触发站点自定义请求处理；405、临时1xx响应标为未完成检查，不改用GET。证书检查仅握手，不续签。
- 退出码：0=检查完成（包括状态快照，不等于整机无风险）；1=网站HTTP错误或证书不足30天；2=依赖、参数、权限、TLS或检查失败。摘要与具体限制一起判读。

## 复现与导入

```bash
python3 -m unittest discover -s deploy/inspection-templates/tests -v
python3 deploy/inspection-templates/build.py /tmp/inspection-bundle.json
sha256sum /tmp/inspection-bundle.json
```

构建器将 `common.py` 与每个 `checks/*.py` 拼成独立正文，不需要在客户主机安装本项目。模板正文与来源信息、SHA256写入bundle；不提交重复生成的正文。

在Spug应用容器内先预览，再明确加 `--apply` 导入：

```bash
python3 /tmp/import_templates.py --bundle /tmp/inspection-bundle.json --sha256 REVIEWED_SHA256
python3 /tmp/import_templates.py --bundle /tmp/inspection-bundle.json --sha256 REVIEWED_SHA256 --apply
```

导入器要求启用的超管账户，核对全包/逐正文哈希，在事务中创建模板。完全相同的记录跳过；同名修改、重复名或元数据冲突整批拒绝，不覆盖用户内容。导入前将原记录写入 `/data/spug/backups/inspection-templates`，目录700、文件600。**备份可能包含用户原有模板的敏感信息，禁止提交Git。** 导入器不调用执行器，不关联主机，不产生执行历史。
