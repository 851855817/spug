import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { http } from 'libs';
import UploadForm from './UploadForm';

jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}}));
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  const Input = ({onChange, ...props}) => <input {...props} onChange={onChange}/>;
  return {Alert: ({message, description}) => <div>{message}{description}</div>, Space: Wrapper,
    Typography: {Text: Wrapper, Paragraph: Wrapper}, Form: Object.assign(Wrapper, {Item: Wrapper}), Input,
    Button: ({children, disabled, loading, onClick}) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>,
    Progress: ({percent}) => <div>{percent}%</div>};
});
const panels = [1, 2].map(id => ({id, name: `Panel ${id}`, api_configured: true, api_checked: true, api_version: '13.1.0'}));
let container;
beforeEach(() => {container = document.createElement('div'); document.body.appendChild(container);});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.resetAllMocks();});
const button = () => [...container.querySelectorAll('button')].find(item => item.textContent.includes('上传并生成冻结预检'));
const change = (label, value) => act(() => {const input = container.querySelector(`[aria-label="${label}"]`); Simulate.change(input, {target: {value}});});
function select(files, directory = false) {act(() => {Simulate.change(container.querySelectorAll('input[type=file]')[directory ? 1 : 0], {target: {files, value: 'fake'}});});}
const makeFile = (name, relativePath) => {const value = new File(['abc'], name); if (relativePath) Object.defineProperty(value, 'webkitRelativePath', {value: relativePath}); return value;};

test('stages directory files sequentially, seals and creates an override request without starting', async () => {
  const prepared = jest.fn(), busy = jest.fn();
  http.post.mockResolvedValueOnce({id: 4, files: [{id: 11, path: 'site/a.txt', size: 3}, {id: 12, path: 'site/b.txt', size: 3}]})
    .mockResolvedValueOnce({}).mockResolvedValueOnce({}).mockResolvedValueOnce({}).mockResolvedValueOnce({id: 7});
  await act(async () => ReactDOM.render(<UploadForm panels={panels} options={{}} onPrepared={prepared} onBusyChange={busy}/>, container));
  change('公共目标目录', '/www/common'); change('Panel 1 单独目标目录', '/www/a');
  select([makeFile('a.txt', 'site/a.txt'), makeFile('b.txt', 'site/b.txt')], true);
  expect(container.textContent).toContain('site/a.txt');
  await act(async () => {button().click();});
  expect(http.post.mock.calls.map(call => call[0])).toEqual(['/api/baota/uploads/', '/api/baota/uploads/4/files/11/',
    '/api/baota/uploads/4/files/12/', '/api/baota/uploads/4/seal/', '/api/baota/tasks/']);
  expect(http.post.mock.calls[0][1]).toEqual({files: [{path: 'site/a.txt', size: 3}, {path: 'site/b.txt', size: 3}]});
  expect(http.post.mock.calls[1][1].get('file').name).toBe('a.txt');
  expect(http.post.mock.calls[1][2]).toMatchObject({timeout: 300000, onUploadProgress: expect.any(Function)});
  expect(http.post.mock.calls[4][1]).toEqual({operation: 'file_upload', panel_ids: [1, 2], params: {bundle_id: 4, target_dir: '/www/common'},
    params_by_panel: {'1': {target_dir: '/www/a'}}});
  expect(prepared).toHaveBeenCalledWith(7); expect(busy.mock.calls).toEqual([[true], [false]]);
  expect(button().disabled).toBe(true);
});

test('busy preparation blocks double clicks and changing selected files', async () => {
  let finish;
  http.post.mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}))
    .mockResolvedValueOnce({}).mockResolvedValueOnce({}).mockResolvedValueOnce({id: 7});
  await act(async () => ReactDOM.render(<UploadForm panels={[panels[0]]} options={{}} onPrepared={() => {}}/>, container));
  change('公共目标目录', '/www/a'); select([makeFile('a.txt')]);
  act(() => {Simulate.click(button()); Simulate.click(button());});
  expect(http.post).toHaveBeenCalledTimes(1);
  expect([...container.querySelectorAll('input')].every(input => input.disabled)).toBe(true);
  expect(container.textContent).toContain('创建上传批次');
  await act(async () => finish({id: 4, files: [{id: 11, path: 'a.txt', size: 3}]}));
});

