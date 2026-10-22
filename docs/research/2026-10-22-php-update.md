# PHP 同分支更新协议核对（2026-10-22）

本记录来自官方 9.2.0、11.7.0、13.1.0 更新 ZIP 的静态读取，以及公开官方下载脚本的只读 HTTP GET。没有执行 ZIP、安装脚本，也没有连接或变更客户面板。本机协议 fixture 通过，不代表真实 Linux、PHP-FPM 或所有发行版的运行验证。

## 结论和边界

核心 PHP 的官方更新入口是 `/plugin?action=install_plugin`，传 `sName=php-7.4,version=7.4,min_version=33,type=0,upgrade=1`。`upgrade` 属性的**存在**使异步软件路径使用 `mtype=update`；不能用无标记的安装请求代替更新，也不能使用面向第三方插件的 `upgrade_plugin` 冒充核心 PHP 更新。

官方异步路径只把 `7.4` 分支传给 `install_soft.sh`，没有把 `min_version` 传给 PHP 脚本。后者在线下载并调用 `php.sh update 7.4`。因此 API **不能固定补丁版本**。请求的补丁值只表示当前目录提供的预期值，最终补丁由官方在线脚本决定。预检冻结分支、预期补丁和目录；只接受该分支目录中最新的数值补丁；执行后必须同时满足唯一关联的新更新队列已结束、实际 PHP 运行时精确等于预期，才成功。实际变成其他补丁时保留 `uncertain`，不重发、不自动回滚。

2026-10-22 对公开脚本的只读抽查还发现编译路径和极速路径版本映射不同：`install/0/php.sh` 与 `install/3/php.sh` 的 `php_74` 为 `7.4.34`，而 `install/1/php.sh` 与 `install/4/php.sh` 为 `7.4.33`。本适配器第一版只接受 `install_type='0'`；宝塔在 apt 系统上会按其官方逻辑转为 `3`。没有增加自定义 Shell、下载 URL 或补丁参数来绕过面板。

更新脚本会停启对应 PHP-FPM，可能影响引用该分支的网站。计划必须带 `required_acknowledgements=['php_update_restart']`；任务服务持久化确认后才可开始，适配器也拒绝被移除或改写的确认要求。预检说明 `patch_pinned=false` 和 `php_service_may_restart=true`。

## 官方包和源码位置

源包放在本任务旁的 `../baota-api-research/source-reference/`。下表行号以 ZIP 内实际文件为准；已有导出的同名 Python 参考文件内容相同。

| 版本 | ZIP SHA-256 |
| --- | --- |
| 9.2.0 | `44ccad5f7dbbcb5f2bb502bf2d9fba8496a18ca82c873dec1ba043967ff40aae` |
| 11.7.0 | `1803d68b51d04fc5f4740b4715a800dc2bae5dd6755d94f41779fe5f0a6fc1da` |
| 13.1.0 | `2c10452dac4d26a0d8723710bceaf50841e44f3f4f3704ef4e931b634e4dba3c` |

| 证据（ZIP 内文件） | 9.2.0 | 11.7.0 | 13.1.0 |
| --- | --- | --- | --- |
| `panel/class/panelPlugin.py`：`install_plugin` 入口；保留互斥、依赖、CPU、内存、OS 检查 | 881 | 904 | 905 |
| 同文件：目录关联同时核对 `m_version` 和 `min_version` | 902 | 925 | 926 |
| 同文件：`type != 5` 走同步插件路径，否则异步核心软件路径 | 930 | 953 | 954 |
| 同文件：`hasattr(get, 'upgrade')` 使 `mtype='update'` | 1040 | 1063 | 1064 |
| 同文件：队列名 `upgrade[php-7.4]` | 1094 | 1117 | 1118 |
| 同文件：`get_version_info` 优先读取 `version_check.pl` 缓存 | 2385 | 2456 | 2457 |
| `panel/class/ajax.py`：`php_info` 用选定分支的 PHP 可执行文件执行信息脚本 | 1985 | 2024 | 2047 |
| 同文件：把运行时 `php_version` 放进 `phpinfo.php_version` | 2007 | 2046 | 2076 |
| `panel/class/php_info.php`：值来自 `PHP_VERSION` | 5 | 5 | 5 |
| `panel/class/files.py`：只读文件属性接口，返回 `is_dir,is_link,st_dev,st_ino,name,path` | 4580 | 4801 | 4978 |
| `panel/class/data.py`：网站 `getData` 补充 `php_version` | 515 | 524 | 481 |

三个包的 `panel/install/install_soft.sh` 第 7 行均为 `version=$4`，第 20 行在线下载 `$name.sh`，第 24 行仅调用 `bash $name.sh $actionType $version`。`min_version` 没有进入该路径。队列请求返回成功只证明请求入队；脚本末尾打印的成功标记同样不构成实际 PHP 版本证据。

