import React from 'react';
import ReactDOM from 'react-dom';
import {act} from 'react-dom/test-utils';
import About from './About';
import {http} from 'libs';

jest.mock('libs',()=>({http:{get:jest.fn()},VERSION:'4.0.0',t:text=>text,docsUrl:path=>'https://ops.spug.cc/docs/'+path}));
jest.mock('../../../branding/source',()=>({RELEASE_REVISION:'0123456789abcdef0123456789abcdef01234567'}));
jest.mock('../../../branding/Brand',()=>()=> <b>KOps · 云枢</b>);
jest.mock('../../../branding/LegalNotice',()=>({LegalNotice:()=> <button>开源许可与源码</button>}));
jest.mock('mobx-react',()=>({observer:component=>component}));
jest.mock('antd',()=>{
  const Descriptions=({children})=><dl>{children}</dl>;
  Descriptions.Item=({label,children})=><div>{label}：{children}</div>;
  return {Descriptions,Spin:({children})=><div>{children}</div>,Alert:({message})=><p role="alert">{message}</p>,
    notification:{open:jest.fn()}};
});

let container;
beforeEach(()=>{container=document.createElement('div');document.body.appendChild(container);http.get.mockReset();});
afterEach(()=>{act(()=>{ReactDOM.unmountComponentAtNode(container);});container.remove();});
test('about shows the KOps release and local runtime without contacting or advertising upstream',async()=>{
  http.get.mockResolvedValue({system_version:'Linux',python_version:'3.12',django_version:'4.2',spug_version:'4.0.0'});
  await act(async()=>{ReactDOM.render(<About/>,container);for(let i=0;i<8;i++)await Promise.resolve();});
  expect(http.get.mock.calls).toEqual([['/api/setting/about/']]);
  expect(container.textContent).toContain('KOps · 云枢');
  expect(container.textContent).toContain('0123456');
  expect(container.textContent).toContain('开源许可与源码');
  expect(container.textContent).not.toMatch(/spug|上游官网|如何升级/i);
  expect(container.querySelector('a[href*="spug.cc"]')).toBeNull();
});
