import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { hasPermission, http } from 'libs';
import PanelPreview from './PanelPreview';

jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, hasPermission: jest.fn()}));
jest.mock('../exec/panel/TaskItem', () => ({child}) => <div>{child.snapshot.label}</div>);
jest.mock('../exec/panel/TaskEvidence', () => ({Status: ({state}) => <span>{state}</span>}));
jest.mock('antd', () => {
  const React = require('react'); const Wrapper = ({children, title, header}) => <div>{title}{header}{children}</div>;
  return {Card: Wrapper, Space: Wrapper, Collapse: Object.assign(Wrapper, {Panel: Wrapper}), Spin: Wrapper,
    Alert: ({message, description}) => <div>{message}{description}</div>, Empty: ({description}) => <div>{description}</div>,
    Typography: {Text: Wrapper, Paragraph: Wrapper}, Button: ({children, onClick}) => <button onClick={onClick}>{children}</button>};
});
let container;
beforeEach(() => {container = document.createElement('div'); document.body.appendChild(container); hasPermission.mockReturnValue(true);});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.clearAllMocks(); jest.useRealTimers();});

test('a re-preflight preview polls read-only and stops when the frozen preflight is ready', async () => {
  jest.useFakeTimers();
  http.get.mockResolvedValueOnce({id: 3, operation: 'site_basedir', state: 'preflight_queued', targets: []})
    .mockResolvedValueOnce({id: 3, operation: 'site_basedir', state: 'prepared', targets: [
      {id: 2, panel: {name: '客户面板'}, state: 'prepared', children: [{snapshot: {key: 'site:4', label: 'example.com'}, state: 'ready'}]}]});
  await act(async () => ReactDOM.render(<PanelPreview taskId={3}/>, container));
  await act(async () => jest.advanceTimersByTime(2500));
  expect(container.textContent).toContain('example.com');
  expect(container.textContent).toContain('只读');
  expect(container.textContent).not.toContain('确认并开始执行');
  await act(async () => jest.advanceTimersByTime(10000));
  expect(http.get).toHaveBeenCalledTimes(2);
  expect(http.post).not.toHaveBeenCalled();
});

test('panel result previews respect panel view permission', async () => {
  hasPermission.mockReturnValue(false);
  await act(async () => ReactDOM.render(<PanelPreview taskId={3}/>, container));
  expect(http.get).not.toHaveBeenCalled();
});

test.each(['preflighting', 'remote', 'reconciling', 'dispatching', 'paused'])('active %s results continue polling until completed', async state => {
  jest.useFakeTimers();
  http.get.mockResolvedValueOnce({id: 3, operation: 'site_basedir', state, targets: []})
    .mockResolvedValueOnce({id: 3, operation: 'site_basedir', state: 'completed', targets: []});
  await act(async () => ReactDOM.render(<PanelPreview taskId={3}/>, container));
  await act(async () => jest.advanceTimersByTime(2500));
  expect(http.get).toHaveBeenCalledTimes(2);
  await act(async () => jest.advanceTimersByTime(10000));
  expect(http.get).toHaveBeenCalledTimes(2); expect(http.post).not.toHaveBeenCalled();
});

test('terminal and uncertain snapshots can always be refreshed manually using GET only', async () => {
  http.get.mockResolvedValueOnce({id: 3, operation: 'site_basedir', state: 'uncertain', targets: []})
    .mockResolvedValueOnce({id: 3, operation: 'site_basedir', state: 'completed', targets: []});
  await act(async () => ReactDOM.render(<PanelPreview taskId={3}/>, container));
  const refresh = [...container.querySelectorAll('button')].find(item => item.textContent === '刷新结果');
  expect(refresh).toBeDefined();
  await act(async () => refresh.click());
  expect(container.textContent).toContain('completed'); expect(http.get).toHaveBeenCalledTimes(2);
  expect(http.post).not.toHaveBeenCalled();
});
