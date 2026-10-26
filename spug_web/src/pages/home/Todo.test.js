import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { hasPermission, http } from 'libs';
import Todo from './Todo';

jest.mock('libs', () => ({hasPermission: jest.fn(), t: value => value, http: {get: jest.fn(), post: jest.fn()}}));
jest.mock('antd', () => {
  const Wrapper = ({children, title, extra}) => <div>{title}{extra}{children}</div>;
  const List = Wrapper; List.Item = Wrapper;
  return {Card: Wrapper, List, Tag: Wrapper, Space: Wrapper, Empty: ({description}) => <div>{description}</div>,
    Alert: ({message}) => <div role="alert">{message}</div>, Spin: Wrapper,
    Button: ({children, onClick, disabled}) => <button disabled={disabled} onClick={onClick}>{children}</button>};
});
const flush = async () => {for (let i = 0; i < 20; i++) await Promise.resolve();};
let container, failed, incomplete;
const result = (path, config) => {
  const certificate = path.includes('inventory');
  const expired = config.params.state === 'expired';
  return {total: expired ? 12 : 1, panels: [{status: incomplete ? 'invalidated' : 'ok', stale: false}],
    items: Array.from({length: expired ? 4 : 1}, (_, i) => certificate ? {
      id: `1:${i}`, panel_id: 1, panel_name: '生产主机', name: `${expired ? 'expired' : 'soon'}-${i}.example.test`,
      expires_at: '2030-11-01', days_remaining: expired ? -2 : 5, stale: false,
    } : {key: `host:${i}`, name: `${expired ? '到期' : '临期'}主机${i}`, address: `192.0.2.${i + 1}`,
      expires_on: '2030-11-01', days_remaining: expired ? -1 : 3})};
};
const render = async () => act(async () => {ReactDOM.render(<Todo/>, container); await flush();});
const visibility = async state => act(async () => {
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: state});
  document.dispatchEvent(new Event('visibilitychange')); await flush();
});
beforeEach(() => {
  jest.useFakeTimers(); failed = false; incomplete = false;
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
  container = document.createElement('div'); document.body.appendChild(container);
  hasPermission.mockReturnValue(true);
  http.get.mockImplementation((path, config) => failed && path.includes('inventory') && config.params.state === 'expired'
    ? Promise.reject('private credential must not be displayed') : Promise.resolve(result(path, config)));
});
afterEach(async () => {act(() => {ReactDOM.unmountComponentAtNode(container);}); await flush(); container.remove();
  jest.useRealTimers(); jest.resetAllMocks();});

test('four local expiry categories show true totals and up to three actionable entries each', async () => {
  await render();
  expect(http.get).toHaveBeenCalledTimes(4);
  expect(http.post).not.toHaveBeenCalled();
  expect(container.textContent).toContain('证书已过期'); expect(container.textContent).toContain('服务器30 天内到期');
  expect(container.textContent).toContain('12'); expect(container.textContent).toContain('剩余 5 天');
  expect(container.textContent).toContain('已过期 2 天'); expect(container.textContent).toContain('2030-11-01');
  expect(container.textContent).not.toContain('expired-3.example.test');
  const hrefs = [...container.querySelectorAll('a')].map(a => a.getAttribute('href'));
  expect(hrefs).toEqual(expect.arrayContaining(['/exec/sites?state=expired', '/host/expiry?state=within_30',
    '/exec/sites?state=expiring&panel_id=1&q=soon-0.example.test', '/host/expiry?state=expired&q=192.0.2.1']));
});

test('ordinary accounts never request administrator-only certificate or expiry data', async () => {
  hasPermission.mockReturnValue(false); await render();
  expect(http.get).not.toHaveBeenCalled(); expect(http.post).not.toHaveBeenCalled();
  expect(container.querySelectorAll('a')).toHaveLength(0);
});

test('each failed category reports failure without masking other todos or displaying raw errors', async () => {
  failed = true; await render();
  expect(container.textContent).toContain('读取失败'); expect(container.textContent).toContain('soon-0.example.test');
  expect(container.textContent).not.toContain('private credential');
  failed = false;
  await act(async () => {[...container.querySelectorAll('button')].find(b => b.textContent === '刷新待办').click(); await flush();});
  expect(container.textContent).not.toContain('读取失败'); expect(container.textContent).toContain('expired-0.example.test');
});

test('todos refresh local data every minute only while the page is visible', async () => {
  await render();
  await act(async () => {jest.advanceTimersByTime(60000); await flush();}); expect(http.get).toHaveBeenCalledTimes(8);
  await visibility('hidden');
  await act(async () => {jest.advanceTimersByTime(180000); await flush();}); expect(http.get).toHaveBeenCalledTimes(8);
  await visibility('visible'); expect(http.get).toHaveBeenCalledTimes(12);
  act(() => {ReactDOM.unmountComponentAtNode(container);});
  await act(async () => {jest.advanceTimersByTime(120000); await flush();}); expect(http.get).toHaveBeenCalledTimes(12);
  expect(http.post).not.toHaveBeenCalled();
});

test('late results from a hidden page cannot replace a newer todo refresh', async () => {
  let finish;
  http.get.mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
  await render(); await visibility('hidden'); await visibility('visible');
  await act(async () => {finish({total: 1, panels: [], items: [{id: 'old', name: '旧结果不应出现'}]}); await flush();});
  expect(container.textContent).not.toContain('旧结果不应出现'); expect(container.textContent).toContain('expired-0.example.test');
});

test('missing certificate snapshots are visibly incomplete even when todo counts are zero', async () => {
  incomplete = true; http.get.mockImplementation((path, config) => Promise.resolve({...result(path, config), total: 0, items: []}));
  await render(); expect(container.textContent).toContain('证书数据不完整');
  expect(container.textContent).not.toContain('暂无到期待办');
  expect(http.post).not.toHaveBeenCalled();
});

test('no registered panels is a valid empty todo list, not a collection failure', async () => {
  http.get.mockResolvedValue({items: [], total: 0, panels: []});
  await render(); expect(container.textContent).toContain('暂无到期待办');
  expect(container.textContent).not.toContain('证书数据不完整');
});

test('incomplete certificate data in either category remains visible', async () => {
  http.get.mockImplementation((path, config) => Promise.resolve({...result(path, config),
    panels: [{status: config.params.state === 'expiring' ? 'error' : 'ok', stale: false}]}));
  await render(); expect(container.textContent).toContain('证书数据不完整');
});

test('long resource names retain a bounded search prefix accepted by target pages', async () => {
  const name = 'a'.repeat(63) + '.' + 'b'.repeat(63) + '.' + 'c'.repeat(63) + '.' + 'd'.repeat(57) + '.test';
  http.get.mockImplementation((path, config) => Promise.resolve({...result(path, config), items: path.includes('inventory')
    ? [{id: '1:long', panel_id: 1, name}] : [{key: 'panel:1', name: '长地址服务器', address: name}]}));
  await render();
  const links = [...container.querySelectorAll('a')].map(node => new URL(node.href));
  const site = links.find(url => url.searchParams.has('panel_id'));
  const server = links.find(url => url.pathname === '/host/expiry' && url.searchParams.has('q'));
  expect(site.searchParams.get('q')).toBe(name.slice(0, 200));
  expect(server.searchParams.get('q')).toBe(name.slice(0, 100));
  expect(http.post).not.toHaveBeenCalled();
});
