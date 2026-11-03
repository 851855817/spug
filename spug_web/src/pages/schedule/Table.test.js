import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { Modal } from 'antd';
import { hasPermission, http } from 'libs';
import store from './store';
import ScheduleTable from './Table';

jest.mock('mobx-react', () => ({observer: item => item}));
jest.mock('libs', () => ({http: {post: jest.fn(), patch: jest.fn()}, hasPermission: jest.fn(), t: value => value}));
jest.mock('./store', () => ({fetchRecords: jest.fn(), showInfo: jest.fn(), showForm: jest.fn(), dataSource: []}));
jest.mock('./PanelPreview', () => ({taskId}) => <div>只读面板任务 #{taskId}</div>);
jest.mock('components', () => {
  const React = require('react');
  const Button = ({children, onClick, disabled}) => <button disabled={disabled} onClick={onClick}>{children}</button>;
  const Action = ({children}) => <div>{children}</div>; Action.Button = Button;
  return {Action, LinkButton: Button, AuthButton: Button,
    TableCard: ({columns, dataSource}) => <div>{dataSource.map(row => <div key={row.id}>{columns.map((col, index) =>
      <span key={index}>{col.render ? col.render(row) : row[col.dataIndex]}</span>)}</div>)}</div>};
});
jest.mock('antd', () => {
  const React = require('react'); const Wrapper = ({children}) => <div>{children}</div>;
  const Modal = ({children}) => <div>{children}</div>; Modal.confirm = jest.fn();
  return {Modal, Tag: Wrapper, Space: Wrapper, Typography: {Paragraph: Wrapper, Text: Wrapper},
    Dropdown: ({menu}) => <div>{menu.items.map((item, index) => <span key={index}>{item.label}</span>)}</div>,
    Radio: {Group: Wrapper, Button: Wrapper}, message: {success: jest.fn()}};
});

let container;
const button = text => [...container.querySelectorAll('button')].find(item => item.textContent === text);
const plan = {template_id: 7, operation_label: '防跨站', panels: [{id: 2, name: '客户面板'}],
  scope: [{panel_id: 2, name: '客户面板', items: ['example.com']}], required_acknowledgements: []};
beforeEach(() => {
  container = document.createElement('div'); document.body.appendChild(container); hasPermission.mockReturnValue(true);
  store.dataSource = [{id: 5, name: '定时任务', interpreter: 'baota', is_active: false, panel_plan: plan}];
  http.patch.mockResolvedValue({}); http.post.mockResolvedValue({panel_task_id: 42, mode: 'preflight'});
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.clearAllMocks();});

test('activation explicitly confirms the frozen scope and automatic preflight and execution', async () => {
  await act(async () => ReactDOM.render(<ScheduleTable/>, container));
  await act(async () => button('激活任务').click());
  const confirm = Modal.confirm.mock.calls[0][0];
  const preview = document.createElement('div'); act(() => {ReactDOM.render(<div>{confirm.content}</div>, preview);});
  expect(preview.textContent).toContain('example.com'); expect(preview.textContent).toContain('自动执行');
  expect(http.patch).not.toHaveBeenCalled();
  await act(async () => confirm.onOk());
  expect(http.patch).toHaveBeenCalledWith('/api/schedule/', {id: 5, is_active: true, confirm_panel: true});
  act(() => {ReactDOM.unmountComponentAtNode(preview);});
});

test('re-preflight opens a read-only task preview and never uses the Shell test result path', async () => {
  await act(async () => ReactDOM.render(<ScheduleTable/>, container));
  expect(button('执行测试')).toBeUndefined();
  await act(async () => button('重新预检').click());
  await act(async () => Modal.confirm.mock.calls[0][0].onOk());
  expect(http.post).toHaveBeenCalledWith('/api/schedule/5/', null, {timeout: 120000});
  expect(container.textContent).toContain('只读面板任务 #42');
  expect(store.showInfo).not.toHaveBeenCalled();
});

test('each panel activation permission is required individually', async () => {
  hasPermission.mockImplementation(permission => permission !== 'exec.panel.execute');
  await act(async () => ReactDOM.render(<ScheduleTable/>, container));
  expect(button('激活任务')).toBeUndefined();
  expect(http.patch).not.toHaveBeenCalled();
});

test('Shell test and activation preserve their existing API payloads', async () => {
  store.dataSource = [{id: 6, name: 'shell', interpreter: 'sh', is_active: false}]; http.post.mockResolvedValue(8);
  await act(async () => ReactDOM.render(<ScheduleTable/>, container));
  await act(async () => button('执行测试').click());
  await act(async () => Modal.confirm.mock.calls[0][0].onOk());
  expect(store.showInfo).toHaveBeenCalledWith(store.dataSource[0], 8);
  await act(async () => button('激活任务').click());
  await act(async () => Modal.confirm.mock.calls[1][0].onOk());
  expect(http.patch).toHaveBeenCalledWith('/api/schedule/', {id: 6, is_active: true});
});

test('an automatically stopped panel plan keeps its latest failure visible', async () => {
  store.dataSource[0].latest_status = 2; store.dataSource[0].latest_status_alias = '失败';
  await act(async () => ReactDOM.render(<ScheduleTable/>, container));
  expect(container.textContent).toContain('失败'); expect(container.textContent).toContain('未激活');
});
