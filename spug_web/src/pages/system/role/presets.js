// Job permission drafts by K, 2026-10-27. Released under AGPL-3.0.
import codes from './codes';

const READ = ['dashboard.dashboard.view','host.host.view','baota.panel.view','exec.panel.view',
  'exec.template.view','deploy.app.view','deploy.repository.view','deploy.request.view',
  'pipeline.pipeline.view','schedule.schedule.view','monitor.monitor.view','alarm.alarm.view'];
const DUTY = [...READ,'host.console.view','host.console.list','exec.task.do','exec.panel.cancel'];
const WEBSITE = ['dashboard.dashboard.view','baota.panel.view','exec.panel.view','exec.panel.create',
  'exec.panel.cancel','monitor.monitor.view','alarm.alarm.view'];
const RELEASE = ['dashboard.dashboard.view','deploy.app.view','deploy.app.config',
  'deploy.repository.view','deploy.repository.add','deploy.repository.build',
  'deploy.request.view','deploy.request.add','deploy.request.edit','deploy.request.do',
  'pipeline.pipeline.view','pipeline.pipeline.do'];

export const ROLE_PRESETS = [
  {id:'auditor',name:'只读审计',description:'查看主机、面板、任务、发布、监控与报警记录。',
    limits:'仅查看；主机组及应用、环境范围仍需单独分配。',permissions:READ},
  {id:'duty',name:'值班运维',description:'查看运行情况，在授权主机执行命令、使用终端，取消未下发面板项目。',
    limits:'可通过 SSH 修改授权主机；不含面板执行与宝塔免登录。',permissions:DUTY},
  {id:'website',name:'网站运维',description:'查看面板和面板任务，创建预检，取消未下发项目。',
    limits:'执行权限同时覆盖升级、安装、卸载；本模板只预检，由主管确认执行，不含宝塔免登录。',permissions:WEBSITE},
  {id:'release',name:'发布工程师',description:'创建版本、执行构建、提交发布申请与执行发布、运行既有流水线。',
    limits:'不含发布审核；须单独授权应用和环境。流水线可执行预设远程动作。',permissions:RELEASE},
  {id:'lead',name:'运维主管',description:'主机与面板维护、批量执行及分发、面板执行与人工判定、发布审核。',
    limits:'包含高风险命令、面板批量变更及宝塔主管理员免登录；不是平台超级管理员。',
    permissions:[...new Set([...DUTY,...WEBSITE,...RELEASE,'host.host.add','host.host.edit','host.console.upload',
      'baota.panel.add','baota.panel.edit','baota.panel.login','baota.group.manage',
      'exec.panel.execute','exec.panel.manage','exec.transfer.do','exec.template.add','exec.template.edit',
      'deploy.request.approve','monitor.monitor.add','monitor.monitor.edit'])]},
];

export function buildPresetPermissions(id) {
  const preset=ROLE_PRESETS.find(row=>row.id===id);
  if(!preset) throw new Error('Unknown role preset');
  const selected=new Set(preset.permissions), result={};
  for(const mod of codes){
    result[mod.key]={};
    for(const page of mod.pages)
      result[mod.key][page.key]=page.perms.filter(perm=>selected.has(`${mod.key}.${page.key}.${perm.key}`)).map(perm=>perm.key);
  }
  return result;
}

export function presetSummary(id) {
  const permissions=buildPresetPermissions(id);
  const rows=codes.flatMap(mod=>mod.pages.flatMap(page=>page.perms.filter(perm=>permissions[mod.key][page.key].includes(perm.key))
    .map(perm=>({code:`${mod.key}.${page.key}.${perm.key}`,label:`${page.label} / ${perm.label}`}))));
  const risky=new Set(['host.console.view','host.console.upload','exec.task.do','exec.transfer.do','baota.panel.login',
    'exec.panel.execute','exec.panel.manage','deploy.request.do','deploy.request.approve','pipeline.pipeline.do']);
  return {count:rows.length,labels:rows.map(row=>row.label),risks:rows.filter(row=>risky.has(row.code)).map(row=>row.label)};
}
