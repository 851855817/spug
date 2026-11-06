import React from 'react';
import ReactDOM from 'react-dom';
import {act} from 'react-dom/test-utils';
import Login from './index';
import {http, updatePermissions} from 'libs';
import history from 'libs/history';
import {Modal, message} from 'antd';

const mockValues = {username: 'fixture-only-user', password: 'fixture-only-password'};
jest.mock('libs', () => ({http: {post: jest.fn()}, updatePermissions: jest.fn(), t: value => value,
  langMode: 'zh', setLanguage: jest.fn(), docsUrl: () => 'https://example.test/docs'}));
jest.mock('libs/history', () => ({push: jest.fn(), location: {state: null}}));
jest.mock('pages/config/environment/store', () => ({}));
jest.mock('pages/config/app/store', () => ({}));
jest.mock('pages/deploy/request/store', () => ({}));
jest.mock('pages/exec/task/store', () => ({}));
jest.mock('pages/host/store', () => ({}));
jest.mock('../../branding/Brand', () => () => <span>KOps</span>);
jest.mock('../../branding/LegalNotice', () => ({LegalNotice: () => null}));
jest.mock('antd', () => {
  const Form = ({children}) => <form>{children}</form>;
  Form.useForm = () => [{getFieldsValue: () => ({...mockValues})}];
  Form.Item = ({children, hidden}) => hidden ? null : <div>{children}</div>;
  const Input = ({placeholder}) => <input placeholder={placeholder}/>;
  Input.Password = Input;
  return {Form, Input, Modal: {warning: jest.fn()}, message: {error: jest.fn()},
    Button: ({children, onClick, disabled}) => <button type="button" onClick={onClick} disabled={disabled}>{children}</button>,
    Dropdown: ({children}) => <div>{children}</div>,
    Tabs: ({items, onTabClick}) => <div>{items.map(item => <button key={item.key} type="button" onClick={() => onTabClick(item.key)}>{item.label}</button>)}</div>};
});

let container;
const flush = async () => {for (let i = 0; i < 8; i++) await Promise.resolve();};
const click = async text => act(async () => {
  [...container.querySelectorAll('button')].find(button => button.textContent === text).click(); await flush();
});
const authenticated = {id: 7, access_token: 'fixture-session', nickname: '模拟账号', is_supper: true, permissions: {}};
beforeEach(() => {
  localStorage.clear(); jest.clearAllMocks(); jest.useFakeTimers();
  delete mockValues.captcha; history.location.state = null;
  container = document.createElement('div'); document.body.appendChild(container);
  act(() => {ReactDOM.render(<Login/>, container);});
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.clearAllTimers(); jest.useRealTimers();});

test.each([false, undefined, true])('successful login with has_real_ip=%s enters directly without a modal', async hasRealIp => {
  http.post.mockResolvedValue({...authenticated, has_real_ip: hasRealIp});
  await click('登录');
  expect(http.post).toHaveBeenCalledWith('/api/account/login/', {...mockValues, type: 'default'});
  expect(Modal.warning).not.toHaveBeenCalled();
  expect(localStorage.getItem('token')).toBe('fixture-session');
  expect(updatePermissions).toHaveBeenCalledTimes(1);
  expect(history.push).toHaveBeenCalledWith('/home');
});

test('LDAP preserves its request type and requested destination without a warning', async () => {
  history.location.state = {from: '/monitor/ssl'};
  http.post.mockResolvedValue({...authenticated, has_real_ip: false});
  await click('LDAP登录'); await click('登录');
  expect(http.post).toHaveBeenCalledWith('/api/account/login/', {...mockValues, type: 'ldap'});
  expect(history.push).toHaveBeenCalledWith('/monitor/ssl');
  expect(localStorage.getItem('login_type')).toBe('ldap');
  expect(Modal.warning).not.toHaveBeenCalled();
});

test('MFA still blocks session creation until the verification code succeeds', async () => {
  http.post.mockResolvedValueOnce({required_mfa: true, has_real_ip: false}).mockResolvedValueOnce({...authenticated, has_real_ip: false});
  await click('登录');
  expect(container.querySelector('input[placeholder="请输入验证码"]')).not.toBeNull();
  expect(localStorage.getItem('token')).toBeNull(); expect(history.push).not.toHaveBeenCalled();
  await click('登录'); expect(message.error).toHaveBeenCalledWith('请输入验证码'); expect(http.post).toHaveBeenCalledTimes(1);
  mockValues.captcha = '123456'; await click('登录');
  expect(http.post).toHaveBeenLastCalledWith('/api/account/login/', {...mockValues, type: 'default'});
  expect(localStorage.getItem('token')).toBe('fixture-session'); expect(history.push).toHaveBeenCalledWith('/home');
  expect(Modal.warning).not.toHaveBeenCalled();
});

test('a rejected password or source verification does not create a session or navigate', async () => {
  http.post.mockRejectedValue(new Error('fixture rejection'));
  await click('登录');
  expect(localStorage.getItem('token')).toBeNull(); expect(updatePermissions).not.toHaveBeenCalled();
  expect(history.push).not.toHaveBeenCalled(); expect(Modal.warning).not.toHaveBeenCalled();
});
