import React from 'react';
import ReactDOM from 'react-dom';
import {act, Simulate} from 'react-dom/test-utils';
import ResourcePolicy, {EffectivePreview} from './ResourcePolicy';
import http from 'libs/http';
jest.mock('libs/http', () => ({get: jest.fn()}));
jest.mock('antd', () => ({
  Alert: ({message}) => <div>{message}</div>, Spin: () => <span>加载中</span>,
  Select: ({options, value, onChange, ...props}) => <select aria-label={props['aria-label']} value={value} onChange={e => onChange(e.target.value)}>{options.map(row => <option key={row.value} value={row.value}>{row.label}</option>)}</select>,
  InputNumber: ({value, onChange, ...props}) => <input aria-label={props['aria-label']} type="number" value={value} onChange={e => onChange(Number(e.target.value))}/>,
}));
let node;
beforeEach(() => {node = document.createElement('div'); document.body.appendChild(node);
  http.get.mockResolvedValue({quota: {used: 1, limit: 2, remaining: 1}, grants: [{kind: 'panel', id: 9, level: 'read'}],
    available_hosts: [], available_panels: [{id: 9, name: '客户面板'}], hosts: [], panels: [{id: 9, name: '客户面板', level: 'read'}], permissions: ['baota.panel.view']});});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(node);}); node.remove(); jest.clearAllMocks();});
test('editing loads the saved grants and effective preview without silently replacing the account policy', async () => {
  const onChange = jest.fn();
  await act(async () => {ReactDOM.render(<ResourcePolicy userId={7} onChange={onChange}/>, node); await Promise.resolve();});
  expect(http.get).toHaveBeenCalledWith('/api/account/resources/', {params: {user_id: 7}});
  expect(onChange).not.toHaveBeenCalled();
  expect(node.textContent).toContain('已用 1 / 2');
  expect(node.textContent).toContain('baota.panel.view');
  expect(node.querySelector('[aria-label="panel-9授权"]').value).toBe('read');
  act(() => {Simulate.change(node.querySelector('[aria-label="额度模式"]'), {target: {value: 'unlimited'}});});
  expect(onChange).toHaveBeenLastCalledWith({limit: null});
  act(() => {Simulate.change(node.querySelector('[aria-label="panel-9授权"]'), {target: {value: 'manage'}});});
  expect(onChange).toHaveBeenLastCalledWith({limit: null, grants: [{kind: 'panel', id: 9, level: 'manage'}]});
});

test('effective preview labels owned and assigned resources independently of explicit grant rows', () => {
  const data = {hosts: [{id: 1, name: '本人主机', source: 'owned', level: 'manage'},
    {id: 2, name: '客户主机', source: 'assigned', level: 'read'}],
    panels: [{id: 3, name: '客户面板', source: 'assigned', level: 'manage'}],
    grants: [{kind: 'host', id: 1, level: 'read'}]};
  act(() => {ReactDOM.render(<EffectivePreview data={data}/>, node);});
  expect(node.textContent).toContain('本人主机 #1 · 可管理 · 本人资源');
  expect(node.textContent).toContain('客户主机 #2 · 只读 · 管理员分配');
  expect(node.textContent).toContain('客户面板 #3 · 可管理 · 管理员分配');
  expect(data.grants).toEqual([{kind: 'host', id: 1, level: 'read'}]);
});
