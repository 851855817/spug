import codes from './codes';
import { ROLE_PRESETS, buildPresetPermissions, presetSummary } from './presets';
jest.mock('libs',()=>({t:value=>value}));
const flatten = value => Object.entries(value).flatMap(([mod,pages])=>Object.entries(pages).flatMap(([page,perms])=>perms.map(perm=>`${mod}.${page}.${perm}`)));

test('five job templates contain only actual permission codes and never a superuser grant',()=>{
  expect(ROLE_PRESETS.map(row=>row.id)).toEqual(['auditor','duty','website','release','lead']);
  const available=new Set(codes.flatMap(mod=>mod.pages.flatMap(page=>page.perms.map(perm=>`${mod.key}.${page.key}.${perm.key}`))));
  for(const row of ROLE_PRESETS){
    expect(row.permissions.every(code=>available.has(code))).toBe(true);
    const permissions=flatten(buildPresetPermissions(row.id));
    expect(new Set(permissions)).toEqual(new Set(row.permissions));
    expect(permissions.length).toBeGreaterThan(0);
    expect(new Set(permissions).size).toBe(permissions.length);
    expect(permissions.every(code=>available.has(code))).toBe(true);
    expect(permissions.some(code=>code.startsWith('system.')||code==='admin')).toBe(false);
  }
});

test('read-only and website templates cannot execute changes, issue panel login or launch SSH',()=>{
  expect(flatten(buildPresetPermissions('auditor')).every(code=>code.endsWith('.view'))).toBe(true);
  const website=flatten(buildPresetPermissions('website'));
  expect(website).toContain('exec.panel.create');
  for(const blocked of ['exec.panel.execute','exec.panel.manage','baota.panel.login','host.console.view','exec.task.do','exec.transfer.do'])
    expect(website).not.toContain(blocked);
  expect(ROLE_PRESETS.find(row=>row.id==='website').limits).toContain('安装');
});

test('privileged operations are explicit, separate release approval, and never carry host or app scope',()=>{
  expect(flatten(buildPresetPermissions('duty'))).toContain('exec.task.do');
  expect(flatten(buildPresetPermissions('duty'))).not.toContain('baota.panel.login');
  expect(flatten(buildPresetPermissions('release'))).toContain('deploy.request.do');
  expect(flatten(buildPresetPermissions('release'))).not.toContain('deploy.request.approve');
  expect(flatten(buildPresetPermissions('lead'))).toContain('baota.panel.login');
  expect(flatten(buildPresetPermissions('lead'))).toContain('exec.panel.execute');
  expect(presetSummary('lead').risks.join(' ')).toContain('主管理员');
  const first=buildPresetPermissions('lead');first.baota.panel.push('del');
  expect(flatten(buildPresetPermissions('lead'))).not.toContain('baota.panel.del');
  expect(()=>buildPresetPermissions('invalid')).toThrow();
});

test('granular resource features are selectable and job drafts never imply a blanket panel action grant', () => {
  const available = codes.flatMap(mod => mod.pages.flatMap(page => page.perms.map(perm => `${mod.key}.${page.key}.${perm.key}`)));
  for (const code of ['host.host.verify','host.host.import','host.host.export','host.group.move','baota.panel.test',
    'exec.panel_action.panel_upgrade','exec.panel_action.site_add','exec.panel_action.software_uninstall',
    'monitor.server.collect','monitor.server.configure','monitor.ssl.renew','ops.inventory.collect',
    'ops.job.continue','ops.expiry.edit','schedule.schedule.run','schedule.schedule.enable']) expect(available).toContain(code);
  for (const role of ROLE_PRESETS) expect(role.limits).not.toContain('执行权限同时覆盖');
  expect(flatten(buildPresetPermissions('lead'))).not.toContain('exec.panel_action.panel_upgrade');
  expect(flatten(buildPresetPermissions('lead'))).not.toContain('exec.panel_action.software_uninstall');
});
