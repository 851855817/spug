import React from 'react';
import ReactDOM from 'react-dom';
import {act} from 'react-dom/test-utils';
import {__capture as capture} from 'antd';
import {http} from 'libs';
import {useResourceAccess} from './ResourceAccess';
import {createResourceAccess} from './access';
import HostForm from 'pages/host/Form';
import PanelForm from 'pages/baota/PanelForm';
import HostSelector from 'pages/host/Selector';
import Import from 'pages/host/Import';
import CloudImport from 'pages/host/CloudImport';
import hostStore from 'pages/host/store';
import selectorStore from 'pages/host/store2';

jest.mock('./ResourceAccess', () => ({useResourceAccess: jest.fn()}));
jest.mock('mobx-react', () => ({observer: value => value}));
jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, t: value => value}));
jest.mock('libs/http', () => require('libs').http);
jest.mock('pages/host/store', () => ({initial: jest.fn(), fetchRecords: jest.fn(), fetchExtend: jest.fn()}));
jest.mock('pages/host/store2', () => ({}));
jest.mock('pages/host/Sync', () => () => null);
jest.mock('pages/host/IPAddress', () => () => null);
jest.mock('components/ResizableTable', () => {
  const Table = props => {require('antd').__capture.table = props; return null;};
  Table.Column = () => null;
  return Table;
});
jest.mock('antd', () => {
  const React = require('react');
  const capture = {};
  const Wrapper = ({children}) => <div>{children}</div>;
  const Form = props => {capture.initialValues = props.initialValues; return <div>{props.children}</div>;};
  Form.Item = Wrapper;
  Form.useForm = () => [{getFieldsValue: () => capture.fields, validateFields: () => Promise.resolve(capture.fields)}];
  Form.useWatch = () => '';
  const Picker = props => {capture.picker = props; return null;};
  const Input = Object.assign(({value, onChange, placeholder}) => <input value={value || ''} onChange={onChange} readOnly={!onChange} placeholder={placeholder}/>, {Password: Wrapper, TextArea: Wrapper});
  return {__capture: capture, Form, Input, TreeSelect: Picker, Cascader: Picker,
    Modal: props => {capture.modal = props; return <div>{props.children}</div>;},
    Button: props => {capture.buttons.push(props); return <button disabled={props.disabled} onClick={props.onClick}>{props.children}</button>;},
    Tree: {DirectoryTree: Picker}, Upload: props => {capture.upload = props; return <div>{props.children}</div>;},
    Select: Object.assign(Wrapper, {Option: Wrapper}), Radio: Object.assign(Wrapper, {Group: Wrapper}),
    Row: Wrapper, Col: Wrapper, Space: Wrapper, Alert: Wrapper, Tooltip: Wrapper, Divider: Wrapper, Checkbox: Wrapper,
    Steps: () => null, Typography: {Paragraph: Wrapper}, message: {success: jest.fn(), error: jest.fn()}};
});

let node;
const tree = [{key: 1, value: 1, title: '只读祖先', can_manage: false, children: [
  {key: 2, value: 2, title: '本人分组', can_manage: true, children: []},
  {key: 3, value: 3, title: '只读分组', can_manage: false, children: []},
  {key: 4, value: 4, title: '本人另一分组', can_manage: true, children: []},
]}];
const groups = [{id: 1, name: '只读祖先', can_manage: false},
  {id: 2, name: '本人分组', parent_id: 1, can_manage: true}, {id: 3, name: '只读分组', parent_id: 1, can_manage: false},
  {id: 4, name: '本人另一分组', parent_id: 1, can_manage: true}];
const render = element => act(() => {capture.buttons = []; ReactDOM.render(React.cloneElement(element), node);});
const nodes = () => capture.picker.treeData || capture.picker.options;
const grant = (is_supper, source = 'owned', additional = []) => useResourceAccess.mockReturnValue(createResourceAccess({is_supper,
  permissions: ['host.host.add', 'host.host.edit', 'baota.panel.add', 'baota.panel.edit'], quota: {limit: null, remaining: null},
  hosts: [{id: 9, level: 'manage', source}, ...additional], panels: [{id: 9, level: 'manage', source}]}));
beforeEach(() => {
  node = document.createElement('div'); document.body.appendChild(node);
  Object.assign(hostStore, {record: {}, treeData: tree, rawTreeData: tree, rawRecords: [], group: tree[0].children[0], initial: jest.fn().mockResolvedValue()});
  Object.assign(selectorStore, {treeData: tree, group: tree[0], rawTreeData: tree, counter: {}, dataSource: []});
  capture.fields = {}; capture.buttons = []; grant(false);
  http.post.mockResolvedValue({}); http.get.mockResolvedValue([]);
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(node);}); node.remove(); jest.clearAllMocks();});

