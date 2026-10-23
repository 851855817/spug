import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { http, hasPermission } from 'libs';
import store from './store';
import ComTable from './Table';
import { message } from 'antd';

jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, hasPermission: jest.fn(), t: value => value}));
jest.mock('mobx-react', () => ({observer: component => component}));
jest.mock('./store', () => ({rawRecords: [{id: 1, name: '主机'}], dataSource: [{id: 1, name: '主机'}], isFetching: false, fetchRecords: jest.fn()}));
jest.mock('./IPAddress', () => () => null);
jest.mock('./icons', () => ({}));
jest.mock('components', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  return {Action: Object.assign(Wrapper, {Button: Wrapper}), AuthButton: Wrapper, AuthFragment: Wrapper,
    TableCard: ({children, dataSource, onReload}) => <div><button onClick={onReload}>刷新主机</button>
      {dataSource.map(info => <div key={info.id}>{React.Children.toArray(children).map((column, index) =>
        <div key={index}>{column.props.render ? column.props.render(column.props.dataIndex ? info[column.props.dataIndex] : info, info) : null}</div>)}</div>)}</div>};
});
jest.mock('antd', () => {
  const Wrapper = ({children}) => <div>{children}</div>;
  return {Table: {Column: () => null}, Switch: ({checked, disabled, loading, onChange}) => <button role="switch" aria-checked={checked}
    disabled={disabled || loading} onClick={() => onChange(!checked)}/>, Modal: {confirm: jest.fn()}, Dropdown: Wrapper,
    Button: ({children, onClick, ...props}) => <button aria-label={props['aria-label']} onClick={onClick}>{children}</button>,
    Avatar: Wrapper, Tooltip: Wrapper, Space: Wrapper, Tag: Wrapper,
    Radio: {Group: Wrapper, Button: Wrapper}, Input: () => null, message: {success: jest.fn(), info: jest.fn()}};
});

test('host monitor icon reports disabled monitoring without collecting', async () => {
  await act(async () => {ReactDOM.render(<ComTable/>, container);});
  await act(async () => {container.querySelector('[aria-label="查看主机监控"]').click();});
  expect(message.info).toHaveBeenCalledWith('未开启监控');
  expect(http.post).not.toHaveBeenCalled();
});
let container;
beforeEach(() => {
  container = document.createElement('div'); document.body.appendChild(container); hasPermission.mockReturnValue(true);
  http.get.mockResolvedValue({sources: [{id: 1, kind: 'host', enabled: false}], hosts: []});
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.resetAllMocks();});
test('host list shares monitor state and refresh reads current persisted preferences', async () => {
  await act(async () => {ReactDOM.render(<ComTable/>, container);});
  expect(http.get).toHaveBeenCalledTimes(1);
  const toggle = container.querySelector('[role=switch]'); expect(toggle).not.toBeNull();
  expect(toggle.getAttribute('aria-checked')).toBe('false');
  http.post.mockResolvedValue({id: 1, kind: 'host', enabled: true});
  await act(async () => {toggle.click();});
  expect(http.post).toHaveBeenCalledWith('/api/baota/monitor/settings/', {kind: 'host', id: 1, enabled: true});
  expect(toggle.getAttribute('aria-checked')).toBe('true');
  http.get.mockResolvedValue({sources: [{id: 1, kind: 'host', enabled: false}], hosts: []});
  store.fetchRecords.mockImplementation(() => {store.rawRecords = [...store.rawRecords]; ReactDOM.render(<ComTable/>, container);});
  await act(async () => {container.querySelector('button').click();});
  expect(store.fetchRecords).toHaveBeenCalledTimes(1);
  expect(http.get).toHaveBeenCalledTimes(2); expect(toggle.getAttribute('aria-checked')).toBe('false');
});
