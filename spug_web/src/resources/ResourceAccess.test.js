import React from 'react';
import ReactDOM from 'react-dom';
import {act} from 'react-dom/test-utils';
import {ResourceAccessProvider, useResourceAccess} from './ResourceAccess';
import http from 'libs/http';
jest.mock('libs/http', () => ({get: jest.fn()}));
let node;
beforeEach(() => {node = document.createElement('div'); document.body.appendChild(node); localStorage.setItem('id', '7');});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(node);}); node.remove(); localStorage.clear(); jest.clearAllMocks();});
function Probe() {const access = useResourceAccess(); return <span>{access.canManage('panel', 1, 'baota.panel.edit') ? '可管理' : '只读'}</span>;}
test('loads effective permissions before mounting pages and does not retain stale local feature grants', async () => {
  localStorage.setItem('permissions', '["baota.panel.login"]');
  let resolve;
  http.get.mockReturnValue(new Promise(done => {resolve = done;}));
  act(() => {ReactDOM.render(<ResourceAccessProvider><Probe/></ResourceAccessProvider>, node);});
  expect(node.textContent).not.toContain('可管理');
  await act(async () => {resolve({user_id: 7, permissions: ['baota.panel.edit'], panels: [{id: 1, level: 'manage'}]}); await Promise.resolve();});
  expect(node.textContent).toContain('可管理');
  expect(JSON.parse(localStorage.getItem('permissions'))).toEqual(['baota.panel.edit']);
  expect(http.get).toHaveBeenCalledWith('/api/account/resources/');
});
