import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { http } from 'libs';
import TaskForm from './TaskForm';

jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}}));
jest.mock('../../baota/helpers', () => ({buildGroupTree: () => [], descendantIds: () => new Set()}));
jest.mock('./OperationForm', () => ({panel, value, onChange}) => <button onClick={() => onChange({...value, all_sites: true, enabled: panel.id === 1})}>设置 {panel.name}</button>);
jest.mock('./SiteGrid', () => () => null);
jest.mock('./UploadForm', () => ({panels, onPrepared, onBusyChange}) => <div>上传目标 {panels.length}
  <button onClick={() => onBusyChange(true)}>模拟暂存</button><button onClick={() => {onBusyChange(false); onPrepared(13);}}>模拟完成</button></div>);
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  const Select = ({value, onChange, mode, options = [], placeholder, disabled}) => <select disabled={disabled} aria-label={placeholder} multiple={mode === 'multiple'}
    value={value || (mode ? [] : '')} onChange={e => onChange(mode ? [...e.target.selectedOptions].map(x => Number(x.value)) : e.target.value)}>
    <option value="">选择</option>{options.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
  </select>;
  return {Card: Wrapper, Space: Wrapper, Typography: {Paragraph: Wrapper, Text: Wrapper}, Form: Object.assign(Wrapper, {Item: Wrapper}),
    Alert: ({message, description}) => <div>{message}{description}</div>, Collapse: Object.assign(Wrapper, {Panel: Wrapper}), Select, TreeSelect: () => null,
    Button: ({children, onClick, loading, disabled}) => <button onClick={onClick} disabled={loading || disabled}>{children}</button>, message: {error: jest.fn()}};
});
let container;
beforeEach(() => {container = document.createElement('div'); document.body.appendChild(container); http.post.mockResolvedValue({id: 9});});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.clearAllMocks();});
test('preflight submits separate panel parameters; preparing never sends start or mutable preview', async () => {
  const prepared = jest.fn();
  const options = {panels: [{id: 1, name: 'A', api_configured: true, api_checked: true, api_version: '13.1.0'}, {id: 2, name: 'B', api_configured: true, api_checked: true, api_version: '11.7.0'}], groups: [], operations: [{code: 'site_basedir', label: '防跨站'}]};
  await act(async () => {ReactDOM.render(<TaskForm options={options} onPrepared={prepared}/>, container);});
  const selects = container.querySelectorAll('select');
  act(() => {for (const option of selects[0].options) option.selected = ['1', '2'].includes(option.value); Simulate.change(selects[0]);});
  act(() => {selects[1].value = 'site_basedir'; Simulate.change(selects[1]);});
  const buttons = () => [...container.querySelectorAll('button')];
  act(() => {buttons().find(x => x.textContent === '设置 A').click(); buttons().find(x => x.textContent === '设置 B').click();});
  await act(async () => {buttons().find(x => x.textContent === '生成冻结预检').click();});
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(http.post).toHaveBeenCalledWith('/api/baota/tasks/', {operation: 'site_basedir', panel_ids: [1, 2], params_by_panel: {'1': {all_sites: true, enabled: true}, '2': {all_sites: true, enabled: false}}});
  expect(prepared).toHaveBeenCalledWith(9);
});

test('the operation picker includes PHP updates and SSL has an explicit apply or renew choice', async () => {
  const codes = ['panel_upgrade', 'site_add', 'site_basedir', 'site_rewrite', 'ssl_apply', 'ssl_renew', 'software_install', 'php_extension_install', 'software_uninstall', 'php_update'];
  await act(async () => {ReactDOM.render(<TaskForm options={{panels: [], groups: [], operations: codes.map(code => ({code, label: code}))}}/>, container);});
  const operation = container.querySelectorAll('select')[1];
  expect(operation.options).toHaveLength(10); // Nine categories plus the empty choice.
  act(() => {operation.value = 'ssl'; Simulate.change(operation);});
  const ssl = container.querySelectorAll('select')[2];
  expect([...ssl.options].map(item => item.value)).toEqual(['', 'ssl_apply', 'ssl_renew']);
  expect(ssl.value).toBe('ssl_apply');
  act(() => {ssl.value = 'ssl_renew'; Simulate.change(ssl);});
  expect(ssl.value).toBe('ssl_renew');
});

test('file upload replaces generic operation forms and locks selectors during staging', async () => {
  const prepared = jest.fn();
  const options = {panels: [{id: 1, name: 'A'}], groups: [], operations: [{code: 'file_upload', label: '上传文件 / 目录'}]};
  await act(async () => {ReactDOM.render(<TaskForm options={options} onPrepared={prepared}/>, container);});
  const selects = container.querySelectorAll('select');
  act(() => {selects[0].options[1].selected = true; Simulate.change(selects[0]); selects[1].value = 'file_upload'; Simulate.change(selects[1]);});
  expect(container.textContent).toContain('上传目标 1');
  expect(container.textContent).not.toContain('200 个子项目');
  expect(container.textContent).not.toContain('设置 A');
  expect(container.textContent).not.toContain('生成冻结预检');
  act(() => {[...container.querySelectorAll('button')].find(item => item.textContent === '模拟暂存').click();});
  expect([...container.querySelectorAll('select')].every(select => select.disabled)).toBe(true);
  act(() => {[...container.querySelectorAll('button')].find(item => item.textContent === '模拟完成').click();});
  expect(prepared).toHaveBeenCalledWith(13);
  expect([...container.querySelectorAll('select')].every(select => !select.disabled)).toBe(true);
  expect(http.post).not.toHaveBeenCalled();
});
