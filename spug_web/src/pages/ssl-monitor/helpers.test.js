import { certificateState, dateUTC8, remaining, sourceDefaults, statFilters, canStart, isPollingJob } from './helpers';

test('statistics map to the exact server filters, including all and ten-day expiry', () => {
  expect(statFilters.map(({key, state}) => [key, state])).toEqual([
    ['total', undefined], ['normal', 'normal'], ['expiring', 'expiring'], ['expired', 'expired'], ['error', 'error']]);
});

test('stale, unknown and unconfigured certificates can never display as normal', () => {
  expect(certificateState({state: 'normal', stale: true})).toBe('stale');
  expect(certificateState({state: 'unknown'})).toBe('unknown');
  expect(certificateState({state: 'unconfigured'})).toBe('unconfigured');
  expect(certificateState({state: 'unexpected'})).toBe('unknown');
  expect(certificateState({state: 'normal', stale: false})).toBe('normal');
});

test('dates use UTC+8 even across midnight and preserve exact expiry seconds', () => {
  expect(dateUTC8('2026-11-04T20:15:43Z')).toBe('2026-11-05 04:15:43');
  expect(dateUTC8('2026-11-04T20:15:43+02:00')).toBe('2026-11-05 02:15:43');
  expect(dateUTC8(null)).toBe('—'); expect(dateUTC8('bad')).toBe('—');
  expect(remaining(null)).toBe('未知'); expect(remaining(-1)).toBe('已过期 1 天');
  expect(remaining(2.7654321)).toBe('2 天'); expect(remaining(0.8)).toBe('不足 1 天');
  expect(remaining(-0.1)).toBe('已过期不足 1 天'); expect(remaining(-2.7)).toBe('已过期 2 天');
});

test('unconfigured sources default disabled with reminders off and six-hour intervals', () => {
  expect(sourceDefaults({key: 'host:1'})).toMatchObject({key: 'host:1', enabled: false, reminders: false, interval_hours: 6, revision: ''});
  expect(sourceDefaults({enabled: true, reminders: true, interval_hours: 12, revision: 'x'})).toMatchObject({enabled: true, reminders: true, interval_hours: 12, revision: 'x'});
});

test('only a prepared job with a revision and every explicit acknowledgement may start', () => {
  const job = {state: 'prepared', revision: 'v1', acknowledgements: [{code: 'ssh_renew'}, {code: 'service_reload'}]};
  expect(canStart(job, [])).toBe(false); expect(canStart(job, ['ssh_renew'])).toBe(false);
  expect(canStart(job, ['ssh_renew', 'service_reload'])).toBe(true);
  expect(canStart({...job, state: 'uncertain'}, ['ssh_renew', 'service_reload'])).toBe(false);
  expect(canStart({...job, revision: ''}, ['ssh_renew', 'service_reload'])).toBe(false);
  expect(isPollingJob({state: 'prepared'})).toBe(false); expect(isPollingJob({state: 'uncertain'})).toBe(false);
  expect(isPollingJob({state: 'running'})).toBe(true);
});
