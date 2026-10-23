import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { hasPermission, http } from 'libs';
import BaotaIndex from './index';

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
    Button: ({children, onClick, disabled, loading}) => <button onClick={onClick} disabled={disabled || loading}
      aria-busy={Boolean(loading)}>{children}</button>,
    Switch: ({checked, disabled, loading, onChange}) => <button role="switch" aria-checked={checked} disabled={disabled || loading}
      onClick={() => onChange(!checked)}/>,
    Card: Wrapper, Col: Wrapper, Row: Wrapper, Space: Wrapper, Tag: Wrapper,
    Typography: {Text: Wrapper, Paragraph: Wrapper}, Input: {Search: () => null},
    Empty: () => null, Modal: {confirm: jest.fn()}, message: {success: jest.fn()},
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
