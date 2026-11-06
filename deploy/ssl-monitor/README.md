# SSL 监控附加迁移发布

这是首次 SSL 监控的独立发布流程，显式 `schema_changes: true`。不使用旧 `upgrade-review` 无迁移 profile，也不修改旧发布器的保全规则。

## 制品与隔离验证

在干净、已审查的 checkout 打包：

```powershell
../spug-testenv/Scripts/python.exe -B deploy/ssl-monitor/package.py <现网完整提交号> --node D:/nodejs/node.exe
```

输出仓库同级的 `ssl-release.json` 与 `ssl-<提交前7位>.tar.gz`。打包会校验固定 API／UI 变更清单，重新构建前端，不带本地配置、数据库或密钥。

上传已核对 SHA-256 的压缩包后，使用压缩包内同版本的 `prepare.py` 在 `/opt/spug` 执行：

```sh
env PYTHONDONTWRITEBYTECODE=1 python3 -B <同版本prepare.py> <完整提交号> <压缩包SHA256>
```

该步骤不改变现网应用或客户配置。仅导出真实现网迁移 Python 文件到隔离目录，以内部网络中的一次性 MariaDB 重放历史、创建非空哨兵数据、生成并验证六张 SSL 新表；再验证兼容回退启动和 Linux／MariaDB 回归。一次性数据库名固定为 `baota-ci-db`，若已被其他工作占用则拒绝运行，不删除其他容器。

原始制品保留在 `ssl-<短号>`；派生制品在 `ssl-prepared-<短号>`。发布加载器将原始全部字节重新绑定压缩包，派生只允许固定 helper、两份 Dockerfile、附加迁移及对应 manifest。任意其他源文件变化即使重写派生摘要也会拒绝。迁移只允许新增六个指定模型及其字段／约束，禁止 RunPython、RunSQL、旧模型更改和顶层执行代码。

## 发布与保全

隔离验证全部成功、CI 与浏览器验收完成后，重新确认实际现网版本和业务空闲，再执行：

```sh
env PYTHONDONTWRITEBYTECODE=1 python3 -B /opt/spug/ssl-<短号>/source/deploy/ssl-monitor/host.py <新完整提交号> <现网完整提交号>
```

发布器持有原有发布锁，检查三份镜像 ID、完整 API／静态文件树、现网 UI 版本、九个服务、原保留进程、健康配置、业务／模式／迁移／密钥摘要和所有原空闲条件。关闭健康恢复和六个入口／后端服务后再次核对，完成数据库与 `/data` 配对备份及源文件收据才覆盖代码。

生产只执行冻结迁移的 `migrate --noinput`。执行前用只读模型状态比较，证明仅有该一条迁移待执行；执行后核对原表结构与数据、原迁移历史不变，仅增加指定迁移和六张与隔离演练完全相同的空表。默认所有 SSL 监控、提醒关闭，启动 scheduler 不能创建任何 SSL 行。验证代码和数据后才开放页面。

## 失败与回退

未尝试迁移时，验证原文件、业务和原配置后恢复原版本。迁移后只允许在六表结构完整、迁移已记录、模型状态一致、原业务完全保全的情况下，恢复旧界面与执行代码，并保留新模型注册、模型定义、包初始化与迁移；未来重建指向已演练的兼容回退镜像。

不会反向删除新表或生成 reverse DDL。部分 DDL、缺失迁移记录、收据损坏、恢复后源文件不符，或开放入口后已出现新业务／作业时，均保持维护关闭，不自动降级未知状态。此时保留配对备份、原始和派生制品及发布报告供必要恢复，不用旧无迁移脚本覆盖。

本地失败路径回归：

```sh
python -B -m unittest discover -s deploy/ssl-monitor -p 'test_*.py' -q
```
