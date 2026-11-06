import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { Modal } from 'antd';
import { hasPermission, http } from 'libs';
import PlatformHealth from './index';

jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, hasPermission: jest.fn()}));
jest.mock('components', () => {
  const Breadcrumb = ({children}) => <nav>{children}</nav>;
  Breadcrumb.Item = ({children}) => <span>{children}</span>;
  return {Breadcrumb};
});
jest.mock('antd', () => ({
  Alert: ({message, description}) => <div role="alert">{message}{description}</div>,
  Card: ({children, title, className}) => <section className={className}>{title}{children}</section>,
  Tag: ({children}) => <span>{children}</span>,
  Empty: ({description}) => <div>{description}</div>,
  Button: ({children, onClick, disabled, loading}) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>,
  Modal: {confirm: jest.fn()},
}));

let container;
const check = (overrides = {}) => ({id: 'process.spug-monitor', name: '监控服务', status: 'warning',
  summary: '监控服务已停止', advice: '确认配置后启动服务', service: 'spug-monitor', process_state: 'STOPPED',
  recoverable: true, ...overrides});
const snapshot = (overrides = {}) => ({checked_at: '2026-10-24T08:15:00Z', status: 'warning', checks: [
  {id: 'database', name: '数据库', status: 'ok', summary: '数据库连接正常', advice: '无需处理'}, check(),
], limitations: ['进程运行不等于业务任务执行成功。', '本页不查询客户服务器。'], ...overrides});
const flush = async () => {for (let i = 0; i < 10; i++) await Promise.resolve();};
const button = text => [...container.querySelectorAll('button')].find(node => node.textContent === text);
const overview = () => container.querySelector('[data-health-overview]');
const render = async () => {await act(async () => {ReactDOM.render(<PlatformHealth/>, container); await flush();});};
const click = async node => {await act(async () => {node.click(); await flush();});};
beforeEach(() => {
  container = document.createElement('div'); document.body.appendChild(container);
  hasPermission.mockImplementation(permission => permission === 'admin');
  http.get.mockResolvedValue(snapshot());
  http.post.mockResolvedValue({status: 'started', summary: '启动命令已完成，请核对检查结果。'});
});
afterEach(() => {
  act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove();
  jest.restoreAllMocks(); jest.resetAllMocks(); jest.useRealTimers();
});

test('non-administrators see the access restriction and never request a health snapshot', async () => {
  hasPermission.mockReturnValue(false); await render();
  expect(container.textContent).toContain('仅超级管理员');
  expect(http.get).not.toHaveBeenCalled(); expect(http.post).not.toHaveBeenCalled();
});

test('the initial manual snapshot includes check time, evidence, advice and scope without polling', async () => {
  jest.useFakeTimers(); await render();
  expect(http.get).toHaveBeenCalledWith('/api/setting/health/');
  expect(overview().getAttribute('data-status')).toBe('warning');
  expect(container.querySelector('time').getAttribute('datetime')).toBe('2026-10-24T08:15:00Z');
  expect(container.textContent).toContain('数据库连接正常');
  expect(container.textContent).toContain('确认配置后启动服务');
  expect(container.textContent).toContain('进程运行不等于业务任务执行成功');
  expect(container.textContent).toContain('本页不查询客户服务器');
  act(() => jest.advanceTimersByTime(300000));
  expect(http.get).toHaveBeenCalledTimes(1);
});

test('a manual check replaces the check time and snapshot with the new result', async () => {
  await render();
  http.get.mockResolvedValue(snapshot({checked_at: '2026-10-24T08:20:00Z', status: 'ok', checks: [
    check({status: 'ok', summary: '监控服务正在运行', process_state: 'RUNNING', recoverable: false}),
  ]}));
  await click(button('立即检查'));
  expect(http.get).toHaveBeenCalledTimes(2);
  expect(container.querySelector('time').getAttribute('datetime')).toBe('2026-10-24T08:20:00Z');
  expect(overview().getAttribute('data-status')).toBe('ok');
  expect(container.textContent).toContain('监控服务正在运行');
  expect(button('启动服务')).toBeUndefined();
});

test('an initial query failure gives retry feedback without claiming a healthy or empty snapshot', async () => {
  http.get.mockRejectedValue('unavailable'); await render();
  expect(container.textContent).toContain('健康检查失败');
  expect(overview().getAttribute('data-status')).toBe('unknown');
  expect(button('立即检查').disabled).toBe(false);
  expect(button('下载报告').disabled).toBe(true);
});

test('a failed refresh labels retained data stale, removes a healthy overview and disables recovery', async () => {
  http.get.mockResolvedValue(snapshot({status: 'ok'})); await render();
  http.get.mockRejectedValue('unavailable'); await click(button('立即检查'));
  expect(container.textContent).toContain('健康检查失败');
  expect(container.textContent).toContain('上次结果已过期');
  expect(container.textContent).toContain('数据库连接正常');
  expect(overview().getAttribute('data-status')).toBe('unknown');
  expect(button('启动服务')).toBeUndefined();
});

test('recovery needs a stopped fixed service and an explicit recoverable flag', async () => {
  const services = ['nginx', 'spug-api', 'spug-monitor', 'spug-panel-worker', 'spug-scheduler', 'spug-worker', 'spug-ws'];
  http.get.mockResolvedValue(snapshot({checks: [
    ...services.map((service, index) => check({id: service, service, process_state: ['STOPPED', 'EXITED', 'FATAL'][index % 3]})),
    check({id: 'redis', service: 'redis'}), check({id: 'injected', service: 'spug-api; ls'}),
    check({id: 'running', process_state: 'RUNNING'}), check({id: 'unknown', process_state: 'UNKNOWN'}),
    check({id: 'starting', process_state: 'STARTING'}), check({id: 'backoff', process_state: 'BACKOFF'}),
    check({id: 'not-recoverable', recoverable: false}), check({id: 'missing-state', process_state: undefined}),
  ]}));
  await render();
  expect([...container.querySelectorAll('button')].filter(node => node.textContent === '启动服务')).toHaveLength(7);
  expect(http.post).not.toHaveBeenCalled();
});

