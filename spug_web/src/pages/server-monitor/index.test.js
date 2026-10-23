import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { http, hasPermission } from 'libs';
import { Modal } from 'antd';
import ServerMonitor from './index';

jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, hasPermission: jest.fn()}));
jest.mock('components', () => {
  const Wrapper = ({children}) => <div>{children}</div>; Wrapper.Item = Wrapper;
  return {Breadcrumb: Wrapper, AuthDiv: Wrapper};
});
jest.mock('antd', () => {
  const Wrapper = ({children, title, extra}) => <div>{title}{extra}{children}</div>;
  const Select = ({value, onChange, options, ...props}) => <select aria-label={props['aria-label']} value={value}
    onChange={event => onChange(event.target.value)}>{options.map(option => <option key={option.value}
      value={option.value}>{option.label}</option>)}</select>;
  return {Card: Wrapper, Space: Wrapper, Tag: Wrapper, Spin: Wrapper,
    Alert: ({message, description}) => <div role="alert">{message}{description}</div>,
    Typography: {Text: Wrapper}, Empty: ({description}) => <div>{description}</div>,
    Button: ({children, onClick, disabled, loading}) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>,
    Switch: ({checked, onChange, ...props}) => <button aria-label={props['aria-label']} role="switch" aria-checked={checked}
      onClick={() => onChange(!checked)}/>,
    Input: ({value, onChange, placeholder}) => <input value={value} onChange={onChange} placeholder={placeholder}/>, Select,
    Progress: ({percent}) => <meter value={percent} data-percent={percent}/>,
    Pagination: ({current, pageSize, total, onChange}) => <div><span>第{current}页</span>
      <button disabled={current * pageSize >= total} onClick={() => onChange(current + 1, pageSize)}>下一页</button></div>,
    Modal: Object.assign(({visible, children, onCancel}) => visible ? <div role="dialog">{children}<button onClick={onCancel}>取消</button></div> : null,
      {confirm: jest.fn()}), message: {success: jest.fn()}};
});

let container;
let cards;
let now;
const snapshot = {status: 'ok', message: '', sampled_at: 1792738800, attempted_at: 1792738800, source_kind: 'panel', metrics: {
  cpu: {percent: 24, cores: 4}, memory: {used_bytes: 1073741824, total_bytes: 4294967296, percent: 25},
  load: [0.2, 0.1, 0.1], network: {up_bytes_sec: 1024, down_bytes_sec: 2048},
  disks: [{path: '/', used_bytes: 1073741824, total_bytes: 10737418240, percent: 10}]}};
const card = id => ({key: `panel:${id}`, name: `Server ${id}`, address: `192.0.2.${id}`, groups: [{key: 'panel:1', name: '生产'}],
  sources: [{id, kind: 'panel', name: `Panel ${id}`}], snapshot: null, can_link: true, panel_id: id, linked_host_id: null});
const flush = async () => {for (let i = 0; i < 15; i++) await Promise.resolve();};
const button = text => [...container.querySelectorAll('button')].find(node => node.textContent === text);
const change = async (label, value) => {await act(async () => {Simulate.change(container.querySelector(`[aria-label="${label}"]`), {target: {value}}); await flush();});};
const advance = async value => {now += value; await act(async () => {jest.advanceTimersByTime(value); await flush();});};
const render = async () => {await act(async () => {ReactDOM.render(<ServerMonitor/>, container); await flush();});};
beforeEach(() => {
  jest.useFakeTimers(); now = 1792738800000; jest.spyOn(Date, 'now').mockImplementation(() => now);
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
  container = document.createElement('div'); document.body.appendChild(container);
  cards = [card(1), card(2)]; hasPermission.mockReturnValue(true);
  http.get.mockImplementation(path => Promise.resolve(path.endsWith('cards/') ? {cards, default_interval: 60} :
    {sources: [{id: 1, kind: 'panel', name: 'Panel 1', address: '192.0.2.1', enabled: true}],
      hosts: [{id: 7, name: '可关联主机', address: '192.0.2.1'}]}));
  http.post.mockImplementation((path, body) => Promise.resolve({key: body.key,
    snapshot: {...snapshot, sampled_at: now / 1000, attempted_at: now / 1000},
    retry_after: body.interval, retry_reason: 'collected'}));
});
afterEach(async () => {act(() => {ReactDOM.unmountComponentAtNode(container);}); await flush(); container.remove();
  jest.restoreAllMocks(); jest.useRealTimers(); jest.resetAllMocks();});

