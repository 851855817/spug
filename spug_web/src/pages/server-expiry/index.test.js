import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { hasPermission, http } from 'libs';
import { Modal } from 'antd';

let Expiry;
try {Expiry = require('./index').default;} catch (_) { /* Red phase: feature has not been created. */ }
jest.mock('libs', () => ({hasPermission: jest.fn(), http: {get: jest.fn(), post: jest.fn()}}));
jest.mock('components', () => {
  const Wrapper = ({children}) => <div>{children}</div>; Wrapper.Item = Wrapper;
  return {AuthDiv: Wrapper, Breadcrumb: Wrapper};
});
jest.mock('../../components/ResizableTable', () => ({columns, dataSource = [], pagination}) => <div>
  {dataSource.map(row => <div key={row.key || row.id} data-row={row.key || row.id}>
    {columns.map(column => <div key={column.key || column.dataIndex}>{column.render ? column.render(row[column.dataIndex], row) : row[column.dataIndex]}</div>)}
  </div>)}
  {pagination && <button disabled={pagination.current * pagination.pageSize >= pagination.total}
    onClick={() => pagination.onChange(pagination.current + 1, pagination.pageSize)}>下一页</button>}
</div>);
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children, title, extra}) => <div>{title}{extra}{children}</div>;
  const Input = props => <input {...props}/>; Input.TextArea = props => <textarea {...props}/>;
  Input.Search = ({onSearch, allowClear, ...props}) => <input {...props} onKeyDown={event => {if (event.key === 'Enter') onSearch(event.target.value);}}/>;
  const Select = ({value, onChange, options = [], ...props}) => <select aria-label={props['aria-label']} value={value == null ? '' : value}
    disabled={props.disabled} onChange={event => onChange(event.target.value)}><option value="">全部</option>
    {options.map(option => <option key={option.value} value={option.value} disabled={option.disabled}>{option.label}</option>)}</select>;
  const Dialog = ({open, children, title, footer, onCancel}) => open ? <section role="dialog">{title}{children}{footer}
    <button onClick={onCancel}>关闭弹窗</button></section> : null;
  Dialog.confirm = jest.fn();
  const Tabs = ({activeKey, onChange, children}) => <div>{React.Children.map(children, child =>
    <button onClick={() => onChange(child.key)}>{child.props.tab}</button>)}
    {React.Children.toArray(children).find(child => child.key.replace(/^\.\$/, '') === activeKey)?.props.children}</div>;
  Tabs.TabPane = Wrapper;
  return {Alert: ({message, description}) => <div role="alert">{message}{description}</div>,
    Card: Wrapper, Space: Wrapper, Tag: Wrapper, Typography: {Text: Wrapper, Paragraph: Wrapper}, Input, Select, Modal: Dialog, Tabs,
    Form: {Item: ({label, children}) => <label>{label}{children}</label>},
    Switch: ({checked, onChange, disabled, ...props}) => <input type="checkbox" aria-label={props['aria-label']}
      checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)}/>,
    Button: ({children, onClick, disabled, loading}) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>,
    message: {success: jest.fn()}};
});

const asset = {key: 'host:1', name: '客户主机', address: '192.0.2.1',
  sources: [{kind: 'host', id: 1, name: '客户主机'}, {kind: 'panel', id: 4, name: '宝塔A'}],
  groups: [{key: 'host:2', name: '生产'}], expires_on: '2030-12-01', original_expiry: '2030-11-20 00:00:00',
  manual_expiry: '2030-12-01', date_source: 'manual', days_remaining: 5, state: 'within_7',
  owner: {id: 2, name: '负责人', available: true}, followup: 'pending', reminder_enabled: false, exempt: false, note: '待续费', version: 'v1'};
