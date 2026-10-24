import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { hasPermission, http } from 'libs';
import Inventory from './index';

jest.mock('libs', () => ({hasPermission: jest.fn(), http: {get: jest.fn(), post: jest.fn()}}));
jest.mock('components', () => {
  const Wrapper = ({children}) => <div>{children}</div>;
  Wrapper.Item = Wrapper;
  return {AuthDiv: Wrapper, Breadcrumb: Wrapper};
});
jest.mock('../../components/ResizableTable', () => ({columns, dataSource, pagination}) => <div>
  {dataSource.map(row => <div key={row.id || row.panel_id} data-row={row.id || `panel-${row.panel_id}`}>
    {columns.map(column => <div key={column.key || column.dataIndex}>{column.render ? column.render(row[column.dataIndex], row) : row[column.dataIndex]}</div>)}
  </div>)}
  {pagination && <button disabled={pagination.current * pagination.pageSize >= pagination.total}
    onClick={() => pagination.onChange(pagination.current + 1, pagination.pageSize)}>下一页</button>}
</div>);
jest.mock('antd', () => {
  const Wrapper = ({children, title, extra}) => <div>{title}{extra}{children}</div>;
  const Select = ({value, onChange, options = [], mode, disabled, ...props}) => <select aria-label={props['aria-label']}
    multiple={mode === 'multiple'} value={value == null ? (mode ? [] : '') : value} disabled={disabled}
    onChange={event => onChange(mode ? [...event.target.selectedOptions].map(option => Number(option.value)) : event.target.value)}>
    {!mode && <option value="">全部</option>}{options.map(option => <option key={option.value} value={option.value} disabled={option.disabled}>{option.label}</option>)}
  </select>;
  return {Card: Wrapper, Space: Wrapper, Tag: Wrapper, Typography: {Text: Wrapper, Paragraph: Wrapper}, Select,
    TreeSelect: ({treeData, ...props}) => <Select {...props} options={treeData.map(node => ({value: node.value, label: node.title}))}/>,
    Alert: ({message, description}) => <div role="alert">{message}{description}</div>,
    Button: ({children, onClick, disabled, loading}) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>,
    Input: {Search: ({onSearch, ...props}) => <input aria-label={props['aria-label']}
      onKeyDown={event => {if (event.key === 'Enter') onSearch(event.target.value);}}/>}};
});

let container;
let options;
let data;
const site = {id: '1:7', panel_id: 1, panel_name: '主站', ip: '192.0.2.1', group_id: 1, group_name: '生产',
  site_id: 7, name: 'example.test', domains: [{name: 'example.test', port: 443}], php_version: '82',
  site_status: 'running', ssl_state: 'unconfigured', expires_at: '', sampled_at: '2026-10-24T10:00:00Z', stale: true};
const panel = {panel_id: 1, name: '主站', configured: true, status: 'error', message: '采集失败，请稍后重试',
  sampled_at: '2026-10-24T10:00:00Z', attempted_at: '2026-10-24T11:00:00Z', stale: true};
const flush = async () => {for (let i = 0; i < 12; i++) await Promise.resolve();};
const button = text => [...container.querySelectorAll('button')].find(node => node.textContent.startsWith(text));
const render = async (kind = 'sites') => {await act(async () => {ReactDOM.render(<Inventory kind={kind}/>, container); await flush();});};
const change = async (label, value) => {await act(async () => {
  Simulate.change(container.querySelector(`[aria-label="${label}"]`), {target: {value}}); await flush();
});};

beforeEach(() => {
  container = document.createElement('div'); document.body.appendChild(container);
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
  hasPermission.mockReturnValue(true);
  options = {panels: [{id: 1, name: '主站', ip: '192.0.2.1', group_id: 1, configured: true},
    {id: 2, name: '备用', ip: '192.0.2.2', group_id: 2, configured: true},
    {id: 3, name: '未配置', group_id: 1, configured: false}], groups: [{id: 1, name: '生产'}, {id: 2, name: '子组', parent_id: 1}]};
  data = {items: [site], total: 41, page: 1, page_size: 20, summary: {total: 1, unconfigured: 1, stale: 1}, panels: [panel]};
  http.get.mockImplementation(path => Promise.resolve(path.endsWith('options/') ? options : data));
  http.post.mockResolvedValue({...panel, status: 'ok', stale: false});
});
afterEach(async () => {act(() => {ReactDOM.unmountComponentAtNode(container);}); await flush(); container.remove(); jest.clearAllMocks();});

