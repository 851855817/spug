# 宝塔面板管理扩展

基于上游 Spug 4.1.1（`049984e01373fc1cd97b94b60c0b3124036b0f47`），保留 AGPL-3.0 许可证。

当前已部署批量面板任务，版本、验证结果和 GitHub 记录见 [面板任务部署记录](DEPLOYMENT-PANEL-TASKS.md)；原入口管理的首次记录见 [历史部署记录](DEPLOYMENT.md)。操作步骤见 [用户说明](../../docs/baota-panel-tasks-user-guide.md)。

## 功能

左侧“宝塔面板管理”：分组及子分组、记录新增/编辑/删除、名称/地址/账户搜索、分页、打开面板登录页。角色管理中可以授予面板查看、新增、编辑、删除和分组管理权限。查看权限允许查看本模块全部记录，暂不提供按分组的数据隔离；密码不返回前端。

地址支持 HTTP/HTTPS、自定义端口、宝塔安全入口路径及查询参数；禁止 URL 内嵌账户密码。保存操作不会连接目标面板。“打开”不执行自动登录。

“批量执行 → 面板任务”提供九个操作码、八类操作。先只读预检并查看固定快照，再明确提交；未知投递状态保留面板锁，重启不会自动重发。伪静态文件保存不证明 Web 服务已加载，结果保持待核实。SSL 预览说明面板自动同步可能影响未选网站、孤立证书和面板 HTTPS 证书，必须确认影响范围。

## 构建

前端使用仓库中的 lockfile。PowerShell：

```powershell
cd spug_web
npm ci --legacy-peer-deps --no-audit --no-fund
$env:NODE_OPTIONS='--openssl-legacy-provider'
$env:GENERATE_SOURCEMAP='false'
node node_modules/react-app-rewired/bin/index.js build
cd ..
tar -czf baota-context.tar.gz spug_api/apps/baota spug_api/spug/settings.py spug_api/spug/urls.py spug_web/build deploy/baota
```

在 Docker 主机中将此归档解压到专用空目录并构建；归档仅包含以上列出的文件，不带数据库、密钥、环境文件和本机依赖：

```bash
tar -xzf baota-context.tar.gz -C /opt/spug/baota-build
cd /opt/spug/baota-build
docker build --build-arg SOURCE_REVISION=<完整提交哈希> -f deploy/baota/Dockerfile -t spug-baota:<短提交哈希> .
```

将原 Compose 的 spug 镜像改为新建的本地镜像，保留数据库环境、数据卷、时区、端口和其他配置，然后 `docker compose up -d --pull never`。启动仍由上游 `updatedb` 命令生成并应用数据库迁移，迁移文件保存在数据卷中，与上游约定一致。

在 Spug 服务中设置 `stop_grace_period: 45s`（至少 45 秒）。镜像直接向基础镜像实际使用的 `/etc/supervisord.conf` 追加 `spug-panel-worker`；基础配置没有 `conf.d` include。上游入口完成 `/opt/spug` 到 `/data/spug` 的同步及迁移后，Supervisor 从 `/data/spug/spug_api` 运行 `python3 manage.py runpanelworker --parallel 2`。保留原七个服务，总计八个；部署后逐个确认 RUNNING，并确认应用健康。

worker 使用单进程锁、独立 `logs/panel-worker.log`，不输出面板原始响应、签名、API 密钥或 PEM。SIGTERM 停止领取新工作并等待当前有时限的调用；Supervisor 等待 35 秒，必要时结束整个进程组。意外中断后恢复为待核实，保留任务历史与面板锁。

## 密钥与备份

扩展入口在第一次运行时创建 `/data/baota.key` 和校验文件 `/data/baota.key.sha256`，权限均为 600；后续启动校验并复用。路径可由 `BAOTA_KEY_FILE` 指定。密钥和校验文件不属于源代码，不会进入镜像或 Git。

必须配套备份数据库和完整 Spug 数据卷（包含原始加密密钥）。丢失密钥后无法恢复已保存密码；指纹文件仍在而密钥丢失时入口会停止，避免静默生成新密钥。