test.each(['host', 'panel', 'excel', 'cloud'])('%s picker disables readonly ancestors and groups while keeping writable descendants accessible', kind => {
  const elements = {host: <HostForm/>, panel: <PanelForm record={{}} groups={groups}/>, excel: <Import/>, cloud: <CloudImport/>};
  render(elements[kind]);
  expect(nodes()[0].disabled).toBe(true);
  expect(nodes()[0].children[0].disabled).toBe(false);
  expect(nodes()[0].children[1].disabled).toBe(true);
  expect(tree[0].disabled).toBeUndefined();
  grant(true); render(elements[kind]);
  expect(nodes()[0].disabled).toBe(false); expect(nodes()[0].children[1].disabled).toBe(false);
});

test('host edits keep the original readonly group but reject changing into another readonly group', async () => {
  hostStore.record = {id: 9, group_ids: [1]}; capture.fields = {name: '主机', group_ids: [1, 2]};
  render(<HostForm/>);
  expect(capture.initialValues.group_ids).toEqual([1]);
  await act(async () => {capture.modal.onOk();});
  expect(http.post).toHaveBeenCalledWith('/api/host/', expect.objectContaining({group_ids: [1, 2]}));
  http.post.mockClear(); capture.fields = {group_ids: [1, 3]};
  await act(async () => {capture.modal.onOk();}); expect(http.post).not.toHaveBeenCalled();
});

test('panel edits preserve original or hidden groups and reject a readonly destination', async () => {
  capture.fields = {group_id: 1};
  render(<PanelForm record={{id: 9, group_id: 1}} groups={groups} onSaved={jest.fn()}/>);
  await act(async () => {await capture.modal.onOk();});
  expect(http.post).toHaveBeenCalledWith('/api/baota/', expect.objectContaining({group_id: 1}));
  http.post.mockClear(); capture.fields = {group_id: 3};
  await act(async () => {await capture.modal.onOk();}); expect(http.post).not.toHaveBeenCalled();
  capture.fields = {}; render(<PanelForm record={{id: 9, group_id: null}} groups={groups} onSaved={jest.fn()}/>);
  await act(async () => {await capture.modal.onOk();});
  expect(http.post).toHaveBeenCalledWith('/api/baota/', expect.objectContaining({id: 9}));
});

test('group moving or copying disables readonly source groups and rechecks both source and destination before submission', async () => {
  const onChange = jest.fn().mockResolvedValue();
  await act(async () => {render(<HostSelector mode="group" value={[9]} onChange={onChange}/>);});
  expect(nodes()[0].disabled).toBe(true); expect(nodes()[0].children[0].disabled).toBe(false);
  await act(async () => {capture.modal.onOk();}); expect(onChange).not.toHaveBeenCalled();
  act(() => {capture.picker.onSelect([2], {node: tree[0].children[0]});});
  await act(async () => {capture.modal.onOk();});
  expect(onChange).toHaveBeenCalledWith(tree[0].children[0], [9]);
  onChange.mockClear(); hostStore.group = tree[0].children[1];
  render(<HostSelector mode="group" value={[9]} onChange={onChange}/>);
  await act(async () => {capture.modal.onOk();}); expect(onChange).not.toHaveBeenCalled();
});

test.each(['excel', 'cloud'])('%s import rejects a readonly group even if a callback provides its id', async kind => {
  render(kind === 'excel' ? <Import/> : <CloudImport/>);
  act(() => {capture.picker.onChange(3);});
  if (kind === 'excel') {
    act(() => {capture.upload.onChange({fileList: [{}], file: new File(['x'], 'hosts.xlsx')});});
    await act(async () => {capture.buttons.findLast(item => item.children === '导入主机').onClick();});
  } else {
    // Region lookup only selects the cloud account; submitting still rechecks group ownership.
    await act(async () => {capture.buttons.findLast(item => item.children === '下一步').onClick();});
    await act(async () => {capture.buttons.findLast(item => item.children === '同步导入').onClick();});
  }
  expect(http.post).not.toHaveBeenCalled();
});

test.each(['host', 'panel'])('%s assigned managers retain the visible original group but cannot move the resource to their own group', async kind => {
  grant(false, 'assigned'); hostStore.record = {id: 9, group_ids: [1]};
  capture.fields = kind === 'host' ? {group_ids: [1]} : {group_id: 1};
  render(kind === 'host' ? <HostForm/> : <PanelForm record={{id: 9, group_id: 1}} groups={groups} onSaved={jest.fn()}/>);
  await act(async () => {await capture.modal.onOk();});
  expect(http.post).toHaveBeenCalledWith(kind === 'host' ? '/api/host/' : '/api/baota/',
    expect.objectContaining(kind === 'host' ? {group_ids: [1]} : {group_id: 1}));
  http.post.mockClear(); capture.fields = kind === 'host' ? {group_ids: [2]} : {group_id: 2};
  await act(async () => {await capture.modal.onOk();}); expect(http.post).not.toHaveBeenCalled();
  expect(capture.picker.disabled).toBe(true);
});