test('mount reads cached inventory only and displays old data with both collection timestamps', async () => {
  await render();
  expect(http.post).not.toHaveBeenCalled();
  expect(http.get).toHaveBeenCalledWith('/api/baota/inventory/sites/', {params: {page: 1, page_size: 20}});
  const row = container.querySelector('[data-row="1:7"]');
  expect(row.textContent).toContain('旧缓存');
  expect(row.textContent).toContain('2026-10-24');
  expect(container.textContent).toContain('采集失败，请稍后重试');
  expect(container.textContent).toContain('公网 TLS');
  expect(row.querySelector('a').getAttribute('href')).toBe('/exec/panel?panel_id=1&site_id=7&operation=ssl_apply');
});

test('filters and pagination are server queries and reject an earlier response', async () => {
  await render();
  let completeOld;
  http.get.mockImplementation((path, config) => {
    if (path.endsWith('options/')) return Promise.resolve(options);
    if (config.params.state === 'expired') return new Promise(resolve => {completeOld = resolve;});
    return Promise.resolve({...data, items: [{...site, name: '最新筛选结果'}]});
  });
  await change('SSL 状态', 'expired');
  await change('SSL 状态', 'unknown');
  await act(async () => {completeOld({...data, items: [{...site, name: '过时响应'}]}); await flush();});
  expect(container.textContent).toContain('最新筛选结果');
  expect(container.textContent).not.toContain('过时响应');
  await act(async () => {button('下一页').click(); await flush();});
  expect(http.get).toHaveBeenLastCalledWith('/api/baota/inventory/sites/', {params: {page: 2, page_size: 20, state: 'unknown'}});
});

test('manual collection is sequential, excludes unconfigured panels and sends no task requests', async () => {
  let complete;
  http.post.mockReturnValueOnce(new Promise(resolve => {complete = resolve;}));
  await render();
  act(() => {button('采集当前范围').click();});
  expect(http.post.mock.calls).toEqual([['/api/baota/inventory/collect/', {panel_id: 1, kind: 'sites'}, {timeout: 60000}]]);
  await act(async () => {complete({...panel, status: 'ok'}); await flush();});
  expect(http.post.mock.calls).toEqual([
    ['/api/baota/inventory/collect/', {panel_id: 1, kind: 'sites'}, {timeout: 60000}],
    ['/api/baota/inventory/collect/', {panel_id: 2, kind: 'sites'}, {timeout: 60000}],
  ]);
});

test.each(['stop', 'hidden', 'unmount'])('%s cancels later collection and ignores the in-flight response', async action => {
  let complete;
  http.post.mockReturnValue(new Promise(resolve => {complete = resolve;}));
  await render();
  act(() => {button('采集当前范围').click();});
  const readsBefore = http.get.mock.calls.length;
  await act(async () => {
    if (action === 'stop') button('停止后续采集').click();
    if (action === 'hidden') {
      Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
      document.dispatchEvent(new Event('visibilitychange'));
    }
    if (action === 'unmount') ReactDOM.unmountComponentAtNode(container);
    await flush();
  });
  await act(async () => {complete({...panel, status: 'ok'}); await flush();});
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(http.get).toHaveBeenCalledTimes(readsBefore);
});

test('stopped collection cannot overlap its outstanding request with a new batch', async () => {
  let complete;
  http.post.mockReturnValue(new Promise(resolve => {complete = resolve;}));
  await render();
  act(() => {button('采集当前范围').click();});
  act(() => {button('停止后续采集').click();});
  expect(button('采集当前范围').disabled).toBe(true);
  expect(button('采集全部面板').disabled).toBe(true);
  await act(async () => {complete({...panel, status: 'ok'}); await flush();});
  expect(button('采集当前范围').disabled).toBe(false);
  expect(http.post).toHaveBeenCalledTimes(1);
});

