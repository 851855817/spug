import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { http, hasPermission } from 'libs';
import UploadResults from './UploadResults';

jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, hasPermission: jest.fn()}));
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  const Input = props => <input {...props}/>; Input.TextArea = props => <textarea {...props}/>;
  return {Alert: ({message}) => <div>{message}</div>, Typography: {Text: Wrapper, Paragraph: Wrapper}, Space: Wrapper,
    Tag: Wrapper, Input, Form: Object.assign(Wrapper, {Item: Wrapper}),
    Select: ({value, onChange, options}) => <select aria-label="判定结果" value={value} onChange={event => onChange(event.target.value)}>
      {options.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select>,
    Button: ({children, onClick, disabled, loading}) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>,
    Table: ({columns, dataSource, pagination}) => <div>{dataSource.map(row => <div key={row.id}>{columns.map((col, index) => <span key={index}>{col.render ? col.render(row[col.dataIndex], row) : row[col.dataIndex]}</span>)}</div>)}
      <button onClick={() => pagination.onChange(2, 20)}>下一页</button></div>,
    Modal: ({visible, title, children, onOk, onCancel, confirmLoading}) => visible ? <div>{title}{children}<button disabled={confirmLoading} onClick={onOk}>保存人工判定</button><button onClick={onCancel}>关闭</button></div> : null};
});

let container;
const item = {id: 9, source_id: 'aa22', relative_path: 'site/a.txt', target_path: '/www/site/a.txt', size: 3, state: 'uncertain', message: '响应未知'};
beforeEach(() => {
  container = document.createElement('div'); document.body.appendChild(container);
  http.get.mockResolvedValue({total: 1, items: [item], page: 1, page_size: 20}); hasPermission.mockReturnValue(true);
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.resetAllMocks(); jest.useRealTimers();});
const button = text => [...container.querySelectorAll('button')].find(node => node.textContent === text);

test('file results stay lazy, paginate, refresh only while expanded and stop after collapse', async () => {
  jest.useFakeTimers();
  await act(async () => {ReactDOM.render(<UploadResults taskId={7} targetId={3} active refreshKey="running"/>, container);});
  expect(http.get).not.toHaveBeenCalled();
  await act(async () => {button('查看文件结果').click();});
  expect(http.get).toHaveBeenLastCalledWith('/api/baota/targets/3/uploads/', {params: {page: 1, page_size: 20}});
  expect(container.textContent).toContain('site/a.txt');
  await act(async () => {button('下一页').click();});
  expect(http.get).toHaveBeenLastCalledWith('/api/baota/targets/3/uploads/', {params: {page: 2, page_size: 20}});
  await act(async () => {jest.advanceTimersByTime(5000);});
  const calls = http.get.mock.calls.length;
  await act(async () => {button('收起文件结果').click(); jest.advanceTimersByTime(10000);});
  expect(http.get).toHaveBeenCalledTimes(calls);
});

test('each uncertain file requires reason and evidence and resolves by immutable source id', async () => {
  const resolved = jest.fn(); http.post.mockResolvedValue({id: 7, state: 'completed'});
  await act(async () => {ReactDOM.render(<UploadResults taskId={7} targetId={3} onResolved={resolved}/>, container);});
  await act(async () => {button('查看文件结果').click();});
  act(() => {button('人工判定此文件').click();});
  await act(async () => {button('保存人工判定').click();});
  expect(http.post).not.toHaveBeenCalled(); expect(container.textContent).toContain('理由和证据');
  act(() => {Simulate.change(container.querySelector('[aria-label="判定理由"]'), {target: {value: '已独立核实'}});
    Simulate.change(container.querySelector('[aria-label="核验证据"]'), {target: {value: '10:00 远端检查文件内容一致'}});});
  await act(async () => {button('保存人工判定').click();});
  expect(http.post).toHaveBeenCalledWith('/api/baota/tasks/7/resolve/', {target_id: 3, child_key: 'file:aa22', outcome: 'success', reason: '已独立核实', evidence: '10:00 远端检查文件内容一致'});
  expect(resolved).toHaveBeenCalledWith({id: 7, state: 'completed'});
});

test('readers cannot resolve and failed loads render a concise error', async () => {
  hasPermission.mockReturnValue(false);
  await act(async () => {ReactDOM.render(<UploadResults taskId={7} targetId={3}/>, container);});
  await act(async () => {button('查看文件结果').click();});
  expect(button('人工判定此文件')).toBeUndefined();
  http.get.mockRejectedValue('error');
  await act(async () => {button('下一页').click();});
  expect(container.textContent).toContain('文件结果读取失败');
});