test('starting requires confirmation and a fresh query; command completion never implies health', async () => {
  await render(); await click(button('启动服务'));
  expect(http.post).not.toHaveBeenCalled();
  expect(Modal.confirm).toHaveBeenCalledTimes(1);
  const confirmation = Modal.confirm.mock.calls[0][0];
  expect(confirmation.content).toContain('spug-monitor');
  expect(container.textContent).toContain('启动可能');
  let completeRead;
  http.get.mockImplementation(() => new Promise(resolve => {completeRead = resolve;}));
  let pending;
  await act(async () => {pending = confirmation.onOk(); await flush();});
  expect(http.post).toHaveBeenCalledWith('/api/setting/health/', {action: 'start', service: 'spug-monitor'});
  expect(http.get).toHaveBeenCalledTimes(2);
  expect(container.textContent).toContain('启动命令已完成，请核对检查结果。');
  expect(overview().getAttribute('data-status')).toBe('unknown');
  expect(button('立即检查').disabled).toBe(true);
  await act(async () => {await confirmation.onOk(); await flush();});
  expect(http.post).toHaveBeenCalledTimes(1);
  await act(async () => {completeRead(snapshot({status: 'warning'})); await pending; await flush();});
  expect(overview().getAttribute('data-status')).toBe('warning');
});

test.each(['uncertain', 'refused', 'rejected'])('%s start results stay explicit and are followed by one read without resending', async result => {
  jest.useFakeTimers(); await render(); await click(button('启动服务'));
  if (result === 'rejected') http.post.mockRejectedValue('timeout');
  else http.post.mockResolvedValue({status: result, summary: result === 'uncertain' ? '启动结果待核实，请查看实际状态。' : '当前状态不允许启动。'});
  await act(async () => {await Modal.confirm.mock.calls[0][0].onOk(); await flush();});
  expect(container.textContent).toContain(result === 'refused' ? '当前状态不允许启动' : '待核实');
  expect(http.get).toHaveBeenCalledTimes(2);
  act(() => jest.advanceTimersByTime(60000));
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(overview().getAttribute('data-status')).toBe('warning');
});

test('an old confirmation cannot start a service after a new snapshot marks it running', async () => {
  await render(); await click(button('启动服务'));
  http.get.mockResolvedValue(snapshot({status: 'ok', checks: [check({process_state: 'RUNNING', recoverable: false})]}));
  await click(button('立即检查'));
  await act(async () => {await Modal.confirm.mock.calls[0][0].onOk(); await flush();});
  expect(http.post).not.toHaveBeenCalled();
});

test('a new snapshot invalidates an old confirmation even if the service still appears stopped', async () => {
  await render(); await click(button('启动服务'));
  http.get.mockResolvedValue(snapshot({checked_at: '2026-10-24T08:20:00Z'}));
  await click(button('立即检查'));
  await act(async () => {await Modal.confirm.mock.calls[0][0].onOk(); await flush();});
  expect(http.post).not.toHaveBeenCalled();
  expect(container.textContent).toContain('请重新检查后再操作');
});

test.each(['fresh', 'failed', 'recovered'])('report download exports the %s snapshot without querying or exposing a query error', async state => {
  let expected = snapshot();
  await render();
  if (state !== 'fresh') {
    http.get.mockRejectedValue(new Error('private internal response'));
    await click(button('立即检查'));
  }
  if (state === 'recovered') {
    expected = snapshot({checked_at: '2026-10-24T08:20:00Z', status: 'ok', checks: []});
    http.get.mockResolvedValue(expected);
    await click(button('立即检查'));
    expect(overview().getAttribute('data-status')).toBe('ok');
    expect(container.textContent).not.toContain('健康检查失败');
  }
  const readsBefore = http.get.mock.calls.length;
  let exported;
  const createURL = jest.fn(blob => {exported = blob; return 'blob:health-report';});
  const revokeURL = jest.fn();
  const createDescriptor = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
  const revokeDescriptor = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
  Object.defineProperty(URL, 'createObjectURL', {configurable: true, value: createURL});
  Object.defineProperty(URL, 'revokeObjectURL', {configurable: true, value: revokeURL});
  const download = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  try {
  await click(button('下载报告'));
  expect(download).toHaveBeenCalledTimes(1);
  expect(exported.type).toBe('application/json;charset=utf-8');
  const body = await new Promise(resolve => {const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.readAsText(exported);});
  const report = JSON.parse(body);
  expect(report).toMatchObject({...expected, snapshot_stale: state === 'failed',
    query_error: state === 'failed' ? '最近一次健康检查失败，当前内容为上次快照。' : null});
  expect(Number.isNaN(Date.parse(report.report_exported_at))).toBe(false);
  expect(body).not.toContain('private internal response');
  expect(http.get).toHaveBeenCalledTimes(readsBefore);
  expect(http.post).not.toHaveBeenCalled();
  expect(revokeURL).toHaveBeenCalledWith('blob:health-report');
  } finally {
    download.mockRestore();
    if (createDescriptor) Object.defineProperty(URL, 'createObjectURL', createDescriptor); else delete URL.createObjectURL;
    if (revokeDescriptor) Object.defineProperty(URL, 'revokeObjectURL', revokeDescriptor); else delete URL.revokeObjectURL;
  }
});
