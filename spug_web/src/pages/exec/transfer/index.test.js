import React from 'react';
import ReactDOM from 'react-dom';
import {act, Simulate} from 'react-dom/test-utils';
import {__capture as capture} from 'antd';
import {http} from 'libs';
import {useResourceAccess} from 'resources/ResourceAccess';
import {createResourceAccess} from 'resources/access';
import TransferIndex from './index';

jest.mock('resources/ResourceAccess', () => ({useResourceAccess: jest.fn()}));
jest.mock('mobx-react', () => ({observer: value => value}));
jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, t: value => value, uniqueId: () => 'file-fixture'}));
jest.mock('./store', () => ({outputs: {}, counter: {}}));
jest.mock('./Output', () => () => null);
jest.mock('pages/host/Selector', () => {
  const React = require('react');
  return props => {
    require('antd').__capture.selectors.push(props);
    return props.onlyOne ? <button onClick={() => props.onChange({id: 1, name: '来源主机'})}>选择来源</button> :
      <div><span data-targets>{props.value.join(',')}</span><button onClick={() => props.onChange([{id: 2, name: '目标二'}, {id: 3, name: '目标三'}])}>选择目标</button></div>;
  };
});
jest.mock('components/ResizableTable', () => {
  const React = require('react');
  const Table = ({children, dataSource}) => <div>{dataSource.map((row, i) => <div key={row.id}>
    {React.Children.toArray(children).map((column, j) => <span key={j}>{column.props.render ? column.props.render(row, row, i) : row[column.props.dataIndex]}</span>)}
  </div>)}</div>;
  Table.Column = () => null;
  return Table;
});
jest.mock('components', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  return {AuthDiv: Wrapper, Breadcrumb: Object.assign(Wrapper, {Item: Wrapper})};
});
jest.mock('antd', () => {
  const React = require('react');
  const capture = {selectors: []};
  const Wrapper = ({children, extra}) => <div>{extra}{children}</div>;
  return {__capture: capture, Form: Object.assign(Wrapper, {Item: Wrapper}), Space: Wrapper, Card: Wrapper, Tooltip: Wrapper, Upload: Wrapper,
    Input: ({value, onChange, placeholder}) => <input value={value} onChange={onChange} placeholder={placeholder}/>,
    Button: props => {if (props.children === '开始执行') capture.submit = props; return <button disabled={props.disabled} onClick={props.onClick}>{props.children}</button>;},
    Alert: ({message}) => <div role="alert">{message}</div>, message: {error: jest.fn()}};
});

let node;
const button = label => [...node.querySelectorAll('button')].find(item => item.textContent === label);
const render = async () => {capture.selectors = []; await act(async () => {ReactDOM.render(<TransferIndex/>, node);});};
const grant = (source = 'manage', target = 'manage', permissions = ['exec.transfer.do']) =>
  useResourceAccess.mockReturnValue(createResourceAccess({permissions, hosts: [{id: 1, level: source}, {id: 2, level: 'manage'}, {id: 3, level: target}]}));
const select = () => act(() => {
  button('选择来源').click(); button('选择目标').click();
  Simulate.change(node.querySelector('[placeholder="请输入目标路径"]'), {target: {value: '/fixture-target'}});
});
beforeEach(() => {
  node = document.createElement('div'); document.body.appendChild(node);
  http.get.mockResolvedValue([]); http.post.mockResolvedValue('fixture-token'); grant();
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(node);}); node.remove(); jest.clearAllMocks();});

test.each([['read', 'manage'], ['manage', 'read']])('source %s and target %s block the entire transfer while keeping every selected host', async (source, target) => {
  grant(source, target); await render(); select();
  expect(node.textContent).toContain('来源主机'); expect(node.querySelector('[data-targets]').textContent).toBe('2,3');
  expect(button('开始执行').disabled).toBe(true); expect(node.textContent).toContain('只读或未授权');
  await act(async () => {capture.submit.onClick();}); expect(http.post).not.toHaveBeenCalled();
});

test('all manageable source and target hosts retain the original transfer request', async () => {
  await render(); select();
  expect(capture.selectors.every(props => props.permission === 'exec.transfer.do')).toBe(true);
  expect(button('开始执行').disabled).toBe(false);
  await act(async () => {button('开始执行').click();});
  expect(http.post).toHaveBeenCalledTimes(1);
  const [url, body] = http.post.mock.calls[0];
  expect(url).toBe('/api/exec/transfer/');
  expect(JSON.parse(body.get('data'))).toEqual({dst_dir: '/fixture-target', host_ids: [2, 3], host: JSON.stringify([1, ''])});
});

test('revoking source scope after selection keeps source and targets visible and prevents even a direct submit callback', async () => {
  await render(); select(); grant('read'); await render();
  expect(node.textContent).toContain('来源主机'); expect(node.querySelector('[data-targets]').textContent).toBe('2,3');
  expect(button('开始执行').disabled).toBe(true);
  await act(async () => {capture.submit.onClick();}); expect(http.post).not.toHaveBeenCalled();
});

test('manageable resources alone do not grant file distribution', async () => {
  grant('manage', 'manage', []); await render(); select();
  expect(button('开始执行').disabled).toBe(true);
  await act(async () => {capture.submit.onClick();}); expect(http.post).not.toHaveBeenCalled();
});
