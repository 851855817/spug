import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import http from 'libs/http';
import Info from './Info';

jest.mock('libs/http', () => ({get: jest.fn()}));
jest.mock('libs', () => ({t: value => value}));
jest.mock('./store', () => ({record: {id: 5, h_id: 8}}));
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  return {Space: Wrapper, Spin: Wrapper, Tag: Wrapper,
    Modal: ({children, title}) => <div>{title}{children}</div>,
    Alert: ({message, action}) => <div>{message}{action}</div>,
    Button: ({onClick, children}) => <button onClick={onClick}>{children}</button>};
});
jest.mock('components/ResizableTable', () => ({columns, dataSource}) => <div>{dataSource.map(row =>
  <section key={row.key}>{columns.map((col, index) => <span key={index}>
    {col.render ? col.render(row[col.dataIndex], row) : row[col.dataIndex]}</span>)}</section>)}</div>);

let container;
beforeEach(() => {container = document.createElement('div'); document.body.appendChild(container);});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.resetAllMocks();});

test('scheduled results preserve output and failure codes and never interpret a missing code as success', async () => {
  http.get.mockResolvedValue({run_time: '2026-10-26 15:00', success: 1, failure: 1, outputs: [
    {name: 'healthy', code: 0, duration: 0, output: 'load=0 memory=128 MiB'},
    {name: 'broken', code: 12, duration: 1, output: 'permission denied'},
    {name: 'unknown', code: null, output: ''}]});
  await act(async () => {ReactDOM.render(<Info/>, container);});
  expect(http.get).toHaveBeenCalledWith('/api/schedule/5/?id=8');
  const rows = [...container.querySelectorAll('section')].map(row => row.textContent);
  expect(rows[0]).toContain('执行成功'); expect(rows[0]).toContain('0 s'); expect(rows[0]).toContain('load=0 memory=128 MiB');
  expect(rows[1]).toContain('执行失败12'); expect(rows[1]).toContain('permission denied');
  expect(rows[2]).toContain('待核实'); expect(rows[2]).toContain('未取得输出'); expect(rows[2]).not.toContain('执行成功');
});

test('read failure remains visible and permits an explicit refresh', async () => {
  http.get.mockRejectedValueOnce(new Error('read failed')).mockResolvedValueOnce({outputs: [{name: 'host', code: 0, output: 'recovered'}]});
  await act(async () => {ReactDOM.render(<Info/>, container);});
  expect(container.textContent).toContain('结果读取失败');
  await act(async () => {container.querySelector('button').click();});
  expect(container.textContent).toContain('recovered'); expect(container.textContent).not.toContain('结果读取失败');
});
