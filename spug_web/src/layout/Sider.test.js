import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { hasPermission, history } from 'libs';
import Sider from './Sider';
import { Router } from 'react-router-dom';
import { createMemoryHistory } from 'history';

let navigationHistory;
let mockMenuProps;
const makeHistory = (pathname = '/home') => {
  window.history.replaceState({}, '', pathname);
  const navigation = createMemoryHistory({initialEntries: [pathname]});
  navigation.listen(location => window.history.replaceState({}, '', location.pathname));
  return navigation;
};

jest.mock('libs', () => ({hasPermission: jest.fn(), history: {push: jest.fn()}}));
jest.mock('../routes', () => [
  {title: '首页', path: '/home'},
  {title: '批量执行', child: [{title: '命令', path: '/exec/task'}, {title: '模板', path: '/exec/template'},
    {title: '网站', path: '/exec/sites'}, {title: '无权项', path: '/exec/secret', auth: 'denied'}, {path: '/exec/internal'}]},
  {title: '系统管理', child: [{title: '系统设置', path: '/system/setting'}, {title: '角色管理', path: '/system/role'}]},
  {title: '监控中心', child: [{title: '服务器监控', path: '/monitor/servers'}, {title: '监控任务', path: '/monitor'}]},
]);
jest.mock('antd', () => ({
  Layout: {Sider: ({children}) => <aside>{children}</aside>},
  Button: ({children, onClick}) => <button onClick={onClick}>{children}</button>,
  Menu: props => {mockMenuProps = props; const {items, onSelect} = props; return <div>{items.filter(Boolean).map(item => <section key={item.key} data-menu-group={item.key}>
    {item.label}{item.children?.filter(Boolean).map(child => <div key={child.key} onClick={() => onSelect({key: child.key})}>{child.label}</div>)}
  </section>)}</div>},
}));
let container;
const flush = async () => {for (let i = 0; i < 10; i++) await Promise.resolve();};
const render = async (collapsed = false) => act(async () => {ReactDOM.render(<Router history={navigationHistory}><Sider collapsed={collapsed}/></Router>, container); await flush();});
const navigate = async route => act(async () => {navigationHistory.push(route); await flush();});
const setOpen = async keys => act(async () => {mockMenuProps.onOpenChange(keys); await flush();});
const button = name => [...container.querySelectorAll('button')].find(node => node.textContent === name);
const path = name => container.querySelector(`[data-nav-path="${name}"]`);
const paths = group => [...container.querySelector(`[data-menu-group="${group}"]`).querySelectorAll('[data-nav-path]')].map(node => node.dataset.navPath);
const click = async name => act(async () => {button(name).click(); await flush();});
const drag = async (from, to) => act(async () => {
  Simulate.dragStart(path(from), {dataTransfer: {setData: jest.fn()}});
  Simulate.dragOver(path(to)); Simulate.drop(path(to)); await flush();
});
beforeEach(() => {
  navigationHistory = makeHistory();
  history.push.mockImplementation(route => navigationHistory.push(route));
  localStorage.clear(); localStorage.setItem('id', '7'); hasPermission.mockImplementation(code => code !== 'denied');
  container = document.createElement('div'); document.body.appendChild(container);
});
afterEach(async () => {act(() => {ReactDOM.unmountComponentAtNode(container);}); await flush(); container.remove();
  jest.restoreAllMocks(); jest.clearAllMocks(); localStorage.clear();});

test('dragging same-group child menus persists order without navigating or changing permissions', async () => {
  await render(); await click('调整导航');
  await drag('/exec/sites', '/exec/task');
  expect(paths('批量执行')).toEqual(['/exec/sites', '/exec/task', '/exec/template']);
  expect(container.textContent).not.toContain('无权项'); expect(path('/exec/internal')).toBeNull();
  expect(history.push).not.toHaveBeenCalled();
  await act(async () => {path('/exec/task').click(); await flush();}); expect(history.push).not.toHaveBeenCalled();
  await click('完成'); await act(async () => {path('/exec/task').click(); await flush();});
  expect(history.push).toHaveBeenCalledWith('/exec/task');
  act(() => {ReactDOM.unmountComponentAtNode(container);}); await render();
  expect(paths('批量执行')).toEqual(['/exec/sites', '/exec/task', '/exec/template']);
});

