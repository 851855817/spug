import React from 'react';
import ReactDOM from 'react-dom';
import {act} from 'react-dom/test-utils';
import {Modal} from 'antd';
import {hasPermission, http} from 'libs';
import {useResourceAccess} from 'resources/ResourceAccess';
import TemplateTable from './Table';
import store from './store';

jest.mock('resources/ResourceAccess', () => ({useResourceAccess: jest.fn()}));
jest.mock('mobx-react', () => ({observer: value => value}));
jest.mock('libs', () => ({hasPermission: jest.fn(), http: {delete: jest.fn()}, t: value => value}));
jest.mock('./store', () => ({dataSource: [], fetchRecords: jest.fn(), showForm: jest.fn()}));
jest.mock('antd', () => ({Table: {Column: () => null}, Modal: {confirm: jest.fn()}, message: {success: jest.fn()}}));
jest.mock('components', () => {
  const React = require('react');
  const Button = ({children, onClick, disabled}) => <button disabled={disabled} onClick={onClick}>{children}</button>;
  const Action = ({children}) => <div>{children}</div>;
  Action.Button = Button;
  return {Action, AuthButton: Button, TableCard: ({children, dataSource}) => <div>{dataSource.map(row => <div key={row.id} data-template={row.id}>
    {React.Children.toArray(children).map((column, index) => <span key={index}>
      {column.props.render ? column.props.render(row) : row[column.props.dataIndex]}
    </span>)}
  </div>)}</div>};
});

let node;
const publicTemplate = {id: 1, name: '公共管理员模板', body: 'readable-public-command', created_by_id: 99};
const ownTemplate = {id: 2, name: '本人模板', body: 'readable-own-command', created_by_id: 7};
const rowButton = (id, label) => [...node.querySelector(`[data-template="${id}"]`).querySelectorAll('button')].find(button => button.textContent === label);
beforeEach(() => {
  node = document.createElement('div'); document.body.appendChild(node);
  store.dataSource = [publicTemplate, ownTemplate];
  useResourceAccess.mockReturnValue({user_id: 7, is_supper: false});
  hasPermission.mockReturnValue(true); http.delete.mockResolvedValue({});
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(node);}); node.remove(); jest.clearAllMocks();});
const render = () => act(() => {ReactDOM.render(<TemplateTable/>, node);});

test('ordinary users can read public administrator templates but cannot edit or delete them', () => {
  render();
  expect(node.textContent).toContain('readable-public-command');
  expect(rowButton(1, '编辑').disabled).toBe(true);
  expect(rowButton(1, '删除').disabled).toBe(true);
  act(() => {rowButton(1, '编辑').click(); rowButton(1, '删除').click();});
  expect(store.showForm).not.toHaveBeenCalled(); expect(Modal.confirm).not.toHaveBeenCalled();
  expect(http.delete).not.toHaveBeenCalled();
  expect(store.dataSource).toEqual([publicTemplate, ownTemplate]);
});

test('owners retain the existing edit and delete workflow for their own templates', async () => {
  render();
  expect(rowButton(2, '编辑').disabled).toBe(false);
  act(() => {rowButton(2, '编辑').click(); rowButton(2, '删除').click();});
  expect(store.showForm).toHaveBeenCalledWith(ownTemplate);
  await act(async () => {await Modal.confirm.mock.calls[0][0].onOk();});
  expect(http.delete).toHaveBeenCalledWith('/api/exec/template/', {params: {id: 2}});
});

test('effective administrators retain global write access and missing ownership fails closed for ordinary users', () => {
  useResourceAccess.mockReturnValue({user_id: 7, is_supper: true}); render();
  expect(rowButton(1, '编辑').disabled).toBe(false); expect(rowButton(1, '删除').disabled).toBe(false);
  useResourceAccess.mockReturnValue({user_id: 7, is_supper: false});
  store.dataSource = [{id: 3, name: '归属未知', body: 'still-readable'}]; render();
  expect(rowButton(3, '编辑').disabled).toBe(true); expect(rowButton(3, '删除').disabled).toBe(true);
});

test('ownership never grants feature permissions that the account does not have', () => {
  hasPermission.mockReturnValue(false); render();
  expect(node.textContent).toContain('readable-own-command');
  expect(rowButton(2, '编辑')).toBeUndefined(); expect(rowButton(2, '删除')).toBeUndefined();
});
