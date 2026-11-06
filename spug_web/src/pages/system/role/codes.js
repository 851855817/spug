/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import { t } from 'libs';

const codes = [{
  key: 'dashboard',
  label: t('Dashboard'),
  pages: [{
    key: 'dashboard',
    label: t('Dashboard'),
    perms: [
      {key: 'view', label: t('查看Dashboard')}
    ]
  }]
}, {
  key: 'host',
  label: t('主机管理'),
  pages: [{
    key: 'host',
    label: t('主机管理'),
    perms: [
      {key: 'view', label: t('查看主机')},
      {key: 'add', label: t('新建主机')},
      {key: 'edit', label: t('编辑主机')},
      {key: 'del', label: t('删除主机')},
    ]
  }, {
    key: 'console',
    label: t('Web终端'),
    perms: [
      {key: 'view', label: t('Web终端')},
      {key: 'list', label: t('文件管理')},
      {key: 'upload', label: t('上传文件')},
      {key: 'del', label: t('删除文件')},
    ]
  }]
}, {
  key: 'baota',
  label: t('宝塔面板管理'),
  pages: [{
    key: 'panel',
    label: t('面板记录'),
    perms: [
      {key: 'view', label: t('查看面板')},
      {key: 'login', label: t('一键登录宝塔主管理员')},
      {key: 'add', label: t('添加面板')},
      {key: 'edit', label: t('编辑面板')},
      {key: 'del', label: t('删除面板')},
    ]
  }, {
    key: 'group',
    label: t('面板分组'),
    perms: [{key: 'manage', label: t('管理面板分组')}]
  }]
}, {
  key: 'exec',
  label: t('批量执行'),
  pages: [{
    key: 'panel',
    label: t('面板任务'),
    perms: [
      {key: 'view', label: t('查看面板任务')},
      {key: 'create', label: t('创建面板预检')},
      {key: 'execute', label: t('确认执行面板任务')},
      {key: 'cancel', label: t('取消未下发项目')},
      {key: 'manage', label: t('人工判定面板任务')},
    ]
  }, {
    key: 'task',
    label: t('执行任务'),
    perms: [
      {key: 'do', label: t('执行任务')}
    ]
  }, {
    key: 'template',
    label: t('模板管理'),
    perms: [
      {key: 'view', label: t('查看模板')},
      {key: 'add', label: t('新建模板')},
      {key: 'edit', label: t('编辑模板')},
      {key: 'del', label: t('删除模板')},
    ]
  }, {
    key: 'transfer',
    label: t('文件分发'),
    perms: [
      {key: 'do', label: t('文件分发')}
    ]
  }]
}, {
  key: 'deploy',
  label: t('应用发布'),
  pages: [{
    key: 'app',
    label: t('应用管理'),
    perms: [
      {key: 'view', label: t('查看应用')},
      {key: 'add', label: t('新建应用')},
      {key: 'edit', label: t('编辑应用')},
      {key: 'del', label: t('删除应用')},
      {key: 'config', label: t('查看配置')},
    ]
  }, {
    key: 'repository',
    label: t('构建仓库'),
    perms: [
      {key: 'view', label: t('查看构建')},
      {key: 'add', label: t('新建版本')},
      {key: 'build', label: t('执行构建')},
      {key: 'del', label: t('删除版本')},
    ]
  },{
    key: 'request',
    label: t('发布申请'),
    perms: [
      {key: 'view', label: t('查看申请')},
      {key: 'add', label: t('新建申请')},
      {key: 'edit', label: t('编辑申请')},
      {key: 'del', label: t('删除申请')},
      {key: 'approve', label: t('审核申请')},
      {key: 'do', label: t('执行发布')}
    ]
  }]
}, {
  key: 'pipeline',
  label: t('流水线'),
  pages: [{
    key: 'pipeline',
    label: t('流水线'),
    perms: [
      {key: 'view', label: t('查看流水线')},
      {key: 'add', label: t('新建流水线')},
      {key: 'edit', label: t('编辑流水线')},
      {key: 'del', label: t('删除流水线')},
      {key: 'do', label: t('执行流水线')}
    ]
  }]
}, {
  key: 'schedule',
  label: t('任务计划'),
  pages: [{
    key: 'schedule',
    label: t('任务计划'),
    perms: [
      {key: 'view', label: t('查看任务')},
      {key: 'add', label: t('新建任务')},
      {key: 'edit', label: t('编辑任务')},
      {key: 'del', label: t('删除任务')},
    ]
  }]
}, {
  key: 'config',
  label: t('配置中心'),
  pages: [{
    key: 'env',
    label: t('环境管理'),
    perms: [
      {key: 'view', label: t('查看环境')},
      // {key: 'add', label: '新建环境'},
      {key: 'edit', label: t('编辑环境')},
      {key: 'del', label: t('删除环境')}
    ]
  }, {
    key: 'src',
    label: t('服务管理'),
    perms: [
      {key: 'view', label: t('查看服务')},
      {key: 'add', label: t('新建服务')},
      {key: 'edit', label: t('编辑服务')},
      {key: 'del', label: t('删除服务')},
      {key: 'view_config', label: t('查看配置')},
      {key: 'edit_config', label: t('修改配置')},
    ]
  }, {
    key: 'app',
    label: t('应用管理'),
    perms: [
      {key: 'view', label: t('查看应用')},
      // {key: 'add', label: '新建应用'},
      {key: 'edit', label: t('编辑应用')},
      {key: 'del', label: t('删除应用')},
      {key: 'view_config', label: t('查看配置')},
      {key: 'edit_config', label: t('修改配置')},
    ]
  }]
}, {
  key: 'monitor',
  label: t('监控中心'),
  pages: [{
    key: 'monitor',
    label: t('监控中心'),
    perms: [
      {key: 'view', label: t('查看监控')},
      {key: 'add', label: t('新建监控')},
      {key: 'edit', label: t('编辑监控')},
      {key: 'del', label: t('删除监控')},
    ]
  }]
}, {
  key: 'alarm',
  label: t('报警中心'),
  pages: [{
    key: 'alarm',
    label: t('报警记录'),
    perms: [
      {key: 'view', label: t('查看记录')}
    ]
  }, {
    key: 'contact',
    label: t('报警联系人'),
    perms: [
      {key: 'view', label: t('查看联系人')},
      {key: 'add', label: t('新建联系人')},
      {key: 'edit', label: t('编辑联系人')},
      {key: 'del', label: t('删除联系人')},
    ]
  }, {
    key: 'group',
    label: t('报警联系组'),
    perms: [
      {key: 'view', label: t('查看联系组')},
      {key: 'add', label: t('新建联系组')},
      {key: 'edit', label: t('编辑联系组')},
      {key: 'del', label: t('删除联系组')},
    ]
  }]
}]

