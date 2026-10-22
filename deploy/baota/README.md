# 宝塔面板管理扩展

基于上游 Spug 4.1.1（`049984e01373fc1cd97b94b60c0b3124036b0f47`），保留 AGPL-3.0 许可证。

已部署版本、验证结果和 GitHub 记录见 [首次部署记录](DEPLOYMENT.md)。

## 功能

左侧“宝塔面板管理”：分组及子分组、记录新增/编辑/删除、名称/地址/账户搜索、分页、打开面板登录页。角色管理中可以授予面板查看、新增、编辑、删除和分组管理权限。查看权限允许查看本模块全部记录，暂不提供按分组的数据隔离；密码不返回前端。

地址支持 HTTP/HTTPS、自定义端口、宝塔安全入口路径及查询参数；禁止 URL 内嵌账户密码。保存操作不会连接目标面板。“打开”不执行自动登录。

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

## 密钥与备份

扩展入口在第一次运行时创建 `/data/baota.key` 和校验文件 `/data/baota.key.sha256`，权限均为 600；后续启动校验并复用。路径可由 `BAOTA_KEY_FILE` 指定。密钥和校验文件不属于源代码，不会进入镜像或 Git。

必须配套备份数据库和完整 Spug 数据卷（包含原始加密密钥）。丢失密钥后无法恢复已保存密码；指纹文件仍在而密钥丢失时入口会停止，避免静默生成新密钥。

升级前保存原 Compose 和数据库/数据卷备份。回滚时恢复原镜像配置再 `docker compose up -d --pull never`。新增表可先保留；不要在有业务数据时盲目删除表或数据卷。回滚后密钥仍保留在 `/data`，以后升级时可以继续使用。

## 验证

后端测试在独立容器、SQLite 内存数据库和内存缓存中执行：

```bash
python3 manage.py test apps.baota.tests --settings=spug.test_settings --noinput
```

前端：

```powershell
$env:CI='true'
node node_modules/react-app-rewired/bin/index.js test --watch=false --runInBand --runTestsByPath src/pages/baota/helpers.test.js
```

密钥不可用时，保存新密码会返回可读错误且不降级为明文。编辑密码留空时保持原密文，填写新值才替换。新接口要求登录及对应角色权限。