let container, data;
const flush = async () => {for (let i = 0; i < 12; i++) await Promise.resolve();};
const button = text => [...container.querySelectorAll('button')].find(node => node.textContent === text);
const render = async () => {expect(typeof Expiry).toBe('function'); await act(async () => {ReactDOM.render(<Expiry/>, container); await flush();});};
const click = async text => {await act(async () => {button(text).click(); await flush();});};
const change = async (label, value) => {await act(async () => {
  const node = container.querySelector(`[aria-label="${label}"]`);
  Simulate.change(node, {target: {value, checked: value}}); await flush();
});};
const options = {owners: [{id: 2, name: '负责人'}], groups: [{key: 'host:2', name: '生产'}], timezone: 'Asia/Shanghai', schedule: '09:00'};
beforeEach(() => {
  container = document.createElement('div'); document.body.appendChild(container);
  hasPermission.mockReturnValue(true);
  data = {items: [asset], total: 22, page: 1, page_size: 20, unread_count: 1, timezone: 'Asia/Shanghai',
    summary: {total: 1, expired: 0, within_7: 1, within_30: 1, unset: 0},
    check: {status: 'never', last_success_at: null}};
  http.get.mockImplementation(path => Promise.resolve(path.endsWith('options/') ? options : path.endsWith('reminders/') ?
    {items: [{id: 8, asset_key: 'host:1', name: '客户主机', address: '192.0.2.1', expires_on: '2030-12-01', stage: '7d', unread: true, current: true}], total: 1, unread_count: 1} :
    path.endsWith('history/') ? {items: [{id: 1, event: 'renew', actor_name: '管理员', created_at: '2030-10-25T09:00:00', detail: {before: {manual_expiry: '2030-11-01'}, after: {manual_expiry: '2030-12-01'}}}], total: 1} : data));
  http.post.mockResolvedValue({...asset, version: 'v2'});
});
afterEach(async () => {act(() => {ReactDOM.unmountComponentAtNode(container);}); await flush(); container.remove(); jest.clearAllMocks();});

test('opens with local reads only and shows merged sources, original date and disabled reminder', async () => {
  await render();
  expect(http.post).not.toHaveBeenCalled();
  expect(http.get).toHaveBeenCalledWith('/api/ops/expiry/assets/', {params: {page: 1, page_size: 20}});
  const row = container.querySelector('[data-row="host:1"]');
  expect(row.textContent).toContain('宝塔A'); expect(row.textContent).toContain('人工登记');
  expect(row.textContent).toContain('2030-11-20'); expect(row.textContent).toContain('未开启');
  expect(container.textContent).toContain('09:00'); expect(container.textContent).toContain('站内提醒');
});
test('denies reads and actions to ordinary accounts', async () => {
  hasPermission.mockReturnValue(false); await render();
  expect(http.get).not.toHaveBeenCalled(); expect(http.post).not.toHaveBeenCalled();
  expect(container.textContent).toContain('仅超级管理员');
});
test('server filtering and pagination ignore obsolete responses', async () => {
  await render(); let finish;
  http.get.mockImplementation((path, config) => config?.params?.state === 'expired' ? new Promise(resolve => {finish = resolve;}) :
    Promise.resolve({...data, items: [{...asset, name: '最新结果'}]}));
  await change('到期状态', 'expired'); await change('到期状态', 'invalid');
  await act(async () => {finish({...data, items: [{...asset, name: '过时结果'}]}); await flush();});
  expect(container.textContent).toContain('最新结果'); expect(container.textContent).not.toContain('过时结果');
  await click('下一页');
  expect(http.get).toHaveBeenLastCalledWith('/api/ops/expiry/assets/', {params: {page: 2, page_size: 20, state: 'invalid'}});
});
test('editing can restore the original date and sends only metadata with its version', async () => {
  await render(); await click('编辑'); await change('日期来源', 'original'); await click('保存');
  expect(http.post).toHaveBeenCalledWith('/api/ops/expiry/assets/host:1/', {version: 'v1', manual_expiry: null,
    owner_id: 2, followup: 'pending', reminder_enabled: false, exempt: false, note: '待续费'});
});
test('saving a manual source without an optional raw override preserves its effective date', async () => {
  const row = {...asset}; delete row.manual_expiry; data.items = [row];
  await render(); await click('编辑'); await click('保存');
  expect(http.post.mock.calls[0][1].manual_expiry).toBe('2030-12-01');
});
test('enabling reminder waits for explicit confirmation before saving', async () => {
  await render(); await click('编辑'); await change('开启站内提醒', true); await click('保存');
  expect(Modal.confirm).toHaveBeenCalledTimes(1); expect(http.post).not.toHaveBeenCalled();
  expect(Modal.confirm.mock.calls[0][0].content).toContain('09:00');
  await act(async () => {await Modal.confirm.mock.calls[0][0].onOk(); await flush();});
  expect(http.post.mock.calls[0][1].reminder_enabled).toBe(true);
});

