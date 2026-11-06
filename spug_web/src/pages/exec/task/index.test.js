import React from 'react';
import ReactDOM from 'react-dom';
import {act} from 'react-dom/test-utils';
import {__capture as capture} from 'antd';
import {http} from 'libs';
import {useResourceAccess} from 'resources/ResourceAccess';
import {createResourceAccess} from 'resources/access';
import TaskIndex from './index';
import store from './store';

jest.mock('resources/ResourceAccess', () => ({useResourceAccess: jest.fn()}));
jest.mock('mobx-react', () => ({observer: value => value}));
jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, t: value => value, cleanCommand: value => value, docsUrl: () => '#'}));
jest.mock('gStore', () => ({fetchUserSettings: jest.fn()}));
jest.mock('./store', () => ({host_ids: [], switchConsole: jest.fn(), switchTemplate: jest.fn()}));
jest.mock('./Output', () => () => null);
jest.mock('./Parameter', () => {
  const React = require('react');
  return ({onOk}) => <button onClick={() => onOk({name: 'value'})}>提交参数</button>;
});
jest.mock('./TemplateSelector', () => {
  const React = require('react');
  return ({onOk}) => <button onClick={() => onOk({id: 10, host_ids: [1, 2], interpreter: 'sh', body: 'echo fixture', parameters: []})}>选择公共模板</button>;
});
jest.mock('pages/host/Selector', () => {
  const React = require('react');
  return ({value}) => <div data-hosts>{value.join(',')}</div>;
});
jest.mock('components', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  return {AuthDiv: Wrapper, Breadcrumb: Object.assign(Wrapper, {Item: Wrapper}), ACEditor: () => null};
});
jest.mock('antd', () => {
  const React = require('react');
  const capture = {};
  const Wrapper = ({children}) => <div>{children}</div>;
  return {__capture: capture, Form: Object.assign(Wrapper, {Item: Wrapper}),
    Button: props => {if (props.children === '开始执行') capture.submit = props; return <button disabled={props.disabled} onClick={props.onClick}>{props.children}</button>;},
    Alert: ({message}) => <div role="alert">{message}</div>, Tooltip: Wrapper,
    Radio: {Group: Wrapper, Button: Wrapper}};
});

let node;
const button = label => [...node.querySelectorAll('button')].find(item => item.textContent === label);
const render = async () => {await act(async () => {ReactDOM.render(<TaskIndex/>, node);});};
const grant = (level = 'manage', permissions = ['exec.task.do']) => useResourceAccess.mockReturnValue(createResourceAccess({
  permissions, hosts: [{id: 1, level: 'manage'}, {id: 2, level}]}));
beforeEach(() => {
  node = document.createElement('div'); document.body.appendChild(node);
  store.host_ids = []; store.showConsole = false; store.showTemplate = true;
  http.get.mockResolvedValue([]); http.post.mockResolvedValue('token'); grant();
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(node);}); node.remove(); jest.clearAllMocks();});

test('a public template keeps all target hosts and blocks the entire execution when any target is readonly', async () => {
  grant('read'); await render(); act(() => {button('选择公共模板').click();});
  expect(store.host_ids).toEqual([1, 2]); expect(node.querySelector('[data-hosts]').textContent).toBe('1,2');
  expect(button('开始执行').disabled).toBe(true); expect(node.textContent).toContain('只读或未授权');
  await act(async () => {capture.submit.onClick();}); expect(http.post).not.toHaveBeenCalled();
});

test('all manageable targets retain the existing execution payload', async () => {
  await render(); act(() => {button('选择公共模板').click();});
  expect(button('开始执行').disabled).toBe(false);
  await act(async () => {button('开始执行').click();});
  expect(http.post).toHaveBeenCalledWith('/api/exec/do/', expect.objectContaining({host_ids: [1, 2], template_id: 10, command: 'echo fixture'}));
});

test('resource management does not grant an absent execution feature', async () => {
  grant('manage', []); await render(); act(() => {button('选择公共模板').click();});
  expect(button('开始执行').disabled).toBe(true);
  await act(async () => {capture.submit.onClick();}); expect(http.post).not.toHaveBeenCalled();
});

test('revoking a selected target retains it and blocks submission after the effective policy refresh', async () => {
  await render(); act(() => {button('选择公共模板').click();});
  grant('read'); await render();
  expect(store.host_ids).toEqual([1, 2]); expect(button('开始执行').disabled).toBe(true);
  await act(async () => {capture.submit.onClick();}); expect(http.post).not.toHaveBeenCalled();
});
