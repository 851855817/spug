import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { hasPermission, http } from 'libs';
import BaotaIndex from './index';
import { message } from 'antd';

jest.mock('libs', () => ({hasPermission: jest.fn(), http: {get: jest.fn(), post: jest.fn()}}));
jest.mock('components', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  Wrapper.Item = Wrapper;
  return {AuthDiv: Wrapper, Breadcrumb: Wrapper};
});
jest.mock('./Group', () => () => null);
jest.mock('./PanelForm', () => () => null);
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  return {
    Button: ({children, onClick, disabled, loading, ...props}) => <button onClick={onClick} disabled={disabled || loading}
      aria-label={props['aria-label']} aria-busy={Boolean(loading)}>{children}</button>,
    Switch: ({checked, disabled, loading, onChange}) => <button role="switch" aria-checked={checked} disabled={disabled || loading}
      onClick={() => onChange(!checked)}/>,
    Card: Wrapper, Col: Wrapper, Row: Wrapper, Space: Wrapper, Tag: Wrapper, Tooltip: Wrapper,
    Typography: {Text: Wrapper, Paragraph: Wrapper}, Input: {Search: () => null},
    Empty: () => null, Modal: {confirm: jest.fn()}, message: {success: jest.fn(), info: jest.fn(), error: jest.fn()},
    Table: ({columns, dataSource}) => <div>{dataSource.map(panel => <div key={panel.id} data-panel-id={panel.id}>
      {columns.map(column => <div key={column.key || column.dataIndex}>{column.render
        ? column.render(panel[column.dataIndex], panel) : panel[column.dataIndex]}</div>)}
    </div>)}</div>,
  };
});

let container;
let panels;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  panels = [
    {id: 1, name: 'One', url: 'https://one/login', username: 'one', group_id: 1,
      api_base_url: 'https://one', has_api_key: true},
    {id: 2, name: 'Two', url: 'https://two/login', username: 'two', group_id: 1,
      api_base_url: 'https://two', has_api_key: true},
  ];
  hasPermission.mockImplementation(permission => ['baota.panel.view', 'baota.panel.edit'].includes(permission));
  http.get.mockImplementation(path => Promise.resolve(path.endsWith('sources/') ?
    {sources: panels.map(panel => ({id: panel.id, kind: 'panel', enabled: panel.id === 2})), hosts: []} :
    path.endsWith('group/') ? [{id: 1, name: 'Test'}] : panels));
  http.post.mockReset();
});

afterEach(() => {
  act(() => { ReactDOM.unmountComponentAtNode(container); });
  container.remove();
  jest.clearAllMocks();
});

async function renderPage() {
  await act(async () => { ReactDOM.render(<BaotaIndex/>, container); });
}

function testButtons() {
  return [...container.querySelectorAll('button')].filter(button => button.textContent === '测试连接');
}

test('row connection check submits saved id, loads only its row and refreshes successful status', async () => {
  let completeCheck;
  http.post.mockReturnValue(new Promise(resolve => { completeCheck = resolve; }));
  await renderPage();
  expect(testButtons()).toHaveLength(2);
  act(() => testButtons()[0].click());
  expect(http.post).toHaveBeenCalledWith('/api/baota/connection/', {id: 1});
  expect(testButtons()[0].getAttribute('aria-busy')).toBe('true');
  expect(testButtons()[1].getAttribute('aria-busy')).toBe('false');
  panels = [{...panels[0], api_checked_at: '2026-10-22 19:00:00', api_version: '13.1.0'}, panels[1]];
  await act(async () => { completeCheck(panels[0]); });
  expect(container.querySelector('[data-panel-id="1"]').textContent).toContain('宝塔 13.1.0');
  expect(testButtons()[0].getAttribute('aria-busy')).toBe('false');
});

test('row connection check requires edit permission and disables missing saved credentials', async () => {
  panels[0].has_api_key = false;
  await renderPage();
  expect(testButtons()).toHaveLength(2);
  expect(testButtons()[0].disabled).toBe(true);
  expect(testButtons()[1].disabled).toBe(false);
  hasPermission.mockImplementation(permission => permission === 'baota.panel.view');
  await renderPage();
  expect(testButtons()).toHaveLength(0);
});

test('monitor switches share one source read, persist through settings and reread with list refresh', async () => {
  await renderPage();
  const switches = container.querySelectorAll('[role=switch]');
  expect(switches).toHaveLength(2);
  expect(http.get.mock.calls.filter(([path]) => path.endsWith('sources/'))).toHaveLength(1);
  expect(switches[0].getAttribute('aria-checked')).toBe('false');
  expect(switches[1].getAttribute('aria-checked')).toBe('true');
  http.post.mockResolvedValue({id: 1, kind: 'panel', enabled: true});
  await act(async () => {switches[0].click();});
  expect(http.post).toHaveBeenCalledWith('/api/baota/monitor/settings/', {kind: 'panel', id: 1, enabled: true});
  expect(switches[0].getAttribute('aria-checked')).toBe('true');
  await act(async () => {[...container.querySelectorAll('button')].find(button => button.textContent === '刷新').click();});
  expect(http.get.mock.calls.filter(([path]) => path.endsWith('sources/'))).toHaveLength(2);
});