const extra = (module, page, rows) => codes.find(item => item.key === module).pages.find(item => item.key === page).perms
  .push(...rows.map(([key, label]) => ({key, label: t(label)})));
extra('host', 'host', [['verify', '验证主机'], ['import', '导入主机'], ['export', '导出主机']]);
codes.find(item => item.key === 'host').pages.push({key: 'group', label: '主机分组', perms:
  [['view', '查看分组'], ['add', '新建分组'], ['edit', '编辑分组'], ['del', '删除分组'], ['move', '移动分组']].map(([key, label]) => ({key, label}))});
extra('baota', 'panel', [['test', '测试面板连接']]);
extra('schedule', 'schedule', [['enable', '启停计划'], ['run', '手动运行或预检计划']]);
codes.find(item => item.key === 'exec').pages.push({key: 'panel_action', label: '面板具体动作（需同时授权创建或执行）', perms:
  [['panel_upgrade', '升级面板'], ['site_add', '创建网站'], ['site_basedir', '配置防跨站'], ['site_rewrite', '配置伪静态'],
    ['ssl_apply', '申请证书'], ['ssl_renew', '续签证书'], ['software_install', '安装软件'], ['software_uninstall', '卸载软件'],
    ['php_extension_install', '安装 PHP 扩展'], ['php_update', '更新 PHP'], ['file_upload', '上传面板文件']].map(([key, label]) => ({key, label}))});
codes.find(item => item.key === 'monitor').pages.push(...[
  {key: 'server', label: '服务器监控', perms: [['view', '查看监控'], ['configure', '配置监控与关联'], ['collect', '采集监控']]},
  {key: 'ssl', label: 'SSL 巡检', perms: [['view', '查看证书'], ['configure', '配置巡检来源'], ['collect', '立即巡检'], ['renew', '续签证书']]},
].map(page => ({...page, perms: page.perms.map(([key, label]) => ({key, label}))})));
codes.push({key: 'ops', label: '运维管理', pages: [
  {key: 'inventory', label: '网站与证书盘点', perms: [['view', '查看盘点'], ['collect', '采集盘点']]},
  {key: 'job', label: '运维作业', perms: [['view', '查看作业'], ['create', '创建作业'], ['continue', '继续执行作业'], ['cancel', '取消作业'], ['baseline', '创建采集基线']]},
  {key: 'expiry', label: '到期管理', perms: [['view', '查看到期'], ['edit', '编辑到期与续费']]},
].map(page => ({...page, perms: page.perms.map(([key, label]) => ({key, label}))}))});

export default codes;
