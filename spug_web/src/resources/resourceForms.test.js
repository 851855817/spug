import React from 'react';
import ReactDOM from 'react-dom';
import {act} from 'react-dom/test-utils';
import {__capture as capture, message} from 'antd';
import {http} from 'libs';
import {useResourceAccess} from './ResourceAccess';
import {createResourceAccess} from './access';
import HostForm from 'pages/host/Form';
import PanelForm from 'pages/baota/PanelForm';
import hostStore from 'pages/host/store';

jest.mock('./ResourceAccess', () => ({useResourceAccess: jest.fn()}));
jest.mock('mobx-react', () => ({observer: value => value}));
jest.mock('libs', () => ({http: {post: jest.fn()}, t: value => value}));
jest.mock('pages/host/store', () => ({fetchRecords: jest.fn(), fetchExtend: jest.fn()}));
jest.mock('antd', () => {
  const React = require('react');
  const capture = {};
  const Wrapper = ({children}) => <div>{children}</div>;
  const Form = props => <div>{props.children}</div>;
  Form.Item = Wrapper;
  Form.useForm = () => [capture.form];
  Form.useWatch = () => '';
  const Input = Object.assign(() => null, {Password: () => null, TextArea: () => null});
  const Modal = props => {capture.modal = props; return <div>{props.children}</div>;};
  Modal.confirm = jest.fn(props => {capture.confirm = props; return capture.confirmHandle;});
  return {__capture: capture, Form, Input, Modal,
    Button: props => {capture.buttons[props.children] = props; return <button disabled={props.disabled} onClick={props.onClick}>{props.children}</button>;},
    Alert: ({message}) => <div role="alert">{message}</div>, TreeSelect: () => null, Upload: Wrapper,
    Checkbox: Wrapper, Divider: Wrapper, Space: Wrapper, Radio: Object.assign(Wrapper, {Group: Wrapper}),
    Typography: {Paragraph: Wrapper}, message: {error: jest.fn(), info: jest.fn(), success: jest.fn()}};
});

let node;
const groups = [{id: 1, name: '原组', can_manage: true}];
const panel = {id: 9, name: '面板', group_id: 1, api_base_url: 'https://panel.fixture', has_api_key: true};
const grant = ({level = 'manage', remaining = 1, permissions = ['host.host.edit', 'host.host.add', 'baota.panel.edit', 'baota.panel.add', 'baota.panel.test'], source = 'assigned'} = {}) => {
  useResourceAccess.mockReturnValue(createResourceAccess({permissions, hosts: [{id: 9, level, source}], panels: [{id: 9, level, source}],
    quota: {used: 0, limit: remaining, remaining}}));
};
const render = (kind, editing = true) => act(() => {
  hostStore.record = editing ? {id: 9, group_ids: [1]} : {};
  ReactDOM.render(kind === 'host' ? <HostForm/> : <PanelForm record={editing ? panel : {}} groups={groups} onSaved={jest.fn()}/>, node);
});
beforeEach(() => {
  node = document.createElement('div'); document.body.appendChild(node); grant();
  hostStore.treeData = [{key: 1, value: 1, title: '原组', can_manage: true, children: []}];
  capture.fields = {group_ids: [1], group_id: 1, api_base_url: panel.api_base_url}; capture.buttons = {};
  capture.confirmHandle = {update: jest.fn(), destroy: jest.fn()};
  capture.form = {getFieldsValue: () => capture.fields, validateFields: jest.fn(() => Promise.resolve(capture.fields)),
    getFieldValue: key => capture.fields[key], setFieldsValue: jest.fn()};
  http.post.mockResolvedValue({});
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(node);}); node.remove(); jest.clearAllMocks();});

test.each(['host', 'panel'])('%s active edit form blocks a saved submit callback after manage becomes read', async kind => {
  render(kind); const submit = capture.modal.onOk;
  grant({level: 'read'}); render(kind);
  await act(async () => {await submit();});
  expect(http.post).not.toHaveBeenCalled(); expect(capture.modal.okButtonProps.disabled).toBe(true);
  expect(node.textContent).toContain('只读或未授权'); expect(message.error).toHaveBeenCalledWith(expect.stringContaining('只读或未授权'));
});

test.each(['host', 'panel'])('%s resource manage alone cannot grant absent edit permission', async kind => {
  grant({permissions: []}); render(kind);
  await act(async () => {await capture.modal.onOk();});
  expect(http.post).not.toHaveBeenCalled(); expect(capture.modal.okButtonProps.disabled).toBe(true);
  expect(node.textContent).toContain('编辑');
});

test.each(['host', 'panel'])('%s active creation form blocks the former submit callback after quota is exhausted', async kind => {
  render(kind, false); const submit = capture.modal.onOk;
  grant({remaining: 0}); render(kind, false);
  await act(async () => {await submit();});
  expect(http.post).not.toHaveBeenCalled(); expect(capture.modal.okButtonProps.disabled).toBe(true);
  expect(node.textContent).toContain('额度已用尽');
});

