# 宝塔 API 地址自动填写

用户指出登录地址已经包含协议、主机和端口，重复填写 API 地址没有必要。本次在现有面板管理表单中调整，不更改任务下发或版本适配规则。

自动模式从合法 HTTP/HTTPS 登录地址提取 origin，去掉安全入口、查询和片段；保留协议、域名、IPv6 和端口。已有显式保存的 API 地址保留，手动修改后不随登录地址变化。历史未配置记录编辑时也提供推导值；若未填写 API 密钥且未手动设置 API 地址，保存普通记录时仍保持 API 未配置，避免无关编辑改变连接配置。非法或带账户密码的 URL 不推导。

本次选用自动填充且保留可编辑字段。完全隐藏地址会影响反向代理配置；只增加文字提示仍然要求重复输入。

- [x] 为 `helpers.js` 的 origin 推导和真实 `PanelForm` 的自动同步、手动覆盖、保存旧配置补充回归。首次运行 13 项预期失败、5 项已有用例通过；实现后面板及任务模块 8 个套件共 42 项通过。
- [x] 修改 `PanelForm.js`：初值和地址变化自动推导；保留显式配置/手动输入；未启用 API 的普通编辑维持空配置；提示说明自动来源。模块 lint 通过；前端生产构建通过，保留上游既有 Hook 警告。
- [x] 运行面板及任务模块前端测试、模块 lint 和生产构建。以提交 `6586098f0606d71175e1e29d90842522bf4f98e9` 打包，21:21（UTC+8）配套备份后发布；完整数据/API 配置/任务/密钥摘要前后一致。浏览器验证自动提取与手动覆盖后取消，未保存测试记录或向客户发送命令。使用说明和部署记录已更新，GitHub PR 延续原分支。

验证命令：`node node_modules/react-app-rewired/bin/index.js test --watch=false --runInBand --runTestsByPath src/pages/baota/helpers.test.js src/pages/baota/PanelForm.test.js`。浏览器验收使用 `https://example.invalid:8888/security-entry`，预期 API 地址 `https://example.invalid:8888`；手动设置带代理前缀的地址后更改登录地址，原手动值保持。
