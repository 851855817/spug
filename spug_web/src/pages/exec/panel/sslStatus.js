import moment from 'moment';

export function sslStatus(ssl) {
  if (ssl?.state === 'unconfigured' || ssl?.enabled === false) return 'SSL 未配置';
  const raw = ssl?.certificate?.expires_at || ssl?.expires_at;
  if (!raw || (ssl?.state !== 'configured' && ssl?.enabled !== true)) return 'SSL 状态未知';
  const expiry = moment(raw, moment.ISO_8601, true);
  if (!expiry.isValid()) return 'SSL 状态未知';
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(raw);
  const date = dateOnly ? raw : expiry.utcOffset(8).format('YYYY-MM-DD HH:mm');
  const expired = dateOnly ? raw < moment().utcOffset(8).format('YYYY-MM-DD') : expiry.isBefore(moment());
  return `SSL ${expired ? '已过期' : '到期'} ${date}`;
}
