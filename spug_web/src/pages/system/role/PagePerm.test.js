import React from 'react';
import ReactDOM from 'react-dom';
import {act,Simulate} from 'react-dom/test-utils';
import PagePerm from './PagePerm';
import store from './store';
import http from 'libs/http';
import {buildPresetPermissions} from './presets';
jest.mock('libs',()=>({t:value=>value}));
jest.mock('libs/http',()=>({patch:jest.fn()}));
jest.mock('mobx-react',()=>({observer:value=>value}));
jest.mock('./store',()=>({record:{id:7},permissions:{},pagePermVisible:true,fetchRecords:jest.fn()}));
jest.mock('antd',()=>{
  const Wrapper=({children})=><div>{children}</div>;
  return {Modal:({children,onOk,onCancel})=><div>{children}<button onClick={onOk}>保存权限</button><button onClick={onCancel}>取消</button></div>,
    Select:({value,onChange,options,...props})=><select aria-label={props['aria-label']} value={value||''} onChange={e=>onChange(e.target.value)}><option value=""/>{options.map(row=><option key={row.value} value={row.value}>{row.label}</option>)}</select>,
    Checkbox:({checked,onChange,children})=><label><input type="checkbox" checked={checked||false} onChange={onChange}/>{children}</label>,
    Button:({children,onClick,disabled})=><button disabled={disabled} onClick={onClick}>{children}</button>,
    Row:Wrapper,Col:Wrapper,Space:Wrapper,Alert:({message,description})=><div>{message}{description}</div>,message:{success:jest.fn()}};
});
let container;
const flush=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
beforeEach(()=>{store.record={id:7};store.permissions=buildPresetPermissions('lead');store.pagePermVisible=true;
  http.patch.mockResolvedValue({});container=document.createElement('div');document.body.appendChild(container);});
afterEach(()=>{act(()=>{ReactDOM.unmountComponentAtNode(container);});container.remove();jest.clearAllMocks();});
test('selecting a template previews its limits, applying replaces only the draft, save uses the existing role ID',async()=>{
  act(()=>{ReactDOM.render(<PagePerm/>,container);});
  act(()=>Simulate.change(container.querySelector('[aria-label="岗位权限模板"]'),{target:{value:'website'}}));
  expect(container.textContent).toContain('安装');
  expect(store.permissions.baota.panel).toContain('login');
  expect(http.patch).not.toHaveBeenCalled();
  act(()=>[...container.querySelectorAll('button')].find(b=>b.textContent==='套用到当前草稿').click());
  expect(store.permissions).toEqual(buildPresetPermissions('website'));
  expect(http.patch).not.toHaveBeenCalled();
  await act(async()=>{[...container.querySelectorAll('button')].find(b=>b.textContent==='保存权限').click();await flush();});
  expect(http.patch).toHaveBeenCalledWith('/api/account/role/',{id:7,page_perms:buildPresetPermissions('website')});
  expect(store.fetchRecords).toHaveBeenCalledTimes(1);
});

test('cancelling a template draft performs no permission write',()=>{
  act(()=>{ReactDOM.render(<PagePerm/>,container);});
  act(()=>Simulate.change(container.querySelector('[aria-label="岗位权限模板"]'),{target:{value:'auditor'}}));
  act(()=>[...container.querySelectorAll('button')].find(b=>b.textContent==='套用到当前草稿').click());
  act(()=>[...container.querySelectorAll('button')].find(b=>b.textContent==='取消').click());
  expect(http.patch).not.toHaveBeenCalled();expect(store.pagePermVisible).toBe(false);
});

test('a failed save leaves the permission draft open for review and retry',async()=>{
  http.patch.mockRejectedValue(new Error('fixture failure'));
  act(()=>{ReactDOM.render(<PagePerm/>,container);});
  const permissions=store.permissions;
  await act(async()=>{[...container.querySelectorAll('button')].find(b=>b.textContent==='保存权限').click();await flush();});
  expect(store.pagePermVisible).toBe(true);expect(store.permissions).toBe(permissions);
  expect(store.fetchRecords).not.toHaveBeenCalled();
});
