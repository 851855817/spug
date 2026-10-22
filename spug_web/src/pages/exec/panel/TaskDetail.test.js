import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { http, hasPermission } from 'libs';
import TaskDetail from './TaskDetail';

jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, hasPermission: jest.fn()}));
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children, header, title}) => <div>{title}{header}{children}</div>;
  Wrapper.Panel = Wrapper;
  return {Alert: ({message, description}) => <div>{message}{description}</div>, Card: Wrapper, Space: Wrapper,
    Collapse: Wrapper, Tag: Wrapper, Typography: {Text: Wrapper, Paragraph: Wrapper}, Divider: Wrapper,
    Descriptions: Object.assign(Wrapper, {Item: Wrapper}), Spin: Wrapper, Empty: Wrapper,
    Button: ({children, onClick, disabled, loading}) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>,
    Checkbox: ({children, checked, onChange}) => <label><input type="checkbox" checked={checked} onChange={onChange}/>{children}</label>,
    Modal: {confirm: jest.fn()}, Form: {useForm: () => [{}]}, message: {success: jest.fn()}};
});
jest.mock('./ResolveForm', () => () => null);

let container;
const task = {id: 7, state: 'prepared', operation: 'ssl_apply', required_acknowledgements: ['ssl_panel_sync'], targets: [
  {id: 3, panel: {id: 1, name: 'Panel A'}, state: 'prepared', children: [{snapshot: {key: 'site:10', label: 'a.com', target: {site_id: 10}, before: {ssl_sync_inventory: {complete: false}}, plan: {ssl_scope: {policy: 'panel-wide'}}}, state: 'ready'}]},
]};
beforeEach(() => {
  container = document.createElement('div'); document.body.appendChild(container);
  http.get.mockResolvedValue(task); http.post.mockResolvedValue({...task, state: 'running'});
  hasPermission.mockReturnValue(true);
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.clearAllMocks();});
const button = text => [...container.querySelectorAll('button')].find(x => x.textContent === text);

test('frozen SSL preview requires an unchecked explicit acknowledgement and starts only by task id', async () => {
  await act(async () => ReactDOM.render(<TaskDetail taskId={7} acknowledgements={{ssl_panel_sync: '影响未选网站和面板 TLS'}}/>, container));
  expect(container.textContent).toContain('a.com');
  expect(container.textContent).toContain('影响未选网站和面板 TLS');
  expect(button('确认并开始执行').disabled).toBe(true);
  expect(container.querySelector('input').checked).toBe(false);
  await act(async () => {container.querySelector('input').click();});
  await act(async () => {button('确认并开始执行').click();});
  expect(http.post).toHaveBeenCalledWith('/api/baota/tasks/7/start/', {acknowledgements: ['ssl_panel_sync']});
});

test('manage permission does not grant execute; readonly reconcile never resends start', async () => {
  hasPermission.mockImplementation(code => code === 'exec.panel.manage' || code === 'exec.panel.view');
  http.get.mockResolvedValue({...task, state: 'uncertain', targets: []});
  await act(async () => ReactDOM.render(<TaskDetail taskId={7}/>, container));
  expect(button('确认并开始执行')).toBeUndefined();
  await act(async () => {button('只读核验').click();});
  expect(http.post).toHaveBeenCalledWith('/api/baota/tasks/7/reconcile/', {});
  expect(http.post.mock.calls.some(([url]) => url.includes('/start/'))).toBe(false);
});

test('an old in-flight poll cannot overwrite the result of start', async () => {
  jest.useFakeTimers();
  let oldPoll;
  http.get.mockResolvedValueOnce({...task, required_acknowledgements: []})
    .mockImplementationOnce(() => new Promise(resolve => {oldPoll = resolve;}));
  await act(async () => {ReactDOM.render(<TaskDetail taskId={7}/>, container);});
  await act(async () => {jest.advanceTimersByTime(2500);});
  await act(async () => {button('确认并开始执行').click();});
  expect(container.textContent).toContain('执行中');
  await act(async () => {oldPoll({...task, required_acknowledgements: []});});
  expect(container.textContent).toContain('执行中');
  expect(button('确认并开始执行')).toBeUndefined();
  jest.useRealTimers();
});