test('empty server state directs users to the two list switches and never samples disabled sources', async () => {
  cards = []; await render();
  expect(container.textContent).toContain('开启监控');
  expect([...container.querySelectorAll('a')].map(node => node.getAttribute('href'))).toEqual(expect.arrayContaining(['/host', '/baota']));
  expect(http.post).not.toHaveBeenCalled();
});

test('only current page cards sample in sequence using the default sixty-second interval', async () => {
  cards = Array.from({length: 20}, (_, i) => card(i + 1)); await render();
  expect(http.post.mock.calls.map(([, body]) => body)).toEqual([{key: 'panel:1', interval: 60}]);
  for (let i = 0; i < 12; i++) await advance(200);
  expect(http.post).toHaveBeenCalledTimes(12);
  expect(container.querySelector('[aria-label="采集间隔"]').value).toBe('60');
  await act(async () => {button('下一页').click(); await flush();});
  expect(http.post.mock.calls[12][1]).toEqual({key: 'panel:13', interval: 60});
});

test('interval selection sends only the allowed thirty and three-hundred second values', async () => {
  cards = [card(1)]; await render();
  await change('采集间隔', '30');
  expect(http.post).toHaveBeenCalledTimes(1);
  await advance(30000);
  expect(http.post.mock.calls[1][1].interval).toBe(30);
  await change('采集间隔', '300');
  expect(http.post).toHaveBeenCalledTimes(2);
  await advance(300000);
  expect(http.post.mock.calls[2][1].interval).toBe(300);
});

test.each(['hidden', 'pause', 'unmount'])('%s stops the remaining round and ignores an old result', async action => {
  let complete;
  http.post.mockReturnValue(new Promise(resolve => {complete = resolve;}));
  await render();
  await act(async () => {
    if (action === 'hidden') {
      Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
      document.dispatchEvent(new Event('visibilitychange'));
    } else if (action === 'pause') container.querySelector('[aria-label="自动刷新"]').click();
    else ReactDOM.unmountComponentAtNode(container);
    await flush();
  });
  await act(async () => {complete({key: 'panel:1', snapshot, retry_after: 0}); await flush();});
  await advance(300000);
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(container.textContent).not.toContain('24%');
});

test('manual refresh reads current cards and old sample cannot restore a disabled source', async () => {
  let complete;
  http.post.mockReturnValue(new Promise(resolve => {complete = resolve;}));
  await render(); cards = [];
  await act(async () => {button('刷新').click(); await flush();});
  await act(async () => {complete({key: 'panel:1', snapshot, retry_after: 0}); await flush();});
  expect(container.textContent).not.toContain('Server 1');
  expect(container.textContent).toContain('开启监控');
});

test('failed and missing metrics remain explicit and hiding addresses also hides search matches', async () => {
  cards = [{...card(1), snapshot: {status: 'error', message: '认证失败', sampled_at: null, attempted_at: 1792738800, metrics: null}}];
  http.post.mockResolvedValue({key: cards[0].key, snapshot: cards[0].snapshot, retry_after: 0}); await render();
  expect(container.textContent).toContain('认证失败'); expect(container.textContent).toContain('—');
  expect(container.querySelectorAll('meter')).toHaveLength(0);
  act(() => container.querySelector('[aria-label="隐藏 IP"]').click());
  expect(container.textContent).not.toContain('192.0.2.1');
});

test('filter changes prevent old samples from replacing new visible card state', async () => {
  cards = [card(1), {...card(2), sources: [{id: 2, kind: 'host', name: 'SSH'}]}];
  let complete;
  http.post.mockImplementationOnce(() => new Promise(resolve => {complete = resolve;})).mockResolvedValue({key: 'panel:2', snapshot: null, retry_after: 0});
  await render(); await change('来源筛选', 'host');
  await act(async () => {complete({key: 'panel:1', snapshot, retry_after: 0}); await flush();});
  expect(container.textContent).not.toContain('Server 1');
  await change('来源筛选', '');
  expect(container.querySelector('[data-monitor-key="panel:1"]').textContent).toContain('待采集');
});

test('link settings list authorized hosts, suggests matching addresses and requires explicit confirmation', async () => {
  cards = [card(1)]; await render();
  await act(async () => {button('关联设置').click(); await flush();});
  expect(container.textContent).toContain('同地址');
  expect(container.querySelector('[aria-label="关联主机"]').value).toBe('');
  await change('关联主机', '7');
  act(() => button('保存关联').click());
  expect(http.post.mock.calls.some(([path]) => path.endsWith('link/'))).toBe(false);
  expect(Modal.confirm).toHaveBeenCalledTimes(1);
  await act(async () => {await Modal.confirm.mock.calls[0][0].onOk(); await flush();});
  expect(http.post).toHaveBeenCalledWith('/api/baota/monitor/link/', {panel_id: 1, host_id: 7});
});