升级前保存原 Compose、镜像 ID 和相同时间点的数据库/完整数据卷备份。先在不接触客户面板的克隆数据库及克隆卷上完成“新版 → 兼容回退版 → 新版”预演，核对原有面板及分组、任务和迁移记录；旧版新增/编辑面板也必须可用。真实数据验证结果另行记录，镜像构建检查不能代替此预演。

## 安全回滚

不能直接切回 `spug-baota:b8677f7`：旧入口每次启动会同步旧模型并运行 `updatedb`（自动 `makemigrations` 与 `migrate`），可能根据旧模型生成删除新 API 字段或任务表的迁移。使用该旧镜像派生的兼容回退镜像，保留旧路由、UI、密钥校验入口及原七个服务；将精确的一条 `python3 manage.py updatedb` 改为 `python3 manage.py migrate --noinput`，匹配不到或匹配多次会构建失败。额外覆盖当前 `models.py`、`views.py`、`validation.py` 三个兼容文件，使旧界面新增面板时提供新 API 字段默认值，编辑时保留 API 配置，且删除面板仍遵守任务历史的外键保护；不接入任务路由或新 worker。

```bash
docker image inspect spug-baota:b8677f7 --format '{{.Id}}'
docker build --network none -f deploy/baota/rollback.Dockerfile -t spug-baota:rollback-b8677f7 .
docker run --rm --network none --read-only \
  -v "$PWD/deploy/baota/test_image.py:/test_image.py:ro" \
  -v "$PWD/spug_api/apps/baota:/compat-source:ro" \
  --entrypoint python3 spug-baota:rollback-b8677f7 /test_image.py rollback
```

回退之前暂停新任务提交，查看并记录所有未完成任务，停止 `spug-panel-worker` 领取并等待当前调用结束（例如 `docker compose exec spug supervisorctl -c /etc/supervisord.conf stop spug-panel-worker`，服务名按实际 Compose 调整）。远端处理或待核实任务必须确认和记录当前状态；不得清空锁、删除任务或自动重发。然后把 Compose 镜像改为上述兼容镜像，保留原环境、卷和至少 45 秒停止宽限，再执行 `docker compose up -d --pull never`。核对旧七个服务、HTTP、面板/分组记录及任务表数量；兼容版不运行新 worker，任务历史和新字段仍保留。

这只是旧功能的临时回退，不撤销已应用迁移。恢复新版时复用相同密钥、数据库和数据卷，再核对任务历史与状态，发送中断任务不会自动重发。若克隆预演失败，先修正兼容方案，不直接对生产尝试。

若必须恢复数据库或卷，只能从同一时间点的配套备份恢复到另一个隔离位置，先另存失败现场并核对交付后的新增记录；不能用旧备份直接覆盖当前数据。不要删除新表、逆迁移或生成删除迁移来“清理”。

## 验证

后端测试在独立容器、SQLite 内存数据库和内存缓存中执行：

```bash
python3 manage.py test apps.baota.tests apps.baota.test_connection apps.baota.test_operations apps.baota.test_tasks apps.baota.test_http_integration --settings=spug.test_settings --noinput
```

前端：

```powershell
$env:CI='true'
node node_modules/react-app-rewired/bin/index.js test --watch=false --runInBand --runTestsByPath src/pages/baota/helpers.test.js
```

密钥不可用时，保存新密码会返回可读错误且不降级为明文。编辑密码留空时保持原密文，填写新值才替换。新接口要求登录及对应角色权限。

GitHub Actions 的后端作业在固定生产基础镜像、`--network none`、只读源码和内存库中发现全部 `test*.py`，包括真实 PanelClient 对回环 HTTP 夹具的签名联调。另有内部网络 MariaDB 并发事务检查及前端测试/构建。轻量镜像检查使用一次性静态占位文件，只验证 Supervisor 注入与受保护的回滚补丁；真实前端由独立作业构建。回滚 CI 使用公开基础镜像加密钥入口构造夹具，不包含主机本地旧镜像、生产数据或凭据。

真实面板观测和未完成项目见 [验证记录](../../docs/research/2026-10-22-baota-lab-validation.md)。软件安装、PHP 扩展、卸载、真实 CA 签发/续签/部署及真实升级未完成实测；回环夹具不证明真实 CA 或面板自动同步行为。两个面板实验容器保持停止，不在生产主机重启或编译实验。
