import moment from 'moment';
import { sslStatus } from './sslStatus';

test('certificate timestamps display Beijing time and retain unknown or disabled states', () => {
  expect(sslStatus({enabled: true, certificate: {expires_at: '2099-01-20T20:30:00+00:00'}}))
    .toBe('SSL 到期 2099-01-21 04:30');
  expect(sslStatus({enabled: false, certificate: {expires_at: '2099-01-20T20:30:00+00:00'}})).toBe('SSL 未配置');
  expect(sslStatus({enabled: true, certificate: {expires_at: 'invalid'}})).toBe('SSL 状态未知');
  expect(sslStatus({enabled: true})).toBe('SSL 状态未知');
  expect(sslStatus(undefined)).toBe('SSL 状态未知');
});

test('official date-only metadata remains a date in Beijing at a UTC day boundary', () => {
  const previous = moment.now;
  moment.now = () => Date.parse('2026-10-23T17:00:00Z');
  try {
    expect(sslStatus({state: 'configured', expires_at: '2026-10-23'})).toBe('SSL 已过期 2026-10-23');
    expect(sslStatus({state: 'configured', expires_at: '2026-10-24'})).toBe('SSL 到期 2026-10-24');
  } finally {moment.now = previous;}
});