test('invalid overrides block staging and a failed stage discards the draft for a new attempt', async () => {
  http.post.mockResolvedValueOnce({id: 4, files: [{id: 11, path: 'a.txt', size: 3}]}).mockRejectedValueOnce('上传失败').mockResolvedValueOnce({});
  await act(async () => ReactDOM.render(<UploadForm panels={[panels[0]]} options={{}} onPrepared={() => {}}/>, container));
  change('公共目标目录', '/www/a'); change('Panel 1 单独目标目录', '/www/../bad'); select([makeFile('a.txt')]);
  await act(async () => {button().click();}); expect(http.post).not.toHaveBeenCalled();
  expect(container.textContent).toContain('目录');
  change('Panel 1 单独目标目录', '');
  await act(async () => {button().click();});
  expect(http.post).toHaveBeenLastCalledWith('/api/baota/uploads/4/discard/', {});
  expect(container.textContent).toContain('上传失败'); expect(button().disabled).toBe(false);
});

test('unknown task creation outcome does not discard a sealed bundle and explains how to check', async () => {
  http.post.mockResolvedValueOnce({id: 4, files: [{id: 11, path: 'a.txt', size: 3}]}).mockResolvedValueOnce({})
    .mockResolvedValueOnce({}).mockRejectedValueOnce('请求异常');
  await act(async () => ReactDOM.render(<UploadForm panels={[panels[0]]} options={{}} onPrepared={() => {}}/>, container));
  change('公共目标目录', '/www/a'); select([makeFile('a.txt')]);
  await act(async () => {button().click();});
  expect(http.post.mock.calls.some(([url]) => url.includes('/discard/'))).toBe(false);
  expect(container.textContent).toContain('任务记录');
});

test('blocked panels display the blocker and never stage files', async () => {
  await act(async () => ReactDOM.render(<UploadForm panels={[{...panels[0], api_configured: false}]} options={{}}/>, container));
  expect(container.textContent).toContain('API 尚未配置');
  change('公共目标目录', '/www/a'); select([makeFile('a.txt')]);
  expect(button().disabled).toBe(true); expect(http.post).not.toHaveBeenCalled();
});

test('all-individual destinations work with an empty common directory and changing common preserves overrides', async () => {
  http.post.mockResolvedValueOnce({id: 4, files: [{id: 11, path: 'a.txt', size: 3}]}).mockResolvedValueOnce({})
    .mockResolvedValueOnce({}).mockResolvedValueOnce({id: 7});
  await act(async () => {ReactDOM.render(<UploadForm panels={panels} options={{}} onPrepared={() => {}}/>, container);});
  change('公共目标目录', '/www/common'); change('Panel 1 单独目标目录', '/www/a'); change('Panel 2 单独目标目录', '/www/b');
  change('公共目标目录', '/www/changed');
  expect(container.querySelector('[aria-label="Panel 1 单独目标目录"]').value).toBe('/www/a');
  change('公共目标目录', ''); select([makeFile('a.txt')]);
  await act(async () => {button().click();});
  expect(http.post).toHaveBeenLastCalledWith('/api/baota/tasks/', {operation: 'file_upload', panel_ids: [1, 2],
    params: {bundle_id: 4, target_dir: ''}, params_by_panel: {'1': {target_dir: '/www/a'}, '2': {target_dir: '/www/b'}}});
});

test('a new selection replaces directory files and configured limits are shown and enforced before staging', async () => {
  await act(async () => {ReactDOM.render(<UploadForm panels={[panels[0]]} options={{upload_limits: {files: 1, file_bytes: 3, bundle_bytes: 3}}}/>, container);});
  select([makeFile('a.txt', 'site/a.txt')], true);
  expect(container.textContent).toContain('site/a.txt');
  select([makeFile('b.txt')]);
  expect(container.textContent).not.toContain('site/a.txt'); expect(container.textContent).toContain('b.txt');
  select([makeFile('a.txt'), makeFile('b.txt')]);
  expect(container.textContent).toContain('文件数量最多 1 个'); expect(button().disabled).toBe(true);
  expect(http.post).not.toHaveBeenCalled();
});
