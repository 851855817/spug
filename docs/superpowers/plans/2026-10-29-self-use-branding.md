# 自用界面整理实现计划

用户已明确要求“全部去掉”，承接上一轮已说明的方案：移除界面开源说明入口及弹窗，原版权与许可证保留在项目文件中。本轮按用户个人自用范围实施，不宣称软件全部原创。

**目标：** 登录、顶栏、页脚、关于、独立终端不再出现开源说明入口或弹窗，基本设置不再展示上游介绍与二维码。

**架构：** 保持现有品牌页脚，删除 LegalLink 与弹窗及其两个调用点。基本设置保留凭据字段、用途和原保存协议。无后端、权限、客户任务或数据修改。

**文件：** `spug_web/src/branding/LegalNotice.js`、`spug_web/src/layout/Header.js`、`spug_web/src/pages/ssh/index.js`、`spug_web/src/pages/system/setting/BasicSetting.js`；更新现有三份品牌/设置测试。新增精确四文件的 `self-use-branding-ui` 发布配置，继承已有完整 API 保全与 UI 回退保护。

- [x] 更新现有行为测试，确认旧界面因仍有入口/服务商介绍而失败。
- [x] 删除入口、弹窗及服务商介绍代码，运行前端回归；不为普通字符串新增镜像测试。
- [x] 新发布配置拒绝任何 API 文件；复用严格发布测试覆盖清单、完整 API 树、不可变镜像、备份和失败恢复。
- [ ] 生产构建、隔离 Chrome、独立审查；提交并更新既有草稿 PR #4，保留主分支。
- [ ] 新鲜核验线上版本和空闲，隔离服务端验证、配对备份、受控发布；独立确认业务/密钥/配色和入口正常，保存实际证据。

前端验证：`CI=true node node_modules/react-app-rewired/bin/index.js test --watch=false --runInBand`。发布验证：`python -m unittest discover -s deploy/upgrade-review -p 'test_*.py'`。生产构建使用现有 `package.py`，实际基准以发布前读取为准。

后续若改变使用对象或发布方式，重新核对相应许可安排；本轮不删除 LICENSE、源代码版权、历史记录或对应源码文件。

## 实际进度

- 旧行为测试明确因仍显示入口和服务商展开按钮失败；实现后前端 39 套、397 项通过。
- 发布回归 157 项通过（Windows 1 项 Linux 锁条件跳过，服务器准备补验），新 profile 复用完整原 8 项协议测试；旧 profile 白名单未放宽。
- 新鲜只读预检：线上 `6428ddc0b118fa618bcbdbe2557587cb68fa1d9e`、业务空闲、63 保全对象，暖瓷白及原 revision 保留。发布前重新核验。

- 独立规格与质量审查通过：四个实际 UI 文件、零 API、旧白名单/版权/许可证未变；终端工具栏条件及 handler、原 token API 保持。独立前端 3 组 5 项、两组发布 16 项均通过。