test('monitor page permission gates all card reads and sampling', async () => {
  hasPermission.mockReturnValue(false); await render();
  expect(http.get).not.toHaveBeenCalled(); expect(http.post).not.toHaveBeenCalled();
});

test('an old snapshot is visibly stale even when the server last recorded success', async () => {
  now = 1792738800000;
  cards = [{...card(1), snapshot: {...snapshot, sampled_at: now / 1000 - 3600}}];
  http.post.mockResolvedValue({key: 'panel:1', snapshot: cards[0].snapshot, retry_after: 0});
  await render();
  expect(container.querySelector('[data-monitor-key="panel:1"]').textContent).toContain('数据过期');
  expect(http.post).toHaveBeenCalledWith('/api/baota/monitor/sample/', {key: 'panel:1', interval: 60}, {timeout: 45000});
});

test('unlink explicitly confirms null and a rejected write keeps the settings available', async () => {
  cards = [{...card(1), linked_host_id: 7}]; await render();
  await act(async () => {button('关联设置').click(); await flush();});
  await change('关联主机', '');
  act(() => button('保存关联').click());
  expect(Modal.confirm.mock.calls[0][0].title).toContain('取消关联');
  http.post.mockRejectedValue('forbidden');
  await act(async () => {await Modal.confirm.mock.calls[0][0].onOk(); await flush();});
  expect(http.post).toHaveBeenCalledWith('/api/baota/monitor/link/', {panel_id: 1, host_id: null});
  expect(container.textContent).toContain('关联保存失败');
  expect(container.querySelector('[role=dialog]')).not.toBeNull();
  expect(button('保存关联').disabled).toBe(false);
});

test('manual refresh while automatic sampling is paused performs exactly one visible round', async () => {
  cards = [card(1)]; await render();
  await act(async () => {container.querySelector('[aria-label="自动刷新"]').click(); await flush();});
  expect(http.post).toHaveBeenCalledTimes(1);
  await act(async () => {button('刷新').click(); await flush();});
  expect(http.post).toHaveBeenCalledTimes(2);
  expect(http.get.mock.calls.filter(([path]) => path.endsWith('cards/'))).toHaveLength(2);
  await advance(300000);
  expect(http.post).toHaveBeenCalledTimes(2);
  expect(container.querySelector('[aria-label="自动刷新"]').getAttribute('aria-checked')).toBe('false');
});

test('a failed card list read shows retry feedback rather than a false empty success', async () => {
  http.get.mockRejectedValue('forbidden'); await render();
  expect(container.textContent).toContain('监控列表加载失败');
  expect(container.textContent).not.toContain('还没有开启监控');
  expect(http.post).not.toHaveBeenCalled();
});

test('an initially empty page discovers a source enabled later on its next automatic refresh', async () => {
  cards = []; await render();
  expect(http.post).not.toHaveBeenCalled();
  cards = [card(1)];
  await advance(60000);
  expect(http.get.mock.calls.filter(([path]) => path.endsWith('cards/'))).toHaveLength(2);
  expect(container.textContent).toContain('Server 1');
  expect(http.post.mock.calls.map(([, body]) => body)).toEqual([{key: 'panel:1', interval: 60}]);
});

test('an empty filtered page keeps refreshing and samples a later matching source', async () => {
  cards = [card(1)]; await render();
  await change('来源筛选', 'host');
  expect(container.textContent).toContain('没有符合筛选条件');
  cards = [card(1), {...card(2), sources: [{id: 2, kind: 'host', name: 'SSH'}]}];
  await advance(60000);
  expect(http.get.mock.calls.filter(([path]) => path.endsWith('cards/'))).toHaveLength(2);
  expect(container.textContent).toContain('Server 2');
  expect(container.textContent).not.toContain('Server 1');
  expect(http.post.mock.calls.map(([, body]) => body)).toEqual([
    {key: 'panel:1', interval: 60}, {key: 'panel:2', interval: 60},
  ]);
});

test.each(['hidden', 'pause', 'unmount'])('%s stops automatic card reads even while the page is empty', async action => {
  cards = []; await render();
  await act(async () => {
    if (action === 'hidden') {
      Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
      document.dispatchEvent(new Event('visibilitychange'));
    } else if (action === 'pause') container.querySelector('[aria-label="自动刷新"]').click();
    else ReactDOM.unmountComponentAtNode(container);
    await flush();
  });
  await advance(300000);
  expect(http.get.mock.calls.filter(([path]) => path.endsWith('cards/'))).toHaveLength(1);
  expect(http.post).not.toHaveBeenCalled();
});
