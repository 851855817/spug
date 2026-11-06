import React from 'react';
import ReactDOM from 'react-dom';
import {act,Simulate} from 'react-dom/test-utils';
import AppearanceSetting from './AppearanceSetting';
import {createAppearanceStore} from './store';
import {Modal,message} from 'antd';
jest.mock('antd',()=>({
  Button:({children,onClick,disabled,loading})=><button disabled={disabled||loading} onClick={onClick}>{children}</button>,
  Alert:({message,action})=><div role="alert">{message}{action}</div>,
  Modal:{confirm:jest.fn()},message:{success:jest.fn()},
}));
let container,request,store;
const flush=async()=>{for(let i=0;i<15;i++)await Promise.resolve();};
const response=(preset='classic-blue',revision='a'.repeat(32))=>({ok:true,json:async()=>({data:{version:1,preset,revision}})});
const click=async label=>act(async()=>{[...container.querySelectorAll('button')].find(node=>node.textContent===label).click();await flush();});
beforeEach(()=>{localStorage.clear();localStorage.setItem('token','fixture');request=jest.fn().mockResolvedValue(response());store=createAppearanceStore(request);
  container=document.createElement('div');document.body.appendChild(container);});
afterEach(()=>{act(()=>{ReactDOM.unmountComponentAtNode(container);});container.remove();jest.clearAllMocks();});
const render=async()=>act(async()=>{ReactDOM.render(<AppearanceSetting store={store}/>,container);await flush();});
test('preview changes only the draft; apply persists selected theme before showing it as current',async()=>{
  await render();const input=container.querySelector('input[value="night"]');
  act(()=>Simulate.change(input,{target:{checked:true}}));
  expect(store.getSnapshot().current.preset).toBe('classic-blue');expect(request).toHaveBeenCalledTimes(1);
  request.mockResolvedValue(response('night','b'.repeat(32)));await click('应用全站');
  expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({preset:'night',revision:'a'.repeat(32)});
  expect(container.textContent).toContain('当前生效：深色夜间');
});
test('background sync preserves unsaved choice and its revision; conflict leaves effective theme intact',async()=>{
  await render();act(()=>Simulate.change(container.querySelector('input[value="indigo"]'),{target:{checked:true}}));
  request.mockResolvedValue(response('jade','b'.repeat(32)));await act(async()=>{await store.refresh();});
  expect(container.querySelector('input[value="indigo"]').checked).toBe(true);
  request.mockResolvedValue({ok:false,status:409,json:async()=>({error:'配色已被其他管理员修改，请刷新后重试'})});
  await click('应用全站');
  expect(JSON.parse(request.mock.calls[2][1].body).revision).toBe('a'.repeat(32));
  expect(container.textContent).toContain('其他管理员');expect(store.getSnapshot().current.preset).toBe('jade');
});
test('restore requires confirmation and failed saves retain the active palette and choice',async()=>{
  request.mockResolvedValue(response('night'));await render();await click('恢复经典蓝');
  expect(Modal.confirm).toHaveBeenCalledWith(expect.objectContaining({content:'所有账号将统一使用经典蓝配色。'}));
  expect(request).toHaveBeenCalledTimes(1);
  request.mockResolvedValue({ok:false,json:async()=>({error:'暂时无法保存配色，请稍后重试'})});
  await act(async()=>{await expect(Modal.confirm.mock.calls[0][0].onOk()).rejects.toThrow('暂时无法保存');});
  expect(store.getSnapshot().current.preset).toBe('night');expect(message.success).not.toHaveBeenCalled();
  expect(container.textContent).toContain('暂时无法保存');
  request.mockResolvedValue(response('classic-blue','b'.repeat(32)));
  await act(async()=>{await Modal.confirm.mock.calls[0][0].onOk();});
  expect(store.getSnapshot().current.preset).toBe('classic-blue');
});
test('read failure disables applying until successful reload',async()=>{
  request.mockRejectedValue(new Error('offline'));await render();
  expect(container.querySelector('fieldset').disabled).toBe(true);expect(container.textContent).toContain('暂时无法同步');
  request.mockResolvedValue(response('jade'));await click('重新载入');
  expect(container.querySelector('fieldset').disabled).toBe(false);expect(container.textContent).toContain('当前生效：青碧');
});
