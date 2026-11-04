import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { hasPermission, http } from 'libs';
import Reminders from './Reminders';
jest.mock('libs', () => ({hasPermission: jest.fn(), http: {get: jest.fn(), post: jest.fn()}}));
jest.mock('antd', () => ({
  Button: ({children, onClick, disabled, loading}) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>,
  Alert: ({message}) => <div role="alert">{message}</div>,
  Checkbox: ({checked, onChange, children}) => <label><input type="checkbox" checked={checked} onChange={onChange}/>{children}</label>,
}));
const flush = async () => {for (let i = 0; i < 20; i++) await Promise.resolve();};
let container;
beforeEach(() => {jest.useFakeTimers(); hasPermission.mockReturnValue(true); localStorage.setItem('id', 'test-owner');
  container = document.createElement('div'); document.body.appendChild(container);
  http.get.mockResolvedValue({items: [{id: 4, domain: 'soon.example.test', source_name: '生产服务器', message: '证书 3 天后到期', muted: false}], total: 1, unread_count: 1});
  http.post.mockResolvedValue({});});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.resetAllMocks(); jest.useRealTimers(); localStorage.clear();});
const render = async () => act(async () => {ReactDOM.render(<Reminders/>, container); await flush();});
test('SSL reminders use only the separate local API and mute only the current account', async () => {
  await render(); expect(container.textContent).toContain('soon.example.test'); expect(http.post).not.toHaveBeenCalled();
  await act(async () => {[...container.querySelectorAll('button')].find(item => item.textContent === '不再提醒').click(); await flush();});
  expect(http.post).toHaveBeenCalledWith('/api/monitor/ssl/reminders/4/mute/', {muted: true});
  expect(http.get.mock.calls.every(([path]) => path === '/api/monitor/ssl/reminders/')).toBe(true);
});
test('failed refresh clears previous domains and count rather than showing stale reminders as current', async () => {
  await render(); http.get.mockRejectedValue(new Error('secret transport data'));
  await act(async () => {jest.advanceTimersByTime(60000); await flush();});
  expect(container.textContent).not.toContain('soon.example.test'); expect(container.textContent).toContain('SSL 提醒读取失败');
  expect(container.textContent).not.toContain('secret transport'); expect(container.textContent).not.toContain('1 条');
});
test('ordinary accounts do not request the admin reminder endpoint', async () => {
  hasPermission.mockReturnValue(false); await render(); expect(http.get).not.toHaveBeenCalled(); expect(container.textContent).toBe('');
});
test('late replies cannot expose domains after the account changes', async () => {
  let finish; http.get.mockImplementation(() => new Promise(resolve => {finish = resolve;})); await render(); localStorage.setItem('id', 'different-owner');
  await act(async () => {finish({items: [{id: 4, domain: 'private.example.test'}], total: 1, unread_count: 1}); await flush();});
  expect(container.textContent).not.toContain('private.example.test');
});