test.each(['host', 'panel'])('%s active creation requires its independent add permission', async kind => {
  grant({permissions: ['host.host.edit', 'baota.panel.edit']}); render(kind, false);
  await act(async () => {await capture.modal.onOk();});
  expect(http.post).not.toHaveBeenCalled(); expect(capture.modal.okButtonProps.disabled).toBe(true);
  expect(node.textContent).toContain('新增');
});

test.each(['edit', 'create'])('panel %s rechecks the latest access after asynchronous validation resolves', async mode => {
  let resolve;
  capture.form.validateFields.mockReturnValue(new Promise(done => {resolve = done;}));
  render('panel', mode === 'edit'); let pending;
  act(() => {pending = capture.modal.onOk();});
  grant(mode === 'edit' ? {level: 'read'} : {remaining: 0}); render('panel', mode === 'edit');
  await act(async () => {resolve(capture.fields); await pending;});
  expect(http.post).not.toHaveBeenCalled();
});

test.each(['edit', 'create'])('host static password confirmation rechecks current %s access instead of its original closure', async mode => {
  http.post.mockResolvedValueOnce('auth fail'); render('host', mode === 'edit');
  await act(async () => {await capture.modal.onOk();});
  expect(capture.confirm).toBeDefined(); capture.confirm.content.props.onChange('fixture-password');
  const confirm = capture.confirm.onOk; http.post.mockClear();
  grant(mode === 'edit' ? {level: 'read'} : {remaining: 0}); render('host', mode === 'edit');
  await act(async () => {await confirm();});
  expect(http.post).not.toHaveBeenCalled();
  expect(capture.confirmHandle.update).toHaveBeenCalledWith(expect.objectContaining({okButtonProps: {disabled: true}}));
});

test('the former host password confirmation cannot submit after the form has unmounted', async () => {
  http.post.mockResolvedValueOnce('auth fail'); render('host');
  await act(async () => {await capture.modal.onOk();});
  capture.confirm.content.props.onChange('fixture-password'); const confirm = capture.confirm.onOk;
  http.post.mockClear(); act(() => {ReactDOM.unmountComponentAtNode(node);});
  await act(async () => {await confirm();}); expect(http.post).not.toHaveBeenCalled();
});

test('pending panel validation cannot submit after the form has unmounted', async () => {
  let resolve;
  capture.form.validateFields.mockReturnValue(new Promise(done => {resolve = done;}));
  render('panel'); let pending;
  act(() => {pending = capture.modal.onOk();});
  act(() => {ReactDOM.unmountComponentAtNode(node);});
  await act(async () => {resolve(capture.fields); await pending;});
  expect(http.post).not.toHaveBeenCalled();
});

test.each(['host', 'panel'])('%s assigned managers retain ordinary edit saves with their unchanged original groups', async kind => {
  grant({remaining: 0}); render(kind);
  await act(async () => {await capture.modal.onOk();});
  expect(capture.modal.okButtonProps.disabled).toBe(false);
  expect(http.post).toHaveBeenCalledWith(kind === 'host' ? '/api/host/' : '/api/baota/', expect.objectContaining({id: 9}));
});

test.each(['host', 'panel'])('%s creation retains its successful save with add permission and available quota', async kind => {
  render(kind, false); await act(async () => {await capture.modal.onOk();});
  expect(capture.modal.okButtonProps.disabled).toBe(false);
  expect(http.post).toHaveBeenCalledWith(kind === 'host' ? '/api/host/' : '/api/baota/', expect.any(Object));
});

test('panel tests require test permission independently of edit and recheck saved callbacks after resource revocation', async () => {
  grant({permissions: ['baota.panel.test']}); render('panel');
  const check = capture.buttons['测试已保存配置'].onClick, certificate = capture.buttons['查看证书'].onClick;
  expect(capture.buttons['测试已保存配置'].disabled).toBe(false);
  expect(capture.buttons['查看证书'].disabled).toBe(false);
  await act(async () => {await check(); await certificate();});
  expect(http.post).toHaveBeenCalledWith('/api/baota/connection/', {id: 9});
  expect(http.post).toHaveBeenCalledWith('/api/baota/certificate/', {id: 9});
  http.post.mockClear(); grant({level: 'read', permissions: ['baota.panel.test']}); render('panel');
  await act(async () => {await check(); await certificate();}); expect(http.post).not.toHaveBeenCalled();
});

test('panel edit permission cannot borrow the connection or certificate test handlers', async () => {
  grant({permissions: ['baota.panel.edit']}); render('panel');
  expect(capture.buttons['测试已保存配置'].disabled).toBe(true); expect(capture.buttons['查看证书'].disabled).toBe(true);
  await act(async () => {await capture.buttons['测试已保存配置'].onClick(); await capture.buttons['查看证书'].onClick();});
  expect(http.post).not.toHaveBeenCalled();
});