test('cross-group drops and external drag data cannot move menu items', async () => {
  await render(); await click('调整导航');
  await drag('/exec/sites', '/monitor/servers');
  expect(paths('批量执行')).toEqual(['/exec/task', '/exec/template', '/exec/sites']);
  expect(paths('监控中心')).toEqual(['/monitor/servers', '/monitor']);
  await act(async () => {Simulate.dragEnd(path('/exec/sites')); Simulate.drop(path('/exec/task'),
    {dataTransfer: {getData: () => '/exec/sites'}}); await flush();});
  expect(paths('批量执行')[0]).toBe('/exec/task');
});

test('dragging downward can put a child at the end of its group', async () => {
  await render(); await click('调整导航'); await drag('/exec/task', '/exec/sites');
  expect(paths('批量执行')).toEqual(['/exec/template', '/exec/sites', '/exec/task']);
});

test('keyboard reordering and restoring defaults are saved per account', async () => {
  await render(); await click('调整导航');
  await act(async () => {Simulate.keyDown(path('/exec/template'), {altKey: true, key: 'ArrowUp'}); await flush();});
  expect(paths('批量执行')[0]).toBe('/exec/template');
  act(() => {ReactDOM.unmountComponentAtNode(container);});
  localStorage.setItem('id', '8'); await render(); expect(paths('批量执行')[0]).toBe('/exec/task');
  act(() => {ReactDOM.unmountComponentAtNode(container);});
  localStorage.setItem('id', '7'); await render(); expect(paths('批量执行')[0]).toBe('/exec/template');
  await click('调整导航'); await click('恢复默认'); expect(paths('批量执行')[0]).toBe('/exec/task');
  act(() => {ReactDOM.unmountComponentAtNode(container);}); await render(); expect(paths('批量执行')[0]).toBe('/exec/task');
});

test('untrusted saved paths never add hidden routes and new visible children are appended', async () => {
  localStorage.setItem('spug:nav-order:v1:7', JSON.stringify({exec: ['/exec/secret', '/exec/sites', '/exec/sites', '/external']}));
  await render(); expect(paths('批量执行')).toEqual(['/exec/sites', '/exec/task', '/exec/template']);
  expect(container.textContent).not.toContain('无权项');
});

test('storage failures retain the session order and show a useful saving message', async () => {
  await render(); await click('调整导航');
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {throw new Error('unavailable');});
  await drag('/exec/sites', '/exec/task'); expect(paths('批量执行')[0]).toBe('/exec/sites');
  expect(container.textContent).toContain('未能保存');
});


test('the active system group can stay closed through unrelated UI renders', async () => {
  navigationHistory = makeHistory('/system/setting');
  await render();
  expect(mockMenuProps.openKeys).toContain('系统管理');
  expect(mockMenuProps.selectedKeys).toEqual(['/system/setting']);
  await setOpen([]);
  expect(mockMenuProps.openKeys).toEqual([]);
  await click('调整导航');
  expect(mockMenuProps.openKeys).toEqual([]);
  expect(navigationHistory.location.pathname).toBe('/system/setting');
  expect(history.push).not.toHaveBeenCalled();
  await setOpen(['系统管理']);
  expect(mockMenuProps.openKeys).toEqual(['系统管理']);
});

test('navigation and back open their group while each active group remains manually collapsible', async () => {
  await render();
  await navigate('/system/setting');
  await setOpen([]);
  expect(mockMenuProps.openKeys).toEqual([]);
  await navigate('/exec/task');
  expect(mockMenuProps.openKeys).toEqual(['批量执行']);
  expect(mockMenuProps.selectedKeys).toEqual(['/exec/task']);
  await setOpen([]);
  expect(mockMenuProps.openKeys).toEqual([]);
  await act(async () => {navigationHistory.goBack(); await flush();});
  expect(mockMenuProps.openKeys).toEqual(['系统管理']);
  expect(mockMenuProps.selectedKeys).toEqual(['/system/setting']);
  await setOpen([]);
  await navigate('/system/role');
  expect(mockMenuProps.openKeys).toEqual(['系统管理']);
});

test('expanding the whole sidebar reveals the current group without locking it open', async () => {
  navigationHistory = makeHistory('/system/setting');
  await render(); await setOpen([]);
  await render(true);
  expect(mockMenuProps.openKeys).toEqual([]);
  await render(false);
  expect(mockMenuProps.openKeys).toEqual(['系统管理']);
  await setOpen([]);
  expect(mockMenuProps.openKeys).toEqual([]);
});
