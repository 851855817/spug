# KOps · 云枢品牌实现计划

目标：按用户已确认的名称及合规要求替换产品展示，保持业务正常并发布至现有后台。

架构：共享 Brand、LegalLink、LegalNotice 组件；法律弹窗无需权限，源码链接绑定 package.py 注入的完整提交。新静态资源追加到独特目录，保留旧资源以兼容回退。

- [x] 编写并运行有意义的失败测试：公共法律入口、完整许可、精确源码版本、branding-ui 白名单与保全/回退。
- [x] 新增 spug_web/src/branding/{Brand.js,LegalNotice.js,source.js,branding.module.css}，更新登录、侧栏、顶栏、Footer 与 About；保留原版权，增加修改日期。
- [x] 更新 public/index.html，新增 public/kops-20261027/{favicon.svg,manifest.json,LICENSE.txt}。README 与 MODIFICATIONS.md 说明二开来源、许可及构建源码绑定方式。
- [x] deploy/upgrade-review/{package.py,check_image.py,prepare.py,publish.py} 接入 branding-ui，设置构建版本；测试严格文件集合、空 API 覆盖、完整保全与失败恢复。
- [x] 运行前端全量与发布保护测试、生产构建；按 requesting-code-review 做独立审查。尽可能验证真实页面视觉，否则如实记录可用验证范围。
- [x] 提交、推送原分支并更新草稿 PR #4；核实公开源码可获取、CI、现网版本和业务空闲，经隔离 prepare、新配对备份受控发布。
- [x] 独立验证服务、完整 API/业务保全、UI 原始回执、许可全文和实际版本源码链接；记录发布结果并告知用户。

实际应用：`0aa2bac577dd706771be17458f4d509d7d7e7862`，17:01 已发布。完整证据见 [发布记录](../../releases/2026-10-27-kops-branding.md)。独立 Web 终端品牌／许可补漏，以及 Git NUL 分隔中文路径打包回归均已完成。
