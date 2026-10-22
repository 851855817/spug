# 宝塔面板管理实现计划

**目标：** 在 Spug 4.1.1 中增加可分组管理的宝塔面板记录，保存地址、账户和加密密码。

**架构：** 独立的 Django `baota` 模块和 React 页面，复用登录与角色权限。基于已部署的固定摘要镜像制作扩展镜像，源代码编入 `/opt/spug`，避免入口脚本覆盖修改。

**技术栈：** Django 4.2、MariaDB、cryptography/Fernet、React 16、Ant Design 4、Docker Compose。

## 执行方式

在本次任务新建的专用源码目录和 `feature/baota-panel-management` 分支内联执行。官方仓库为 upstream，用户 Fork 为 origin。测试通过后创建 PR，部署对应提交并打版本标签。

## 任务 1：版本管理与备份

- [x] 创建 Fork，配置 origin/upstream 和本地提交身份。
- [x] 备份线上数据库、Spug 数据卷、Compose 和环境文件，并校验压缩包。
- [x] 提交已确认规格、执行计划和忽略规则，推送功能分支。

## 任务 2：后端记录与安全边界

文件：`spug_api/apps/baota/{models,security,views,urls,tests}.py`、`spug_api/spug/{settings,urls,test_settings}.py`。

- [x] 先写接口测试：未登录拒绝；无权限拒绝；创建/读取/编辑/删除；密码不回传且数据库为密文；编辑空密码保留；非法地址拒绝；非空分组禁止删除；分组层级循环拒绝；角色的查看/新增/编辑权限互相隔离。
- [x] 在独立容器中运行测试，确认新增接口尚不存在，测试失败。
- [x] 实现 PanelGroup（父分组）与 Panel（名称、地址、账户、密文密码、分组、备注、创建人、时间）。
- [x] 实现服务器持久化密钥文件，Fernet 加密；列表只返回 has_password。
- [x] 接口为 `/baota/`、`/baota/group/`，使用当前项目 JSON 响应约定。按各自权限分别控制 GET/POST/DELETE，POST 编辑依据 id 区分权限。
- [x] 运行 `python manage.py test apps.baota.tests --settings=spug.test_settings`，全部通过。

## 任务 3：前端与角色权限

文件：`spug_web/src/pages/baota/{index,Group,PanelForm,helpers}.js`、`index.module.less`、`spug_web/src/routes.js`、`spug_web/src/pages/system/role/codes.js`。

- [x] 编写并运行 helpers 测试，覆盖树构建、分组后代筛选、搜索及循环保护，再实现逻辑。
- [x] 菜单位于主机管理下方；左侧分组树与右侧列表，搜索、分页、刷新、添加、编辑、确认删除、打开面板。
- [x] 表单验证必填项；编辑密码留空保持；地址保留自定义端口、安全入口路径和查询参数；不把密码放入 URL。
- [x] 添加角色权限选项和前端展示权限；所有权限由后端再次验证。
- [x] 运行前端测试并完成生产构建。

## 任务 4：持久部署和验收

文件：`deploy/baota/{Dockerfile,entrypoint.sh,README.md}`。

- [x] 在 Docker 包装入口中初始化 `/data/baota.key`（600），再调用官方入口；不把密钥写入镜像或 Git。
- [x] 将后端改动和前端构建编入独立镜像，启动隔离测试实例，使用单独数据库与数据卷。
- [x] 运行 API 集成验证，确认新增表和持久化加密密钥。
- [x] 备份后将线上 Compose 切换到二开镜像；验证登录、原主机管理和新增模块。
- [x] 浏览器验证菜单、父子分组、记录新增编辑与搜索；接口验证删除与分组保护，测试覆盖分组更名；重启容器后复验。
- [x] 审查 diff，提交代码，推送并创建 PR；打部署标签并记录回滚命令、密钥备份要求和使用说明。

## 交付边界

“打开面板”打开宝塔登录页，本版不绕过验证码或双重认证。保存面板记录不发起外部登录请求。不将本机私钥、线上密码或服务器备份提交至 GitHub。
