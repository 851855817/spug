# 网站与备份资产查询协议依据

日期：2026-10-24。本次仅检查已下载的官方版本源码，并用本地夹具和独立进程测试实现；没有请求客户 API，没有下载或恢复任何备份，也没有执行宝塔源码。

## 固定来源

官方更新包地址为 `https://download.bt.cn/install/update/LinuxPanel-<版本>.zip`。源码位于仓库旁的 `../baota-api-research/source-reference/<版本>/panel__class__data.py`；11.7.0 的该成员从本地已有 ZIP 的 `panel/class/data.py` 补提取，没有重新下载。以下为实际源码文件 SHA-256，不是完整安装包摘要。

| 版本 | data.py SHA-256 | 重点行号 |
| --- | --- | --- |
| 9.2.0 | `ac257bfca70ccc4f3d5e924f278ecbc03db2912a033bb4fe6692a86238b95950` | getData 414；localexist/backup_status 482–496；GetSql 713；MySQL 限制 800–802；GetField 895 |
| 11.7.0 | `4b35769be4e117848d6d2b0addd121ad0c4d6625f88867e425990e701932efb2` | getData 414；localexist/backup_status 486–505；GetSql 725；MySQL 限制 812–814；GetField 907 |
| 13.1.0 | `1e74cf664ab7ab8a569d5f247d6500e71bbd5038ca5bc68e7394df1bd59ac12e` | getData 371；localexist/backup_status 443–462；GetSql 682；MySQL 限制 779–781；GetField 874 |

## 采用的请求和字段

仅调用 `/system GetSystemTotal` 核对已适配 Linux 版本，以及 `/data getData` 的固定表 `sites`、`domain`、`databases`、`backup`。网站和域名逐页查询，返回的全部域名归入对应网站。单页 100 条；网站与 MySQL 资产合计上限 1000，域名上限 5000。分页重复、字段无法识别、数量超限均整体失败，不生成部分“成功”资产。

备份采用每个资源一条查询：`table=backup, search=<资源ID>, type=0|1, p=1, limit=1, order="addtime desc"`。其中 `type=0` 为网站、`type=1` 为数据库；官方仅在提供 `search` 时应用备份类型条件。官方备份响应的字段列表未包含 `type`，不能全局拉表后猜测网站与数据库；两类资源的同号 ID 也不能合并。没有登记备份的资源明确保留为 `no_record`。

`databases` 的官方 SQL 强制过滤 `LOWER(type)='MySQL'`，因此本功能只覆盖面板登记的网站和 MySQL 数据库。Redis、PostgreSQL、SQL Server 等未被此列表证实覆盖。该表可能返回 `password` 和 `conn_config`，它们只存在读取进程的原始响应内存中，不存入快照、日志或浏览器响应。

备份取白名单：登记 ID、资源身份、登记时间、面板报告的 `size`、`localexist`、`backup_status`。`backup_status=1` 优先表示进行中；否则分别显示本地缺失、零字节、未知，或面板报告本地存在且非空。未知和无记录不能混为一类。

`filename` 可能是本地路径、`本地路径|云端地址` 或其他远端标识。浏览器只得到受限文件基名以及 `local/cloud_record/unknown` 分类，不保存完整路径、云端地址、签名 URL 或云端凭据。云端登记不证明云端可访问；本地副本缺失也不证明云端备份失败。不会使用 `/files get_file_attribute`（可能计算最多 100 MB 文件摘要），也不会下载、解压、校验归档、发起备份、恢复或清理。

SSL 仅使用网站列表提供的 `ssl.notAfter` 或明确的 `ssl=-1`。本功能不调用 `GetSSL`，不读取私钥，不测试网站、源站或 CDN 的 TLS 握手。日期异常为未知；失效、30 天内临期等状态在读取本地快照时动态计算。

面板 `addtime` 不含时区。备份年龄按平台时区解释，仅作估算；未来、格式错误或未知时间不判定为正常或超期。阈值为 24/48/72/168 小时，默认 48；超期依据未舍入的实际时差判断。

## 查询的面板端副作用

这些查询不属于零写入接口。官方数据路由初始化可能创建缓存；网站 getData 可记录页大小、排序和搜索历史，旧数据库结构还存在列初始化分支；网站摘要还调用宝塔自己的配额和插件查询。备份 getData 会读取本地文件状态与大小，检查 `.pl` 对应的进程，并可删除失效 `.pl` 标记（9.2.0 482–496；11.7.0 486–505；13.1.0 443–462）。平台不会主动调用备份清理，但无法把上述官方查询描述成面板端绝对无写入。

## 平台隔离和验证

GET 只读平台数据库，不创建快照或租约、不访问客户面板。只有超管手动 POST 采集会发请求。数据库单行条件更新提供全局并发一；75 秒租约配合 45 秒独立进程总时限、最多 200 次请求，包含 OS DNS 阻塞。超时先 kill/wait 回收，再返回失败；读取进程没有数据库连接或写入能力。每次 PanelClient 请求继续使用响应体上限和剩余时间预算，无后台线程或自动重试。

网络及凭据解密发生在数据库事务外；写入前重新检查有效租约、当前面板配置和调用者仍为启用、未软删除的超级管理员。配置漂移、面板删除、权限撤销或租约失效时丢弃结果。采集失败保留上次成功快照；24 小时后或采集失败时展示过期数据。配置改变或 API 清空时保留旧快照存储、只展示面板失效状态，不返回旧资源，避免把旧地址的资源绑定到新面板地址。写入和返回时再次执行字段白名单，包含嵌套域名字段。

本地验证命令：从 `spug_api` 使用隔离 Python 运行 `manage.py test apps.baota.inventory.tests --settings=spug.test_settings --noinput`。包含真实独立进程的无网络成功夹具及配置错误测试。MariaDB 两个线程竞争测试分别覆盖首次创建租约和已存在租约；SQLite 显式跳过这些真实行锁测试，不能将跳过当作 MariaDB 验证。
