import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { http, hasPermission } from 'libs';
import { message } from 'antd';
import { ResourceMonitorProvider } from './ResourceMonitorSwitch';
import ResourceMonitorButton from './ResourceMonitorButton';

jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, hasPermission: jest.fn()}));
jest.mock('antd', () => {
  const Wrapper = ({children}) => <div>{children}</div>;
  return {Button: ({children, onClick, loading, disabled, ...props}) => <button aria-label={props['aria-label']}
    onClick={onClick} disabled={disabled || loading}>{children}</button>, Tooltip: Wrapper, Tag: Wrapper, Spin: Wrapper,
    Progress: ({percent}) => <meter value={percent}/>, Empty: ({description}) => <div>{description}</div>,
    Alert: ({message}) => <div role="alert">{message}</div>,
    Modal: ({children, title, onCancel}) => <div role="dialog">{title}{children}<button onClick={onCancel}>关闭详情</button></div>,
    message: {info: jest.fn()}};
});

const flush = async () => {for (let i = 0; i < 20; i++) await Promise.resolve();};
let container;
let enabled;
let cards;
const merged = {key: 'host:9', name: '合并服务器', address: '192.0.2.9', groups: [], can_link: true, panel_id: 2,
  sources: [{id: 9, kind: 'host'}, {id: 2, kind: 'panel'}], snapshot: null};
beforeEach(() => {
  enabled = false; cards = [merged, {...merged, key: 'host:8', sources: [{id: 8, kind: 'host'}]}];
  hasPermission.mockReturnValue(true);
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
  http.get.mockImplementation(path => Promise.resolve(path.endsWith('sources/')
    ? {sources: [{id: 2, kind: 'panel', enabled}], hosts: []} : {cards}));
  http.post.mockImplementation((path, body) => Promise.resolve({key: body.key, snapshot: {status: 'ok',
    sampled_at: Date.now() / 1000, metrics: {cpu: {percent: 42, cores: 2}}}, retry_after: 60, retry_reason: 'collected'}));
  container = document.createElement('div'); document.body.appendChild(container);
});
afterEach(async () => {
  act(() => {ReactDOM.unmountComponentAtNode(container);}); await flush(); container.remove(); jest.resetAllMocks();
});
async function open() {
  await act(async () => {ReactDOM.render(<ResourceMonitorProvider kind="panel"><ResourceMonitorButton id={2} name="客户面板"/></ResourceMonitorProvider>, container); await flush();});
  await act(async () => {container.querySelector('[aria-label="查看客户面板监控"]').click(); await flush();});
}

test('disabled monitoring reports the reason without opening details or sampling', async () => {
  await open();
  expect(message.info).toHaveBeenCalledWith('未开启监控');
  expect(container.querySelector('[role=dialog]')).toBeNull();
  expect(http.post).not.toHaveBeenCalled();
  expect(http.get).toHaveBeenCalledTimes(1);
});

test('panel details use the matching merged card and collect only that target', async () => {
  enabled = true; await open();
  expect(container.querySelector('[role=dialog]').textContent).toContain('42%');
  expect(container.textContent).toContain('合并服务器');
  expect(http.post.mock.calls.map(([, body]) => body)).toEqual([{key: 'host:9', interval: 60}]);
  expect(container.textContent).not.toContain('关联设置');
  await act(async () => {[...container.querySelectorAll('button')].find(b => b.textContent === '立即采集').click(); await flush();});
  expect(http.post.mock.calls[1][1]).toEqual({key: 'host:9', interval: 30});
});

test('missing monitor permission and a source disabled elsewhere never start collection', async () => {
  enabled = true; hasPermission.mockReturnValue(false); await open();
  expect(message.info).toHaveBeenCalledWith('没有查看监控的权限');
  expect(http.post).not.toHaveBeenCalled();
  hasPermission.mockReturnValue(true); cards = [];
  await act(async () => {container.querySelector('[aria-label="查看客户面板监控"]').click(); await flush();});
  expect(container.textContent).toContain('未开启监控或已无权查看');
  expect(http.post).not.toHaveBeenCalled();
});

test('closing a pending detail discards its response and releases queued work', async () => {
  enabled = true; let complete;
  http.post.mockReturnValue(new Promise(resolve => {complete = resolve;})); await open();
  await act(async () => {[...container.querySelectorAll('button')].find(b => b.textContent === '关闭详情').click(); await flush();});
  await act(async () => {complete({key: 'host:9', snapshot: {status: 'ok', metrics: {cpu: {percent: 99}}}}); await flush();});
  expect(container.querySelector('[role=dialog]')).toBeNull();
  expect(container.textContent).not.toContain('99%');
  expect(http.post).toHaveBeenCalledTimes(1);
});