test('cancelling reminder confirmation preserves the editor and permits one later confirmation', async () => {
  Modal.confirm.mockReturnValueOnce({destroy: jest.fn()}).mockReturnValueOnce({destroy: jest.fn()});
  await render(); await click('编辑'); await change('开启站内提醒', true); await click('保存');
  await click('保存');
  expect(Modal.confirm).toHaveBeenCalledTimes(1);
  expect(http.post).not.toHaveBeenCalled();
  act(() => {Modal.confirm.mock.calls[0][0].onCancel();});
  expect(container.querySelector('[role="dialog"]')).not.toBeNull();
  expect(container.querySelector('[aria-label="开启站内提醒"]').checked).toBe(true);
  expect(http.post).not.toHaveBeenCalled();
  await click('保存');
  expect(Modal.confirm).toHaveBeenCalledTimes(2);
  await act(async () => {await Modal.confirm.mock.calls[1][0].onOk(); await flush();});
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(http.post.mock.calls[0][1].reminder_enabled).toBe(true);
});
test('invalid manual dates and nonadvancing renewal dates never submit', async () => {
  await render(); await click('编辑'); await change('人工到期日', ''); await click('保存');
  expect(http.post).not.toHaveBeenCalled(); expect(container.textContent).toContain('填写有效');
  await click('关闭弹窗'); await click('登记续费'); await change('续费后到期日', '2030-11-01'); await click('确认登记');
  expect(http.post).not.toHaveBeenCalled(); expect(container.textContent).toContain('晚于');
});
test('renewal records a new date without claiming the cloud provider has renewed', async () => {
  await render(); await click('登记续费'); await change('续费后到期日', '2099-12-01'); await change('续费备注', '人工核对');
  expect(container.textContent).toContain('云厂商'); await click('确认登记');
  expect(http.post).toHaveBeenCalledWith('/api/ops/expiry/assets/host:1/renew/', {version: 'v1', expires_on: '2099-12-01', note: '人工核对'});
});
test('failed or conflicting save keeps the editor and prevents duplicate in-flight writes', async () => {
  await render(); await click('编辑'); let reject;
  http.post.mockReturnValue(new Promise((_, failure) => {reject = failure;}));
  await click('保存'); await click('保存'); expect(http.post).toHaveBeenCalledTimes(1);
  await act(async () => {reject('信息已变化，请刷新'); await flush();});
  expect(container.querySelector('[role="dialog"]')).not.toBeNull(); expect(container.textContent).toContain('信息已变化');
});
test('history is explicit local read and uses field labels instead of raw JSON', async () => {
  await render(); await click('记录');
  expect(http.get).toHaveBeenCalledWith('/api/ops/expiry/assets/host:1/history/', {params: {page: 1, page_size: 20}});
  expect(container.textContent).toContain('管理员'); expect(container.textContent).toContain('2030-12-01');
  expect(container.textContent).not.toContain('"manual_expiry"'); expect(http.post).not.toHaveBeenCalled();
});
test('personal reminder read is explicit and is separate from the global notification API', async () => {
  await render(); await click('站内提醒 (1)');
  expect(http.get).toHaveBeenCalledWith('/api/ops/expiry/reminders/', {params: {page: 1, page_size: 20}});
  expect(http.post).not.toHaveBeenCalled(); await click('标记已读');
  expect(http.post).toHaveBeenCalledWith('/api/ops/expiry/reminders/read/', {ids: [8]});
  expect(http.get.mock.calls.some(([path]) => path.includes('/notify/'))).toBe(false);
});
test('disabled owner and unknown expiry remain visible and are never labelled healthy', async () => {
  data.items = [{...asset, owner: {id: 2, name: '旧负责人', available: false}, state: 'invalid', expires_on: null, days_remaining: null}];
  await render(); expect(container.textContent).toContain('日期无效'); expect(container.textContent).toContain('已停用');
});
test('asset page returns to a valid page when a result count shrinks', async () => {
  await render();
  http.get.mockImplementation(path => Promise.resolve(path.endsWith('options/') ? options : {...data, total: 1}));
  await click('下一页');
  expect(http.get).toHaveBeenLastCalledWith('/api/ops/expiry/assets/', {params: {page: 1, page_size: 20}});
  expect(container.textContent).toContain('客户主机');
});
test('unread reminder page and badge recover after its last item is read', async () => {
  let total = 21, unread = 21;
  http.get.mockImplementation((path, config) => Promise.resolve(path.endsWith('options/') ? options : path.endsWith('reminders/') ?
    {items: total === 20 && config.params.page === 2 ? [] : [{id: 8, name: '提醒主机', stage: '7d', unread: true, current: true}], total, unread_count: unread} : data));
  await render(); await click('站内提醒 (1)');
  expect(button('站内提醒 (21)')).toBeTruthy();
  await change('提醒已读状态', '1'); await click('下一页');
  total = 20; unread = 20;
  await click('标记已读');
  expect(http.get.mock.calls.filter(([path]) => path.endsWith('reminders/')).pop()[1].params.page).toBe(1);
  expect(container.textContent).toContain('提醒主机');
});