test('readonly panel monitor switches show persisted value without permitting writes', async () => {
  hasPermission.mockImplementation(permission => permission === 'baota.panel.view');
  await renderPage();
  expect([...container.querySelectorAll('[role=switch]')].map(node => node.disabled)).toEqual([true, true]);
});

test('panel list shows only the address host and does not render login URL or account cells', async () => {
  panels[0] = {...panels[0], url: 'http://192.0.2.10:8888/private-entry', username: 'private-account'};
  await renderPage();
  const row = container.querySelector('[data-panel-id="1"]');
  expect(row.textContent).toContain('192.0.2.10');
  expect(row.textContent).not.toContain('private-entry');
  expect(row.textContent).not.toContain('private-account');
  expect(row.querySelector('a').getAttribute('href')).toBe(panels[0].url);
});

test('panel monitor icon remains clickable when disabled and reports the reason', async () => {
  await renderPage();
  await act(async () => {container.querySelector('[aria-label="查看One监控"]').click();});
  expect(message.info).toHaveBeenCalledWith('未开启监控');
  expect(http.post).not.toHaveBeenCalled();
});

function openButtons() {
  return [...container.querySelectorAll('button')].filter(button => button.textContent.trim() === '打开');
}

function enableLogin() {
  hasPermission.mockImplementation(permission => ['baota.panel.view', 'baota.panel.login'].includes(permission));
}

function mockWindow() {
  const popup = {opener: window, closed: false, document: document.implementation.createHTMLDocument(''),
    location: {replace: jest.fn()}, close: jest.fn(), navigate: jest.fn()};
  popup.document.addEventListener('click', event => {
    event.preventDefault();
    popup.navigate(event.target.href);
  });
  jest.spyOn(window, 'open').mockReturnValue(popup);
  return popup;
}

afterEach(() => jest.restoreAllMocks());

test('one click login opens tab synchronously, sends only id, prevents duplicate and navigates without opener/referrer', async () => {
  enableLogin();
  const popup = mockWindow();
  let finish;
  http.post.mockReturnValue(new Promise(resolve => {finish = resolve;}));
  await renderPage();
  act(() => {openButtons()[0].click(); openButtons()[0].click();});
  expect(window.open).toHaveBeenCalledTimes(1);
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(http.post).toHaveBeenCalledWith('/api/baota/login/', {id: 1});
  expect(popup.opener).toBeNull();
  expect(popup.document.querySelector('meta[name="referrer"]').content).toBe('no-referrer');
  expect(openButtons()[0].getAttribute('aria-busy')).toBe('true');
  expect(openButtons()[1].getAttribute('aria-busy')).toBe('false');
  const url = 'https://one/login?tmp_token=' + 'a'.repeat(64);
  await act(async () => finish({url}));
  expect(popup.navigate).toHaveBeenCalledWith(url);
  const anchor = popup.document.querySelector('a');
  expect(anchor.rel).toBe('noreferrer noopener');
  expect(anchor.referrerPolicy).toBe('no-referrer');
  expect(openButtons()[0].getAttribute('aria-busy')).toBe('false');
});

test('one click login requires dedicated permission while preserving explicit login page link', async () => {
  await renderPage();
  expect(openButtons()).toHaveLength(0);
  const link = container.querySelector('[data-panel-id="1"] a');
  expect(link.textContent).toBe('登录页');
  expect(link.rel).toBe('noopener noreferrer');
});

test('missing API is explained without opening a tab or minting a ticket', async () => {
  enableLogin();
  panels[0].has_api_key = false;
  mockWindow();
  await renderPage();
  await act(async () => openButtons()[0].click());
  expect(window.open).not.toHaveBeenCalled();
  expect(http.post).not.toHaveBeenCalled();
  expect(message.info).toHaveBeenCalledWith(expect.stringContaining('API'));
});

test('blocked popup never mints a ticket', async () => {
  enableLogin();
  jest.spyOn(window, 'open').mockReturnValue(null);
  await renderPage();
  await act(async () => openButtons()[0].click());
  expect(http.post).not.toHaveBeenCalled();
  expect(message.info).toHaveBeenCalledWith(expect.stringContaining('弹出'));
});

test('failed request closes blank tab and clears row loading', async () => {
  enableLogin();
  const popup = mockWindow();
  http.post.mockRejectedValue(new Error('already displayed by http'));
  await renderPage();
  await act(async () => openButtons()[0].click());
  expect(popup.close).toHaveBeenCalledTimes(1);
  expect(popup.location.replace).not.toHaveBeenCalled();
  expect(popup.navigate).not.toHaveBeenCalled();
  expect(openButtons()[0].getAttribute('aria-busy')).toBe('false');
});

test.each(['javascript:alert(1)', 'https://evil.test/login?tmp_token=' + 'a'.repeat(64),
  'https://one/wrong?tmp_token=' + 'a'.repeat(64), 'https://one/login?tmp_token=short',
  'https://user:password@one/login?tmp_token=' + 'a'.repeat(64)])('rejects unexpected login destination %s', async url => {
  enableLogin();
  const popup = mockWindow();
  http.post.mockResolvedValue({url});
  await renderPage();
  await act(async () => openButtons()[0].click());
  expect(popup.close).toHaveBeenCalledTimes(1);
  expect(popup.location.replace).not.toHaveBeenCalled();
  expect(popup.navigate).not.toHaveBeenCalled();
  expect(message.error).toHaveBeenCalledWith('登录地址校验失败，请刷新列表后重试');
});
