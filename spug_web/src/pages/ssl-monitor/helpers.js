import moment from 'moment';

export const API = '/api/monitor/ssl/';
export const statFilters = [
  {key: 'total', label: '域名总数', note: '全部监控域名'},
  {key: 'normal', state: 'normal', label: '证书正常', note: '证书在有效期内', tone: 'success'},
  {key: 'expiring', state: 'expiring', label: '即将到期', note: '10 天内到期', tone: 'warning'},
  {key: 'expired', state: 'expired', label: '已过期', note: '需要及时处理', tone: 'error'},
  {key: 'unconfigured', state: 'unconfigured', label: '未配置 SSL', note: '网站未开启 SSL'},
  {key: 'error', state: 'error', label: '检查失败', note: '查看原因并处理', tone: 'error'},
];
export const states = {normal: '正常', expiring: '即将到期', expired: '已过期', error: '检查失败',
  stale: '旧缓存', unknown: '状态未知', unconfigured: '未配置 SSL'};
export const jobStates = {queued: '排队中', running: '执行中', prepared: '等待确认', blocked: '预检未通过',
  success: '检查完成', verified: '证书已核实', failed: '失败', cancelled: '已取消', uncertain: '结果待核实'};
export function certificateState(row) {
  return row.stale ? 'stale' : Object.prototype.hasOwnProperty.call(states, row.state) ? row.state : 'unknown';
}
export function tone(state) {
  return ['normal', 'success', 'verified'].includes(state) ? 'success' : ['expiring', 'stale', 'uncertain', 'blocked'].includes(state)
    ? 'warning' : ['expired', 'error', 'failed'].includes(state) ? 'error' : 'neutral';
}
export function dateUTC8(value) {
  if (!value) return '—';
  const parsed = moment.parseZone(value, moment.ISO_8601, true);
  return parsed.isValid() ? parsed.utcOffset(480).format('YYYY-MM-DD HH:mm:ss') : '—';
}
export function remaining(value) {
  if (!Number.isFinite(value)) return '未知';
  if (value === 0) return '今天到期';
  const days = Math.floor(Math.abs(value));
  return value < 0 ? days ? `已过期 ${days} 天` : '已过期不足 1 天' : days ? `${days} 天` : '不足 1 天';
}
export const sourceDefaults = source => ({...source, enabled: source.enabled === true, reminders: source.reminders === true,
  interval_hours: [1, 6, 12, 24].includes(Number(source.interval_hours)) ? Number(source.interval_hours) : 6, revision: source.revision || ''});
export function canStart(job, accepted) {
  return job?.state === 'prepared' && !!job.revision && Array.isArray(job.acknowledgements) && job.acknowledgements.length > 0 &&
    job.acknowledgements.every(item => typeof item.code === 'string' && accepted.includes(item.code));
}
export const isPollingJob = job => ['queued', 'running'].includes(job?.state);
export const text = value => typeof value === 'string' || typeof value === 'number' ? String(value).slice(0, 500) : '—';
