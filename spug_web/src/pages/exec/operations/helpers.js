export const RUN_LABELS = {queued: '排队中', running: '执行中', paused: '批次完成，待继续', tripped: '已熔断', completed: '已完成', cancelled: '已取消'};
export const TARGET_LABELS = {pending: '待执行', running: '采集中', success: '采集成功', failed: '采集失败', unknown: '中断待核实', cancelled: '已取消'};
export const CATEGORY_LABELS = {ssh_auth: 'SSH认证', connection: '连接失败', timeout: '超时', dependency: '缺少依赖', check_error: '检查异常', interrupted: '中断待核实', host_changed: '主机信息已变化', permission_denied: '主机权限已变化'};
export const CHECK_LABELS = {ok: '正常', warning: '指标需关注', unknown: '未知'};
export const DIFF_LABELS = {same: '相同', changed: '变化', different: '变化', missing: '缺失', unknown: '未知'};
export const ACTIVE_STATES = ['queued', 'running'];

export function hostName(host) {return `${host?.name || `#${host?.id || '未知'}`} · ${host?.hostname || '地址未知'}`;}
export function displayValue(value) {
  if (value === undefined || value === null) return '未知';
  if (value === true) return '是';
  if (value === false) return '否';
  return typeof value === 'object' ? '未知' : String(value);
}
export function groupName(group, groups) {
  const names = [group.name];
  const seen = new Set([group.id]);
  let parent = groups.find(item => item.id === group.parent_id);
  while (parent && !seen.has(parent.id)) {
    seen.add(parent.id); names.unshift(parent.name);
    const parentId = parent.parent_id;
    parent = groups.find(item => item.id === parentId);
  }
  return names.join(' / ');
}
export function previewHosts(catalog, hostIds, groupIds) {
  const selected = new Set(groupIds);
  let changed = true;
  while (changed) {
    changed = false;
    for (const group of catalog.groups || []) {
      if (selected.has(group.parent_id) && !selected.has(group.id)) {selected.add(group.id); changed = true;}
    }
  }
  return (catalog.hosts || []).filter(host => hostIds.includes(host.id) || (host.group_ids || []).some(id => selected.has(id)));
}
export function downloadReport(run, comparison, stale) {
  const report = {run, comparison: comparison || null, snapshot_stale: Boolean(stale), report_exported_at: new Date().toISOString()};
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: 'application/json;charset=utf-8'}));
  const link = document.createElement('a');
  link.href = url; link.download = `operations-${run.id}.json`; document.body.appendChild(link);
  try {link.click();} finally {link.remove(); URL.revokeObjectURL(url);}
}
