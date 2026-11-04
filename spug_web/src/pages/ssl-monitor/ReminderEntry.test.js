import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { hasPermission, http } from 'libs';
import ReminderEntry from './ReminderEntry';
jest.mock('libs', () => ({hasPermission: jest.fn(), http: {get: jest.fn()}}));
jest.mock('./Reminders', () => {
  const React = require('react'), {http} = require('libs');
  return function Reminders() {React.useEffect(() => {http.get('/api/monitor/ssl/reminders/');}, []); return <div>独立 SSL 提醒</div>;};
});
jest.mock('antd', () => ({
  Button: ({children, onClick}) => <button onClick={onClick}>{children}</button>,
  Modal: ({visible, children}) => visible ? <div role="dialog">{children}</div> : null,
}));
let container;
beforeEach(() => {hasPermission.mockReturnValue(true); container = document.createElement('div'); document.body.appendChild(container);});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.resetAllMocks();});
test('homepage and topbar do not request SSL data until the administrator opens the separate entry', async () => {
  await act(async () => {ReactDOM.render(<ReminderEntry/>, container);});
  expect(http.get).not.toHaveBeenCalled();
  await act(async () => {container.querySelector('button').click(); for (let i = 0; i < 20; i++) await Promise.resolve();});
  expect(http.get).toHaveBeenCalledWith('/api/monitor/ssl/reminders/'); expect(container.textContent).toContain('独立 SSL 提醒');
});
test('ordinary accounts have no SSL reminder entry', async () => {
  hasPermission.mockReturnValue(false); await act(async () => {ReactDOM.render(<ReminderEntry compact/>, container);});
  expect(container.querySelector('button')).toBeNull(); expect(http.get).not.toHaveBeenCalled();
});
