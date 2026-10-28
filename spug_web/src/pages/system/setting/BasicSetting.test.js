import React from 'react';
import ReactDOM from 'react-dom';
import {act} from 'react-dom/test-utils';
import BasicSetting from './BasicSetting';
import store from './store';
import {http} from 'libs';
jest.mock('mobx-react',()=>({observer:component=>component}));
jest.mock('libs',()=>({http:{post:jest.fn()},t:text=>text}));
jest.mock('./store',()=>({settings:{spug_key:'fixture-provider-token'},loading:false,fetchSettings:jest.fn(),update:jest.fn()}));
jest.mock('antd',()=>({Form:{Item:({children,extra,label})=><div>{label}{children}{extra}</div>},
  Popover:({children,content})=><div>{children}{content}</div>,
  Input:({value,placeholder,onChange})=><input value={value} placeholder={placeholder} onChange={onChange}/>,
  Button:({children,onClick,...props})=><button onClick={onClick} aria-expanded={props['aria-expanded']}>{children}</button>,
  message:{success:jest.fn(),error:jest.fn()}}));
let container;
beforeEach(()=>{container=document.createElement('div');document.body.appendChild(container);http.post.mockResolvedValue({});});
afterEach(()=>{act(()=>{ReactDOM.unmountComponentAtNode(container);});container.remove();jest.clearAllMocks();});
test('provider is shown on request while the existing service token and save protocol stay intact',async()=>{
  act(()=>{ReactDOM.render(<BasicSetting/>,container);});
  expect(container.textContent).not.toMatch(/spug/i);
  expect(container.querySelector('img')).toBeNull();
  expect(container.querySelector('input').value).toBe('fixture-provider-token');
  const toggle=container.querySelector('[aria-expanded]');expect(toggle.getAttribute('aria-expanded')).toBe('false');
  act(()=>toggle.click());
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
  expect(container.textContent).toContain('Spug');
  expect(http.post).not.toHaveBeenCalled();
  act(()=>toggle.click());expect(container.textContent).not.toMatch(/spug/i);
  await act(async()=>{[...container.querySelectorAll('button')].find(e=>e.textContent==='保存设置').click();for(let i=0;i<8;i++)await Promise.resolve();});
  expect(http.post).toHaveBeenCalledWith('/api/setting/',{data:[{key:'spug_key',value:'fixture-provider-token'}]});
  expect(store.fetchSettings).toHaveBeenCalledTimes(1);
});
