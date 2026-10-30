export const RUN_LABELS = {queued: '排队中', running: '执行中', paused: '批次完成，待继续', tripped: '已熔断', completed: '已完成', cancelled: '已取消'};
export const TARGET_LABELS = {pending: '待执行', running: '采集中', success: '采集成功', failed: '采集失败', unknown: '中断待核实', cancelled: '已取消'};
export const CATEGORY_LABELS = {ssh_auth: 'SSH认证', connection: '连接失败', timeout: '超时', dependency: '缺少依赖', check_error: '检查异常', interrupted: '中断待核实', host_changed: '主机信息已变化', permission_denied: '主机权限已变化'};
export const CHECK_LABELS = {ok: '正常', warning: '指标需关注', unknown: '未知'};
export const DIFF_LABELS = {same: '相同', changed: '变化', different: '变化', missing: '缺失', unknown: '未知'};
export const ACTIVE_STATES = ['queued', 'running'];
const FAILURE_HINTS = {
  ssh_auth: '在主机管理核对 SSH 用户、密钥及登录权限，确认后手动新建检查作业。',
  connection: '核对主机地址、SSH 端口、防火墙与网络可达性。',
  timeout: '核对主机负载与连接耗时，先确认本次执行状态，再决定是否新建检查。',
  dependency: '按标准作业库核对 Linux、python3 或所选 PHP CLI 的依赖与版本。',
  check_error: '核对标准作业与依赖版本，确认环境支持后手动新建检查；原始输出未保存。',
  interrupted: '核对 Worker 状态与本次执行记录；中断项不会自动重发，核实后再决定新建检查。',
  host_changed: '核对主机最新地址与凭据，确认目标后重新创建作业。',
  permission_denied: '核对创建者当前的主机访问权限，确认授权后重新创建作业。',
};

export function parseRunId(value) {
  if (!/^[1-9]\d{0,9}$/.test(String(value))) return null;
  const id = Number(value);
  return Number.isInteger(id) && id <= 2147483647 ? id : null;
}
export function resultIssues(target) {
  const checks = Array.isArray(target.checks) ? target.checks : [];
  return {warnings: checks.filter(check => check.status === 'warning'), unknowns: checks.filter(check => check.status === 'unknown')};
}
export function needsAttention(target) {
  const {warnings, unknowns} = resultIssues(target);
  return ['failed', 'unknown'].includes(target.state) || warnings.length > 0 || unknowns.length > 0 ||
    (target.state === 'success' && !target.checks?.length);
}
export function resultSummary(target) {
  if (target.state === 'failed') return '采集失败，指标状态未确认';
  if (target.state === 'unknown') return '本次采集待核实，指标状态未确认';
  if (target.state !== 'success') return `${TARGET_LABELS[target.state] || '状态未知'}，尚无检查结果`;
  const {warnings, unknowns} = resultIssues(target);
  const issues = [warnings.length ? `异常 ${warnings.length} 项（${warnings.map(item => item.label || item.key).join('、')}）` : '',
    unknowns.length ? `未知 ${unknowns.length} 项（${unknowns.map(item => item.label || item.key).join('、')}）` : ''].filter(Boolean);
  return issues.join('；') || (target.checks?.length ? `未发现指标异常（已采集 ${target.checks.length} 项）` : '无可用检查项，需核实');
}
export function failureHint(target) {
  if (!['failed', 'unknown'].includes(target.state)) return '—';
  const hint = FAILURE_HINTS[target.category] || '核对本次执行记录与标准作业依赖，确认原因后再决定是否新建检查。';
  return target.state === 'unknown' && target.category !== 'interrupted' ? `${hint} 本次检查可能已下发，请先核实执行结果。` : hint;
}

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
