import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { hasPermission, http } from 'libs';
import PanelPlanSelector, { templateResponse } from './PanelPlanSelector';

jest.mock('libs', () => ({http: {get: jest.fn()}, hasPermission: jest.fn()}));
jest.mock('../exec/panel/TaskForm', () => ({onPrepared}) => <button onClick={() => onPrepared(17)}>模拟生成预检</button>);
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  return {Space: Wrapper, Spin: Wrapper, Typography: {Paragraph: Wrapper, Text: Wrapper},
    Alert: ({message, description, action}) => <div>{message}{description}{action}</div>,
    Modal: ({children, title}) => <div>{title}{children}</div>,
    Select: ({options = [], value, onChange, placeholder}) => <select aria-label={placeholder} value={value || ''} onChange={e => onChange(Number(e.target.value))}>
      <option value=""/>{options.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select>,
    Checkbox: ({children, checked, onChange}) => <label><input type="checkbox" checked={checked} onChange={onChange}/>{children}</label>,
    Button: ({children, onClick, disabled, loading}) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>};
});

let container;
const summary = {template_id: 17, template_token: 'signed', operation: 'file_upload', operation_label: '上传文件 / 目录',
  panels: [{id: 2, name: '客户面板'}], scope: [{panel_id: 2, name: '客户面板', items: ['/www/site/a.txt']}],
  required_acknowledgements: [{code: 'upload_overwrite', label: '确认覆盖同名文件'}], acknowledgements: []};
const button = text => [...container.querySelectorAll('button')].find(item => item.textContent === text);
beforeEach(() => {
  container = document.createElement('div'); document.body.appendChild(container); hasPermission.mockReturnValue(true);
  http.get.mockResolvedValue({items: []});
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.clearAllMocks(); jest.useRealTimers();});

test('existing templates show frozen scope and explicit acknowledgements', async () => {
  const changed = jest.fn();
  http.get.mockResolvedValueOnce({items: [summary]}).mockResolvedValueOnce(summary);
  await act(async () => ReactDOM.render(<PanelPlanSelector onChange={changed}/>, container));
  await act(async () => {const select = container.querySelector('select'); select.value = '17'; Simulate.change(select);});
  expect(http.get).toHaveBeenLastCalledWith('/api/schedule/panel_templates/', expect.objectContaining({params: {id: 17}}));
  const selected = changed.mock.calls[changed.mock.calls.length - 1][0];
  await act(async () => ReactDOM.render(<PanelPlanSelector value={selected} onChange={changed}/>, container));
  expect(container.textContent).toContain('/www/site/a.txt');
  expect(container.querySelector('input[type=checkbox]').checked).toBe(false);
  await act(async () => container.querySelector('input[type=checkbox]').click());
  expect(changed).toHaveBeenLastCalledWith({...summary, acknowledgements: ['upload_overwrite']});
});

test('new preparation polls only the explicit pending response then returns the ready template', async () => {
  jest.useFakeTimers(); const changed = jest.fn();
  http.get.mockResolvedValueOnce({items: []}).mockResolvedValueOnce({panels: [], operations: []})
    .mockRejectedValueOnce('预检尚未完成，请稍后刷新').mockResolvedValueOnce(summary);
  await act(async () => ReactDOM.render(<PanelPlanSelector onChange={changed}/>, container));
  await act(async () => button('新建宝塔预检').click());
  await act(async () => button('模拟生成预检').click());
  expect(container.textContent).toContain('等待预检');
  expect(changed).not.toHaveBeenCalledWith(summary);
  await act(async () => jest.advanceTimersByTime(2500));
  expect(changed).toHaveBeenLastCalledWith(summary);
  expect(http.get.mock.calls.filter(([url]) => url.includes('panel_templates'))).toHaveLength(3);
});

test('a failed preparation stops polling and a closed picker cannot accept stale results', async () => {
  jest.useFakeTimers(); const changed = jest.fn();
  http.get.mockResolvedValueOnce({items: [summary]}).mockRejectedValueOnce('固定网站身份已变化');
  await act(async () => ReactDOM.render(<PanelPlanSelector onChange={changed}/>, container));
  await act(async () => {const select = container.querySelector('select'); select.value = '17'; Simulate.change(select);});
  expect(container.textContent).toContain('固定网站身份已变化');
  await act(async () => jest.advanceTimersByTime(60000));
  expect(http.get).toHaveBeenCalledTimes(2);
  let resolve;
  http.get.mockImplementationOnce(() => new Promise(done => {resolve = done;}));
  await act(async () => button('重新读取预检').click());
  act(() => {ReactDOM.unmountComponentAtNode(container);});
  await act(async () => resolve(summary));
  expect(changed).not.toHaveBeenCalledWith(summary);
});

test('templates are never read without panel view permission', async () => {
  hasPermission.mockReturnValue(false);
  await act(async () => ReactDOM.render(<PanelPlanSelector onChange={() => {}}/>, container));
  expect(http.get).not.toHaveBeenCalled();
  expect(button('新建宝塔预检')).toBeUndefined();
});

test('view permission alone does not request templates or permit creating a new one', async () => {
  hasPermission.mockImplementation(permission => permission === 'exec.panel.view');
  await act(async () => ReactDOM.render(<PanelPlanSelector onChange={() => {}}/>, container));
  expect(http.get).not.toHaveBeenCalled();
  expect(button('新建宝塔预检')).toBeUndefined();
});

test('pending polling is bounded and can be resumed by an explicit retry', async () => {
  jest.useFakeTimers();
  http.get.mockResolvedValueOnce({items: [summary]}).mockRejectedValue('预检尚未完成，请稍后刷新');
  await act(async () => ReactDOM.render(<PanelPlanSelector onChange={() => {}}/>, container));
  await act(async () => {const select = container.querySelector('select'); select.value = '17'; Simulate.change(select);});
  for (let index = 0; index < 240; index++) await act(async () => jest.advanceTimersByTime(2500));
  expect(container.textContent).toContain('已停止自动刷新');
  expect(http.get).toHaveBeenCalledTimes(241);
  expect(button('重新读取预检')).toBeDefined();
});

test('only the exact pending envelope is handled quietly; real API failures retain their error', () => {
  expect(templateResponse('{"error":"预检尚未完成，请稍后刷新"}')).toEqual({data: {pending: true}});
  expect(templateResponse('{"error":"权限拒绝"}')).toEqual({error: '权限拒绝'});
  expect(templateResponse('{"data":{"template_id":17}}')).toEqual({data: {template_id: 17}});
});
