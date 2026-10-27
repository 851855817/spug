import { getReminderPreferences, siteReminder, serverReminder, notificationReminder } from './personalReminders';
import { http } from 'libs';

jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}}));
let settings, owner;
beforeEach(() => {
  Object.defineProperty(window, 'crypto', {configurable: true, value: {getRandomValues: values => require('crypto').randomFillSync(values)}});
  owner = `user-${Math.random()}`; localStorage.setItem('id', owner); localStorage.setItem('token', owner);
  settings = {};
  http.get.mockImplementation(() => Promise.resolve({...settings}));
  http.post.mockImplementation((url, body) => {settings[body.key] = body.value; return Promise.resolve({...settings});});
});
afterEach(() => {jest.resetAllMocks(); localStorage.clear();});
const item = () => serverReminder({key: 'host:9', name: '客户主机', expires_on: '2030-01-02'});

test('personal mutes persist in independent setting rows without changing any other setting or read state', async () => {
  settings.terminal = '{"fontSize":16}';
  const store = getReminderPreferences(); await store.load(); await store.mute(item());
  expect(store.isMuted(item())).toBe(true);
  await store.mute(siteReminder({id: '2:4', panel_id: 2, name: 'example.test', expires_at: '2030-03-02'}));
  expect(Object.keys(settings).filter(key => /^mute:v1:[0-9a-f]{24}$/.test(key))).toHaveLength(2);
  expect(settings.terminal).toBe('{"fontSize":16}');
  expect(http.post.mock.calls.every(([url]) => url === '/api/setting/user/')).toBe(true);
  await store.load(true); expect(store.isMuted(item())).toBe(true);
});

test('date changes and new notification IDs remind again; expiry reminders share the server date identity', async () => {
  const store = getReminderPreferences(); await store.load(); await store.mute(item());
  expect(store.isMuted(serverReminder({asset_key: 'host:9', name: '主机', expires_on: '2030-01-02'}))).toBe(true);
  expect(store.isMuted(serverReminder({key: 'host:9', name: '主机', expires_on: '2031-01-02'}))).toBe(false);
  await store.mute(notificationReminder({id: 1, title: '任务失败', created_at: '2030-01-01 01:00:00'}));
  expect(store.isMuted(notificationReminder({id: 2, title: '任务失败', created_at: '2030-01-01 01:00:00'}))).toBe(false);
  expect(siteReminder({id: '1:2', name: 'unknown'})).toBeNull();
});

test('account switches cannot expose mutes or let late saves update a different account store', async () => {
  const original = getReminderPreferences(); await original.load();
  localStorage.setItem('id','other'); localStorage.setItem('token','other');
  const other = getReminderPreferences(); await other.load();
  await expect(original.mute(item())).rejects.toThrow();
  expect(other.isMuted(item())).toBe(false); expect(http.post).not.toHaveBeenCalled();
});

test('save failure leaves the reminder visible and does not display private error content', async () => {
  const store = getReminderPreferences(); await store.load();
  http.post.mockRejectedValue('credential must stay private');
  await expect(store.mute(item())).rejects.toThrow();
  expect(store.isMuted(item())).toBe(false);
  expect(store.snapshot.error).toContain('保存失败'); expect(store.snapshot.error).not.toContain('credential');
});

test('restoring clears duplicate records for the same cycle and retains a different reminder', async () => {
  const target = item(), other = {...target, identity: 'server:host:10'};
  settings['mute:v1:'+'a'.repeat(24)] = JSON.stringify({...target, version: 1, muted: true});
  settings['mute:v1:'+'b'.repeat(24)] = JSON.stringify({...target, version: 1, muted: true});
  settings['mute:v1:'+'c'.repeat(24)] = JSON.stringify({...other, version: 1, muted: true});
  const store=getReminderPreferences(); await store.load();
  await store.restore(target);
  expect(store.isMuted(target)).toBe(false); expect(store.isMuted(other)).toBe(true);
});
