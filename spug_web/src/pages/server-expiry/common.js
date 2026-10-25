import moment from 'moment';

export const API = '/api/ops/expiry/';
export const STATES = {expired: '已过期', within_7: '7 天内到期', within_30: '30 天内到期', valid: '30 天后到期',
  unset: '未登记', invalid: '日期无效', exempt: '无需管理'};
export const STAGES = {'30d': '30 天提醒', '7d': '7 天提醒', '1d': '1 天／今天提醒', expired: '已过期提醒'};
export const time = value => value ? moment(value).format('YYYY-MM-DD HH:mm:ss') : '尚无记录';
export const validDate = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && moment(value, 'YYYY-MM-DD', true).isValid();
export const safeError = (error, fallback) => typeof error === 'string' ? error.replace(/[\r\n\t]/g, ' ').slice(0, 160) : fallback;
export const ownerName = owner => owner ? `${owner.name}${owner.available === false ? '（已停用）' : ''}` : '未指派';

export function platformToday(zone = 'Asia/Shanghai') {
  const parts = new Intl.DateTimeFormat('en', {timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(new Date());
  const get = type => parts.find(part => part.type === type).value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}

export function expiryLabel(row) {
  if (['unset', 'invalid', 'exempt'].includes(row.state)) return STATES[row.state];
  if (row.days_remaining == null) return '日期未知';
  if (row.days_remaining < 0) return `已过期 ${Math.abs(row.days_remaining)} 天`;
  return row.days_remaining === 0 ? '今天到期' : `剩余 ${row.days_remaining} 天`;
}