公开脚本读取地址：[编译 php.sh](https://download.bt.cn/install/0/php.sh)、[apt 编译 php.sh](https://download.bt.cn/install/3/php.sh)、[极速 php.sh](https://download.bt.cn/install/1/php.sh)、[apt 极速 php.sh](https://download.bt.cn/install/4/php.sh)。此次读取的 `/0/php.sh` SHA-256 为 `9e90e6c2e181cf8a86d23b2b12e952767e2551799ca7e2fb2b8ddfd93f6c42c6`，`/1/php.sh` 为 `617fd7f9316282c66657b53cdf01205cc9d2868061e5764d1a359c742bb4f539`。它们是会随官方更新变化的公开资源，并未用于自动执行或运行时版本锁定。

`/0/php.sh:1024` 的更新分支依次 `php-fpm stop`、`make install`、`php-fpm start`，并删除 `version_check.pl`。第 1206 行按 `php_${php_version}` 映射获取补丁。`/1/php.sh:568` 也停启 PHP-FPM，第 760 行的 `php_74` 映射值与编译脚本不同。

## 实现合同

- 操作类 `UpdatePHP`，`code/category='php_update'`，`resources=('php_updates',)`。
- 参数为 `{php_version:'74',m_version:'7.4',min_version:'33',install_type:'0'}`；安装类型默认 `0`，拒绝未知字段、非数值补丁、跨分支和极速更新。
- `update_options(client)` 返回 `{'php_updates': [...]}`。每项包含 `php_version,m_version,min_version,current_version,target_version,install_types:['0']`，只列已安装且经运行时核实存在更高目录补丁的分支。
- 预检要求 `get_soft_find.setup is True` 且 `type` 是整数 `5`，复用软件目录摘要、资源/依赖/互斥/免费授权检查和队列检查。实际版本不使用可能过时的 `get_soft_find.version`。
- 运行时必须来自 `/www/server/php/<branch>/bin/php`，返回匹配该分支的严格 `X.Y.Z`、预期 `php_path/php_bin/php_ini`。固定路径所有父目录及 PHP 文件必须存在且非符号链接；设备号和 inode 在派发前必须匹配冻结值。
- 网站引用冻结 `id,name,path,project_type,php_version,status`，派发前重读。PHP 项目存在无法识别的运行分支时拒绝生成不完整的影响预览。
- 每次派发最多一个变更请求，保留官方检查，不传 `force` 或 `id`。目录、当前版本、路径、引用、资源条件或面板版本变化即拒绝。
- 核验只读。实际补丁达到预期但队列尚未结束时继续 `remote`；只有唯一关联的新队列结束且实际版本精确等于目标才成功。队列结束而实际版本仍是初值时失败；队列碰撞、读回失败、目录树替换、实际版本偏离预期或传输歧义时不报告成功。歧义标记存在时，即使实际版本达到预期也保持 `uncertain`；误调派发方法也只读核验。
- PHP 使用专属的 `update_queue_state`，一次读取全部队列后，先检查预检后所有同目标候选数量以及旧同目标任务是否重新运行，再核对保存的任务 ID。不能先按已关联 ID 过滤，否则会漏掉后来新增的同目标任务。新增第二个候选、旧任务重新运行、已关联 ID 消失或被其他 ID 替换时均为冲突；冲突标记保持不确定，不因队列行后来消失而自动恢复成功。

版本读回证明对应分支的 **CLI PHP 二进制版本**。它不证明已在运行的 PHP-FPM 进程使用该版本，也不证明网站请求正常或扩展 ABI 兼容；适配器不会宣称这些效果已验证。符号链接/自定义安装、OpenLiteSpeed、非标准版本后缀，以及官方 `php_info` 实际使用其他分支 ini 的情况均拒绝。

面板 API 没有原子比较后执行能力：最后一次只读检查与官方脚本开始之间仍可能发生外部变更。在线脚本及镜像也不能用此 API 固定；这里的禁止降级是请求合同和派发前检查，意外安装结果只能读回后标为不确定，不能假称完全消除官方脚本行为风险。

## 本机验证

先新增 15 个测试，在适配器缺失时运行得到 15 个预期断言失败；实现后 15 个通过。之后分别添加目录 inode 替换、未知网站引用、更新后目录树替换、冻结确认要求、实际版本已变而队列仍运行、歧义不自动解除的测试，每次先观察对应失败，再补实现。最终命令：

```powershell
../../spug-testenv/Scripts/python.exe manage.py test apps.baota.test_php_update --settings=spug.test_settings --verbosity 1
../../spug-testenv/Scripts/python.exe -m py_compile apps/baota/operations/php_update.py apps/baota/test_php_update.py
```

后续独立审查补充了“已经关联任务 ID 后又新增同目标队列”的回归：新增 4 个测试，修复前得到 5 个预期失败（其中一个测试含运行中/已结束两个冲突子用例）；修复为 PHP 专属队列核验后全部通过。

最终结果：25 个测试通过；Python 编译检查退出码 0。测试使用完全本地协议 fixture，无真实网络请求或远端变更。9.2.0 的适配器测试局部扩展版本白名单，生产白名单由集成改动统一维护。测试覆盖三版本、精确版本独立读回、缓存假成功、禁止降级/跨分支/未安装、资源和队列阻断、网站及目录漂移、单次官方更新请求、队列结束不等于成功、实际版本正确但队列未完工不成功、关联后新增队列仍冲突、旧任务重新运行、已关联 ID 消失、冲突标记不自动解除、歧义不重发和风险确认要求。依赖环境有 Paramiko/cryptography 的现存 TripleDES 弃用提示，不是本次测试失败。