test('backup age threshold defaults to 48 hours and changes are queried with evidence labels', async () => {
  data = {...data, items: [{id: '1:site:7', panel_id: 1, name: 'example.test', asset_type: 'site',
    backup_name: 'archive.tar.gz', state: 'panel_reported', location: 'cloud_record', local_exists: null,
    size_bytes: 2048, age_hours: 60, overdue: true, sampled_at: panel.sampled_at}], summary: {total: 1, overdue: 1}};
  await render('backups');
  expect(http.get).toHaveBeenLastCalledWith('/api/baota/inventory/backups/', {params: {page: 1, page_size: 20, max_age_hours: 48}});
  expect(container.textContent).toContain('云端记录');
  expect(container.textContent).toContain('可恢复');
  await change('逾期阈值', '168');
  await change('备份状态', 'overdue');
  expect(http.get).toHaveBeenLastCalledWith('/api/baota/inventory/backups/', {params: {page: 1, page_size: 20, max_age_hours: 168, state: 'overdue'}});
});

test('administrator permission gates all inventory reads', async () => {
  hasPermission.mockReturnValue(false);
  await render();
  expect(http.get).not.toHaveBeenCalled();
  expect(http.post).not.toHaveBeenCalled();
});

test('global busy response stops the batch and API failure keeps visible old cache', async () => {
  http.post.mockResolvedValue({status: 'busy'});
  await render();
  await act(async () => {button('采集当前范围').click(); await flush();});
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain('已有采集进行中');
  http.post.mockRejectedValue(new Error('internal details must not be rendered'));
  await act(async () => {button('采集当前范围').click(); await flush();});
  expect(container.textContent).toContain('保留上次成功缓存');
  expect(container.textContent).toContain('旧缓存');
  expect(container.textContent).not.toContain('internal details');
});

test('current range respects server filter and group descendants while ignoring resource keyword for targets', async () => {
  await render();
  await change('分组筛选', '1');
  await act(async () => {button('采集当前范围').click(); await flush();});
  expect(http.post.mock.calls.map(([, body]) => body.panel_id)).toEqual([1, 2]);
  http.post.mockClear();
  await change('服务器筛选', '2');
  await act(async () => {button('采集当前范围').click(); await flush();});
  expect(http.post.mock.calls.map(([, body]) => body.panel_id)).toEqual([2]);
});

test('unknown SSL and unconfigured API rows have no action link', async () => {
  data = {...data, items: [{...site, ssl_state: 'unknown'}, {...site, id: '3:7', panel_id: 3}],
    panels: [panel, {...panel, panel_id: 3, configured: false}]};
  await render();
  expect(container.querySelector('[data-row="1:7"] a')).toBeNull();
  expect(container.querySelector('[data-row="3:7"] a')).toBeNull();
});

test('permission revoked before clicking collection prevents a request from the old render', async () => {
  await render();
  hasPermission.mockReturnValue(false);
  await act(async () => {button('采集当前范围').click(); await flush();});
  expect(http.post).not.toHaveBeenCalled();
});

test('permission revoked during a request ignores its result and stops remaining panels', async () => {
  let complete;
  http.post.mockReturnValue(new Promise(resolve => {complete = resolve;}));
  await render();
  act(() => {button('采集当前范围').click();});
  const readsBefore = http.get.mock.calls.length;
  hasPermission.mockReturnValue(false);
  await act(async () => {complete({...panel, status: 'ok'}); await flush();});
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(http.get).toHaveBeenCalledTimes(readsBefore);
});

test('a backup record with no filename stays distinct from a resource without a backup record', async () => {
  data = {...data, items: [{id: '1:site:7', panel_id: 1, name: '有记录', asset_type: 'site', backup_id: 77, backup_name: '', state: 'unknown'},
    {id: '1:site:8', panel_id: 1, name: '无记录资源', asset_type: 'site', backup_id: null, backup_name: '', state: 'no_record'},
    {id: '1:site:9', panel_id: 1, name: '待核实资源', asset_type: 'site', backup_id: null, backup_name: '', state: 'unknown'}]};
  await render('backups');
  const present = container.querySelector('[data-row="1:site:7"]');
  expect(present.textContent).toContain('文件名未知');
  expect(present.textContent).not.toContain('无记录');
  expect(container.querySelector('[data-row="1:site:8"]').textContent).toContain('无记录');
  expect(container.querySelector('[data-row="1:site:9"]').textContent).not.toContain('无记录');
});
