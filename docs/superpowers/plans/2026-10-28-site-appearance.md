# 全站配色模板实现计划

> 工作者：使用 subagent-driven-development 分派有独立文件边界的任务，逐项完成规格和质量审查。

**目标：** 管理员预览并应用五套配色，全站及所有账号使用同一设置，可恢复经典蓝。

**架构：** 独立 settings 记录与只读公开外观接口，专用超管写入接口校验模板和 revision。根级前端管理器同步主题；Ant Design 4 变量样式、暗色组件样式及自定义 CSS 变量覆盖页面。

**技术栈：** Django 4、React 16、Ant Design 4.24、Less 3、Jest、隔离 SQLite／MariaDB、Chrome。

**规格：** [已获用户批准的设计](../specs/2026-10-28-site-appearance-design.md)。现有 linked worktree 为 `work/spug-panel-tasks`，分支 `feature/operations-reliability`，基准 `c85f77f`；继续使用草稿 PR #4。

## 任务 1：统一配置接口

- [ ] 在 `spug_api/apps/setting/test_appearance.py` 先测试匿名读取最小白名单、非 GET 拒绝、超管保存、普通／停用账号拒绝、未知模板、并发版本冲突及旧数据回退。
- [ ] 运行 `../spug-testenv/Scripts/python.exe spug_api/manage.py test apps.setting.test_appearance --settings=spug.test_settings --noinput`，确认新行为缺失而失败。
- [ ] 创建 `spug_api/apps/setting/appearance.py`，修改 `apps/setting/urls.py` 及 `spug/settings.py` 的精确公开路径。读接口 `/setting/appearance/public/`，写接口 `/setting/appearance/`；格式 `{version:1,preset:'classic-blue',revision:'default'}`，实际保存后 revision 为随机 32 hex。POST `{preset,revision}`，模板为 `classic-blue/jade/indigo/graphite/night`。
- [ ] 同一隔离测试通过；不添加数据库迁移，不将专用外观键加入通用设置写入白名单。提交接口与回归。

## 任务 2：主题管理器及设置页

- [ ] 在 `spug_web/src/appearance/` 测试默认／有效／未知主题、请求合并、失败回退、可见页面 60 秒同步、隐藏暂停、保存失败和并发冲突。
- [ ] 新建 `presets.js`、`store.js`、`Provider.js` 与 `AppearanceSetting.js`；修改入口 `src/index.js` 和 `pages/system/setting/index.js`。模板卡片使用原生单选语义，预览独立示例；成功保存才改变生效主题。
- [ ] 根主题变量挂在 html，公共 GET 不带管理凭据，POST 使用当前登录 token。只存成功读取到的外观缓存；读取失败不阻断页面，不触发全站登录跳转。
- [ ] 用 `D:/nodejs/node.exe node_modules/react-app-rewired/bin/index.js test --watch=false --runInBand --runTestsByPath src/appearance/store.test.js src/appearance/presets.test.js src/appearance/AppearanceSetting.test.js` 验证失败后实现并通过。

## 任务 3：全站颜色与深色组件

- [ ] 将 `src/index.less` 改为 Ant Design 4 变量构建，增加 `src/appearance/theme.less`，深色导入受根标识约束；不升级依赖。
- [ ] 将自定义页面、品牌和共享组件中用于页面、表面、文字、边框、主色的固定色改为主题变量，保留红橙绿语义、图表数据颜色及终端专用色。
- [ ] 浅色／深色样式和关键对比度自动验证；正式构建检查浮层、表格、监控、设置、首页、登录及窄屏。示例数据全部模拟。

## 任务 4：严格发布配置

- [ ] 在 `deploy/upgrade-review/test_appearance_release.py` 先测试固定 API／UI 文件白名单，拒绝额外权限、任务、数据库变更；多模块原始收据、新文件兼容回退及部分失败恢复。
- [ ] 在 `package.py/check_image.py/prepare.py/publish.py` 新增独立 `site-appearance` 配置，精确列出实际文件。两份完整 API 树除白名单覆盖外全部保全，不放宽原有 UI profile。
- [ ] `prepare.py` 和 `.github/workflows/baota-checks.yml` 接入外观隔离测试；更新实际运行镜像的相关固定 COPY 路径。
- [ ] `../spug-testenv/Scripts/python.exe -m unittest discover -s deploy/upgrade-review -p 'test_*.py'` 全部通过，保留旧 profile 回归。

## 任务 5：审查、验证、发布

- [ ] 独立审查先规格再代码质量，修复必须项。运行前端全量、后端相关全量、生产构建和实际 Chrome 模拟多会话验收。
- [ ] 完成 `docs/site-appearance-guide.md`，提交并推送，更新既有草稿 PR，确认 CI 与公开源码精确匹配应用提交。
- [ ] 重新只读确认线上 revision、业务空闲、服务和保全摘要；隔离准备通过后创建新配对备份并受控发布，无需用户再次批准已授权部署。
- [ ] 独立检查 UI／API 版本、原业务数据和进程身份、外观端点与服务健康；保留默认经典蓝由用户切换。记录实际部署证据与结果，不执行客户任务或主动采集。