test.each([['host', false, 'owned'], ['host', true, 'assigned'], ['panel', false, 'owned'], ['panel', true, 'assigned']])(
  '%s group changes remain available to owned resources or effective administrators (admin=%s source=%s)', async (kind, admin, source) => {
    grant(admin, source); hostStore.record = {id: 9, group_ids: [1]};
    capture.fields = kind === 'host' ? {group_ids: [2]} : {group_id: 2};
    render(kind === 'host' ? <HostForm/> : <PanelForm record={{id: 9, group_id: 1}} groups={groups} onSaved={jest.fn()}/>);
    expect(capture.picker.disabled).not.toBe(true);
    await act(async () => {await capture.modal.onOk();});
    expect(http.post).toHaveBeenCalledWith(kind === 'host' ? '/api/host/' : '/api/baota/',
      expect.objectContaining(kind === 'host' ? {group_ids: [2]} : {group_id: 2}));
  });

test.each([{ids: [9]}, {ids: [9, 10]}])('single or mixed-batch group changes keep selected assigned hosts visible and block the whole request: $ids', async ({ids}) => {
  grant(false, ids.length === 1 ? 'assigned' : 'owned', [{id: 10, level: 'manage', source: 'assigned'}]);
  hostStore.group = tree[0].children[2]; const onChange = jest.fn().mockResolvedValue();
  await act(async () => {render(<HostSelector mode="group" value={ids} onChange={onChange}/>);});
  selectorStore.group = tree[0].children[0];
  render(<HostSelector mode="group" value={[...ids]} onChange={onChange}/>);
  expect(capture.table.rowSelection.selectedRowKeys).toEqual(ids);
  await act(async () => {await capture.modal.onOk();}); expect(onChange).not.toHaveBeenCalled();
  expect(capture.table.rowSelection.getCheckboxProps({id: ids[ids.length - 1]}).disabled).toBe(true);
  expect(capture.modal.okButtonProps.disabled).toBe(true);
});

test.each([[false, 'owned'], [true, 'assigned']])('owned hosts or effective admins retain group moving and copying (admin=%s source=%s)', async (admin, source) => {
  grant(admin, source); hostStore.group = tree[0].children[2]; const onChange = jest.fn().mockResolvedValue();
  await act(async () => {render(<HostSelector mode="group" value={[9]} onChange={onChange}/>);});
  selectorStore.group = tree[0].children[0];
  render(<HostSelector mode="group" value={[9]} onChange={onChange}/>);
  expect(capture.modal.okButtonProps.disabled).toBe(false);
  await act(async () => {await capture.modal.onOk();});
  expect(onChange).toHaveBeenCalledWith(tree[0].children[0], [9]);
});

test.each(['host', 'panel'])('%s creation can assign an owned group before a resource ownership row exists', async kind => {
  grant(false, 'assigned'); capture.fields = kind === 'host' ? {group_ids: [2]} : {group_id: 2};
  render(kind === 'host' ? <HostForm/> : <PanelForm record={{}} groups={groups} onSaved={jest.fn()}/>);
  expect(capture.picker.disabled).not.toBe(true);
  await act(async () => {await capture.modal.onOk();});
  expect(http.post).toHaveBeenCalledWith(kind === 'host' ? '/api/host/' : '/api/baota/',
    expect.objectContaining(kind === 'host' ? {group_ids: [2]} : {group_id: 2}));
});

test('an assigned host cannot remove a visible original group by submitting only the retained subset', async () => {
  grant(false, 'assigned'); hostStore.record = {id: 9, group_ids: [1, 2]}; capture.fields = {group_ids: [1]};
  render(<HostForm/>); await act(async () => {await capture.modal.onOk();});
  expect(http.post).not.toHaveBeenCalled();
});

test.each(['host', 'panel'])('%s assigned resources with hidden groups can save ordinary fields without sending private group ids', async kind => {
  grant(false, 'assigned'); hostStore.record = {id: 9, group_ids: []}; capture.fields = {name: '只更新名称'};
  render(kind === 'host' ? <HostForm/> : <PanelForm record={{id: 9, group_id: null}} groups={groups} onSaved={jest.fn()}/>);
  await act(async () => {await capture.modal.onOk();});
  expect(http.post).toHaveBeenCalledTimes(1);
  const payload = http.post.mock.calls[0][1];
  expect(payload.name).toBe('只更新名称'); expect(payload).not.toHaveProperty(kind === 'host' ? 'group_ids' : 'group_id');
});

test.each(['host', 'panel'])('%s resources with missing ownership source fail closed when changing groups', async kind => {
  grant(false, null); hostStore.record = {id: 9, group_ids: [1]};
  capture.fields = kind === 'host' ? {group_ids: [2]} : {group_id: 2};
  render(kind === 'host' ? <HostForm/> : <PanelForm record={{id: 9, group_id: 1}} groups={groups} onSaved={jest.fn()}/>);
  await act(async () => {await capture.modal.onOk();}); expect(http.post).not.toHaveBeenCalled();
});
