import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { http, hasPermission } from 'libs';
import TaskDetail from './TaskDetail';
import { Modal } from 'antd';

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
    InputNumber: ({value, onChange, min, max, ...props}) => <input {...props} type="number" value={value} min={min} max={max} onChange={e => onChange(Number(e.target.value))}/>,
    Modal: {confirm: jest.fn()}, Form: {useForm: () => [{}]}, message: {success: jest.fn()}};
});
jest.mock('./ResolveForm', () => () => null);
jest.mock('./UploadResults', () => ({taskId, targetId, summary, onResolved}) => <div>文件结果 {taskId}/{targetId} {summary?.files}
  <button onClick={() => onResolved({id: taskId, operation: 'file_upload', state: 'completed', targets: []})}>模拟文件判定</button></div>);

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
  expect(http.post).toHaveBeenCalledWith('/api/baota/tasks/7/start/', {acknowledgements: ['ssl_panel_sync'], execution_policy: {batch_size: 1, failure_threshold: 1}});
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

test('uploads show file counts and a frozen overwrite acknowledgement with per-file resolution only', async () => {
  http.get.mockResolvedValue({...task, operation: 'file_upload', required_acknowledgements: ['upload_overwrite'], targets: [
    {id: 3, state: 'prepared', panel: {name: 'A'}, summary: {upload: {files: 9, pending: 7, uncertain: 2, success: 0, failed: 0}},
      children: [{state: 'uncertain', snapshot: {key: 'upload', label: '上传9个文件', target: {}, plan: {target_dir: '/www/a', file_count: 9}}}]},
  ]});
  await act(async () => {ReactDOM.render(<TaskDetail taskId={7}/>, container);});
  expect(container.textContent).toContain('待执行 7'); expect(container.textContent).toContain('待核实 2');
  expect(container.textContent).toContain('文件结果 7/3 9');
  expect(button('人工判定')).toBeUndefined();
  expect(container.textContent).toContain('覆盖同名文件');
  expect(button('确认并开始执行').disabled).toBe(true);
  await act(async () => {container.querySelector('input[type=checkbox]').click();});
  await act(async () => {button('确认并开始执行').click();});
  expect(http.post).toHaveBeenCalledWith('/api/baota/tasks/7/start/', {acknowledgements: ['upload_overwrite'], execution_policy: {batch_size: 1, failure_threshold: 1}});
});

test('new starts opt into one-panel batches and can explicitly keep legacy execution', async () => {
  http.get.mockResolvedValue({...task, required_acknowledgements: []});
  await act(async () => ReactDOM.render(<TaskDetail taskId={7}/>, container));
  expect(container.textContent).toContain('分批执行');
  const checkbox = [...container.querySelectorAll('label')].find(label => label.textContent.includes('分批执行')).querySelector('input');
  expect(checkbox.checked).toBe(true);
  await act(async () => checkbox.click());
  await act(async () => button('确认并开始执行').click());
  expect(http.post).toHaveBeenCalledWith('/api/baota/tasks/7/start/', {acknowledgements: []});
});

test('paused batches poll read-only and continue only after explicit trip confirmation', async () => {
  jest.useFakeTimers();
  http.get.mockResolvedValue({...task, state: 'paused', batch: {enabled: true, index: 1, total: 3,
    state: 'tripped', pending: 2, requires_acknowledgement: true, pause_reason: '本批失败达到阈值'}});
  await act(async () => ReactDOM.render(<TaskDetail taskId={7}/>, container));
  expect(container.textContent).toContain('第 1 / 3 批');
  expect(container.textContent).toContain('本批失败达到阈值');
  await act(async () => jest.advanceTimersByTime(2500));
  expect(http.post).not.toHaveBeenCalled();
  await act(async () => button('核对结果并继续').click());
  expect(http.post).not.toHaveBeenCalled();
  await act(async () => Modal.confirm.mock.calls[0][0].onOk());
  expect(http.post).toHaveBeenCalledWith('/api/baota/tasks/7/continue/', {acknowledge: true});
  jest.useRealTimers();
});

test('only execute permission can continue and unresolved batches remain held', async () => {
  hasPermission.mockImplementation(code => code === 'exec.panel.manage' || code === 'exec.panel.view');
  http.get.mockResolvedValue({...task, state: 'paused', batch: {enabled: true, index: 1, total: 3,
    state: 'tripped', pending: 2, unresolved: true, requires_acknowledgement: true}});
  await act(async () => ReactDOM.render(<TaskDetail taskId={7}/>, container));
  expect(button('核对结果并继续')).toBeUndefined();
  expect(http.post).not.toHaveBeenCalled();
});

test('verified SSL deployment has a separate explicit confirmation for the fixed current child', async () => {
  http.get.mockResolvedValue({...task, state: 'paused', batch: {enabled: true, index: 1, total: 3,
    state: 'tripped', pending: 2, unresolved: true, deployable: [{target_id: 3, child_key: 'site:10', panel_name: 'Panel A', label: 'a.com'}]}});
  await act(async () => ReactDOM.render(<TaskDetail taskId={7}/>, container));
  expect(button('核对结果并继续')).toBeUndefined();
  expect(container.textContent).toContain('确认继续当前证书部署阶段');
  expect(http.post).not.toHaveBeenCalled();
  await act(async () => button('确认继续当前证书部署阶段').click());
  expect(Modal.confirm.mock.calls[0][0].content).toContain('a.com');
  expect(http.post).not.toHaveBeenCalled();
  await act(async () => Modal.confirm.mock.calls[0][0].onOk());
  expect(http.post).toHaveBeenCalledWith('/api/baota/tasks/7/continue/', {
    acknowledge: true, deploy: {target_id: 3, child_key: 'site:10'}});
});

test('SSL phase authorization is hidden without execute permission or while active', async () => {
  hasPermission.mockImplementation(code => code !== 'exec.panel.execute');
  http.get.mockResolvedValue({...task, state: 'paused', batch: {enabled: true, state: 'tripped', active: false,
    deployable: [{target_id: 3, child_key: 'site:10'}]}});
  await act(async () => ReactDOM.render(<TaskDetail taskId={7}/>, container));
  expect(button('确认继续当前证书部署阶段')).toBeUndefined();
  expect(http.post).not.toHaveBeenCalled();
});
