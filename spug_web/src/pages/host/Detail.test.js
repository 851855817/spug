import React from 'react';
import ReactDOM from 'react-dom';
import {act} from 'react-dom/test-utils';
import {http, hasPermission} from 'libs';
import {useResourceAccess} from 'resources/ResourceAccess';
import {createResourceAccess} from 'resources/access';
import Detail from './Detail';
import store from './store';
jest.mock('resources/ResourceAccess', () => ({useResourceAccess: jest.fn()}));
jest.mock('mobx-react', () => ({observer: value => value}));
jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, hasPermission: jest.fn(), t: value => value}));
jest.mock('./store', () => ({record: {}, detailVisible: true, groups: {}, fetchRecords: jest.fn()}));
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children, extra}) => <div>{extra}{children}</div>;
  const Button = ({children, onClick, disabled, loading}) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>;
  const Input = ({value, onChange}) => <input value={value || ''} onChange={onChange}/>;
  const Select = Object.assign(Wrapper, {Option: Wrapper});
  return {Button, Drawer: Wrapper, Descriptions: Object.assign(Wrapper, {Item: Wrapper}), List: Object.assign(Wrapper, {Item: Wrapper}),
    Input, Select, DatePicker: Input, Tag: Wrapper, message: {success: jest.fn()}};
});
jest.mock('components', () => ({AuthButton: ({auth, ...props}) => require('libs').hasPermission(auth) ? require('react').createElement(require('antd').Button, props) : null}));
let node;
const button = label => [...node.querySelectorAll('button')].find(item => item.textContent === label);
const render = () => act(() => {ReactDOM.render(<Detail/>, node);});
const grant = (permissions, level = 'manage') => {
  const access = createResourceAccess({user_id: 7, permissions, hosts: [{id: 1, level}]});
  useResourceAccess.mockReturnValue(access); hasPermission.mockImplementation(code => access.has(code));
};
beforeEach(() => {
  node = document.createElement('div'); document.body.appendChild(node);
  store.record = {id: 1, name: '主机', group_ids: [], disk: [], private_ip_address: [], public_ip_address: []};
  store.detailVisible = true; http.get.mockResolvedValue({cpu: 8}); http.post.mockResolvedValue({cpu: 4});
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(node);}); node.remove(); jest.clearAllMocks();});

test('verify with manage scope can synchronize independently without edit permission', async () => {
  grant(['host.host.verify']); render();
  expect(button('同步')).toBeDefined(); expect(button('同步').disabled).toBe(false);
  expect(button('编辑')).toBeUndefined();
  await act(async () => {button('同步').click();});
  expect(http.get).toHaveBeenCalledWith('/api/host/extend/', {params: {host_id: 1}});
  expect(http.post).not.toHaveBeenCalled();
});

test('edit with manage scope can save but does not grant remote synchronization', async () => {
  grant(['host.host.edit']); render();
  act(() => {button('编辑').click();});
  expect(button('同步')).toBeUndefined(); expect(button('保存').disabled).toBe(false);
  await act(async () => {button('保存').click();});
  expect(http.post).toHaveBeenCalledWith('/api/host/extend/', expect.objectContaining({host_id: 1}));
  expect(http.get).not.toHaveBeenCalled();
});

test('read grants block both synchronization and edits despite feature grants', () => {
  grant(['host.host.edit', 'host.host.verify'], 'read'); render();
  expect(button('同步').disabled).toBe(true); expect(button('编辑').disabled).toBe(true);
  act(() => {button('同步').click(); button('编辑').click();});
  expect(http.get).not.toHaveBeenCalled(); expect(http.post).not.toHaveBeenCalled();
});

test('revoking edit scope during editing disables save and does not post', () => {
  grant(['host.host.edit', 'host.host.verify']); render(); act(() => {button('编辑').click();});
  grant(['host.host.edit', 'host.host.verify'], 'read'); render();
  expect(button('保存').disabled).toBe(true); act(() => {button('保存').click();});
  expect(http.post).not.toHaveBeenCalled();
});
