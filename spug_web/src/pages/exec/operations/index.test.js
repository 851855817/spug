import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { Modal } from 'antd';
import { http, hasPermission } from 'libs';
import Operations from './index';
import RunForm from './RunForm';
import RunDetail from './RunDetail';
import Records from './Records';

jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, hasPermission: jest.fn(), history: {
  get location() {return global.window.location;},
  replace: location => global.window.history.replaceState(global.window.history.state, '', `${location.pathname}${location.search}${location.hash}`),
}}));
jest.mock('components', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  return {Breadcrumb: Object.assign(Wrapper, {Item: Wrapper})};
});
jest.mock('components/ResizableTable', () => ({columns, dataSource = [], expandable}) => <div><div>{columns.map((col, i) => <span key={i}>{col.title}</span>)}</div>{dataSource.map((row, i) => <section key={row.id || i}>
  {columns.map((col, j) => <div key={j}>{col.render ? col.render(row[col.dataIndex], row) : row[col.dataIndex]}</div>)}
  {expandable?.expandedRowRender?.(row)}</section>)}</div>);
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children, label, title}) => <div>{title}{label}{children}</div>;
  const Select = ({value, onChange, mode, options = [], placeholder, disabled}) => <select aria-label={placeholder} disabled={disabled} multiple={mode === 'multiple'}
    value={value ?? (mode ? [] : '')} onChange={event => onChange(mode ? [...event.target.selectedOptions].map(option => options.find(item => String(item.value) === option.value).value) : options.find(item => String(item.value) === event.target.value)?.value)}>
    {!mode && <option value="">选择</option>}{options.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select>;
  return {Card: Wrapper, Space: Wrapper, Spin: Wrapper, Tag: Wrapper, Empty: Wrapper,
    Tabs: Object.assign(({children, activeKey, defaultActiveKey, onChange}) => <div data-active-tab={activeKey || defaultActiveKey}>
      {React.Children.map(children, child => <button onClick={() => onChange?.(child.key)}>{child.props.tab}</button>)}{children}</div>, {TabPane: Wrapper}), Typography: {Paragraph: Wrapper, Text: Wrapper},
    Form: Object.assign(Wrapper, {Item: Wrapper}), Select,
    Input: ({value, onChange, placeholder, disabled}) => <input aria-label={placeholder} value={value} onChange={onChange} disabled={disabled}/>,
    InputNumber: ({value, onChange, disabled, ...props}) => <input type="number" aria-label={props['aria-label']} value={value} disabled={disabled} onChange={event => onChange(Number(event.target.value))}/>,
    Alert: ({message, description}) => <div>{message}{description}</div>,
    Button: ({children, onClick, loading, disabled, 'aria-pressed': pressed}) => <button onClick={onClick} disabled={loading || disabled} aria-pressed={pressed}>{children}</button>,
    Modal: {confirm: jest.fn()}, message: {success: jest.fn(), error: jest.fn()}};
});

const catalog = {jobs: [{code: 'linux_inspection', version: '1', name: 'Linux基础巡检', description: '基础指标与版本', dependencies: ['SSH 与 sh'], limitations: ['不读取完整配置']},
  {code: 'php_environment', version: '1', name: '宝塔PHP配置检查', description: 'PHP安全项', dependencies: ['宝塔PHP'], limitations: ['只检查指定分支']}],
  hosts: [{id: 1, name: 'A', hostname: '192.0.2.1', group_ids: [2]}, {id: 2, name: 'B', hostname: '192.0.2.2', group_ids: [1]}],
  groups: [{id: 1, name: '父组', parent_id: null}, {id: 2, name: '子组', parent_id: 1}]};
const run = {id: 7, job: 'linux_inspection', job_name: 'Linux基础巡检', version: '1', state: 'paused', batch_size: 1, failure_threshold: 1,
  counts: {total: 2, success: 1, failed: 1, unknown: 0, pending: 0, running: 0, cancelled: 0}, targets: [
    {id: 11, host: {id: 1, name: 'A', hostname: '192.0.2.1'}, state: 'success', checks: [{key: 'os', label: '系统版本', status: 'ok', value: 'Debian 12', summary: '版本已采集'}]},
    {id: 12, host: {id: 2, name: 'B', hostname: '192.0.2.2'}, state: 'failed', category: 'ssh_auth', message: 'SSH认证失败', checks: []}]};
let container;
const button = text => [...container.querySelectorAll('button')].find(item => item.textContent === text);
const select = label => container.querySelector(`select[aria-label="${label}"]`);
function choose(label, values) {
  const element = select(label);
  act(() => {for (const option of element.options) option.selected = values.map(String).includes(option.value); Simulate.change(element);});
}
beforeEach(() => {
  window.history.replaceState({}, '', '/exec/operations');
  HTMLElement.prototype.scrollIntoView = jest.fn();
  container = document.createElement('div'); document.body.appendChild(container);
  hasPermission.mockReturnValue(true);
  http.post.mockResolvedValue({...run, state: 'queued'});
  http.get.mockImplementation(url => Promise.resolve(url.includes('/baselines/') ? {items: []} : url === '/api/ops/catalog/' ? catalog : url === '/api/ops/runs/' ? {items: [], total: 0} : run));
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.clearAllMocks(); jest.useRealTimers();});

test('operations is admin-only and denied visitors never fetch job or run data', async () => {
  hasPermission.mockReturnValue(false);
  await act(async () => {ReactDOM.render(<Operations/>, container);});
  expect(container.textContent).toContain('仅超级管理员');
  expect(http.get).not.toHaveBeenCalled();
});

test('catalog displays read-only descriptions, dependencies and limitations', async () => {
  http.get.mockImplementation(url => Promise.resolve(url === '/api/ops/catalog/' ? {...catalog, jobs: [{...catalog.jobs[0], limitations: ['不读取完整配置。', '仅检查根分区。']}]} : {items: [], total: 0}));
  await act(async () => {ReactDOM.render(<Operations/>, container);});
  expect(container.textContent).toContain('基础指标与版本');
  expect(container.textContent).toContain('SSH 与 sh');
  expect(container.textContent).toContain('不读取完整配置');
  expect(container.textContent).toContain('不读取完整配置；仅检查根分区');
});

test('creation waits for explicit confirmation and submits the frozen selectors, defaults and PHP branch', async () => {
  const onCreated = jest.fn();
  await act(async () => {ReactDOM.render(<RunForm catalog={catalog} onCreated={onCreated}/>, container);});
  choose('标准作业', ['php_environment']); choose('选择主机', [1]); choose('选择分组', [1]);
  expect(container.textContent).toContain('预估 2 台');
  await act(async () => {button('确认执行').click();});
  await act(async () => {button('确认执行').click();});
  expect(http.post).not.toHaveBeenCalled();
  expect(Modal.confirm).toHaveBeenCalledTimes(1);
  const confirmation = Modal.confirm.mock.calls[0][0];
  expect(confirmation.content).toContain('SSH'); expect(confirmation.content).toContain('只读'); expect(confirmation.content).toContain('资源');
  await act(async () => {await confirmation.onOk();});
  expect(http.post).toHaveBeenCalledWith('/api/ops/runs/', {job: 'php_environment', host_ids: [1], group_ids: [1], params: {php_version: '74'}, batch_size: 1, failure_threshold: 1, confirmed: true});
  expect(onCreated).toHaveBeenCalledWith(7);
});

test('empty targets disable creation and over 50 recursive distinct targets cannot be confirmed', async () => {
  const hosts = Array.from({length: 51}, (_, index) => ({id: index + 1, name: `host${index}`, group_ids: [2]}));
  await act(async () => {ReactDOM.render(<RunForm catalog={{...catalog, hosts}}/>, container);});
  expect(button('确认执行').disabled).toBe(true);
  choose('选择分组', [1]);
  expect(container.textContent).toContain('51 台');
  expect(button('确认执行').disabled).toBe(true);
  expect(Modal.confirm).not.toHaveBeenCalled();
});

test.each([['paused', {}], ['tripped', {acknowledge: true}]])('%s continues only after confirmation with the required acknowledgement', async (state, payload) => {
  http.get.mockResolvedValue({...run, state});
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  await act(async () => {button('继续下一批').click();});
  expect(http.post).not.toHaveBeenCalled();
  expect(Modal.confirm.mock.calls[0][0].content).toContain(state === 'tripped' ? '熔断' : '下一批');
  await act(async () => {await Modal.confirm.mock.calls[0][0].onOk();});
  expect(http.post).toHaveBeenCalledWith('/api/ops/runs/7/continue/', payload);
});

test('cancel explains that in-flight work can finish and only sends cancel after confirmation', async () => {
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  act(() => {button('取消未执行项').click();});
  expect(Modal.confirm.mock.calls[0][0].content).toContain('已下发');
  expect(http.post).not.toHaveBeenCalled();
  await act(async () => {await Modal.confirm.mock.calls[0][0].onOk();});
  expect(http.post).toHaveBeenCalledWith('/api/ops/runs/7/cancel/', {});
});

test('detail distinguishes failed collection from check warnings, filters failures and creates baselines only from success targets', async () => {
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  expect(container.textContent).toContain('SSH认证失败');
  expect(container.textContent).toContain('系统版本');
  const targetOptions = [...select('基线来源主机').options].map(option => option.value);
  expect(targetOptions).toContain('11'); expect(targetOptions).not.toContain('12');
  choose('结果状态', ['failed']);
  expect(container.textContent).not.toContain('Debian 12');
  choose('基线来源主机', [11]);
  act(() => {Simulate.change(container.querySelector('input[aria-label="基线名称"]'), {target: {value: '生产标准'}});});
  await act(async () => {button('保存不可变基线').click();});
  expect(http.post).toHaveBeenCalledWith('/api/ops/baselines/', {run_id: 7, target_id: 11, name: '生产标准'});
});

test('comparison permits only the same job version and retains missing versus unknown values', async () => {
  http.get.mockImplementation(url => Promise.resolve(url.includes('/baselines/') ? {items: [
    {id: 1, name: '同版本', job: 'linux_inspection', version: '1'}, {id: 2, name: '旧版本', job: 'linux_inspection', version: '0'}, {id: 3, name: '其他作业', job: 'php_environment', version: '1'}]} : url.includes('/diff/') ? {baseline: {name: '同版本'}, items: [{host: {id: 1, name: 'A'}, state: 'success', changes: [
      {key: 'os', label: '系统版本', before: 'Debian 11', after: 'Debian 12', state: 'different'},
      {key: 'php', label: 'PHP', before: '7.4', after: null, state: 'missing'}, {key: 'web', label: 'Web', before: 'Nginx', after: null, state: 'unknown'}]}]} : run));
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  expect(select('选择同版本基线').textContent).toContain('同版本');
  expect(select('选择同版本基线').textContent).not.toContain('旧版本');
  expect(select('选择同版本基线').textContent).not.toContain('其他作业');
  choose('选择同版本基线', [1]);
  await act(async () => {button('比较差异').click();});
  expect(http.get).toHaveBeenCalledWith('/api/ops/runs/7/diff/', {params: {baseline_id: 1}});
  expect(container.textContent).toContain('变化'); expect(container.textContent).toContain('缺失'); expect(container.textContent).toContain('未知');
});

test('PHP baselines also require the same branch and audit uses server event and actor fields', async () => {
  http.get.mockImplementation(url => Promise.resolve(url.includes('/baselines/') ? {items: [
    {id: 1, name: 'PHP74基线', job: 'php_environment', version: '1', params: {php_version: '74'}},
    {id: 2, name: 'PHP82基线', job: 'php_environment', version: '1', params: {php_version: '82'}}]} :
    {...run, job: 'php_environment', params: {php_version: '74'}, audit: [{event: 'baseline_created', actor_id: 2, created_at: '2026-10-24 10:00:00'}]}));
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  expect(select('选择同版本基线').textContent).toContain('PHP74基线');
  expect(select('选择同版本基线').textContent).not.toContain('PHP82基线');
  expect(container.textContent).toContain('创建基线');
  expect(container.textContent).toContain('用户 #2');
});

test('old baselines remain reachable through server pagination and compatible name search', async () => {
  http.get.mockImplementation((url, options) => Promise.resolve(url.includes('/baselines/') ? {items: [
    {id: options?.params?.page === 2 ? 1 : 201, name: options?.params?.page === 2 ? '旧标准' : '新标准', job: 'php_environment', version: '1', params: {php_version: '74'}}
  ], total: 201} : {...run, job: 'php_environment', params: {php_version: '74'}}));
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  expect(http.get).toHaveBeenCalledWith('/api/ops/baselines/', {params: {page: 1, page_size: 100, job: 'php_environment', version: '1', php_version: '74', q: ''}});
  await act(async () => {button('下一页基线').click();});
  choose('选择同版本基线', [1]);
  expect(select('选择同版本基线').textContent).toContain('旧标准');
  expect(button('比较差异').disabled).toBe(false);
  await act(async () => {
    const input = container.querySelector('input[aria-label="搜索基线名称"]');
    Simulate.change(input, {target: {value: '生产标准'}});
  });
  await act(async () => {button('搜索基线').click();});
  expect(http.get).toHaveBeenLastCalledWith('/api/ops/baselines/', {params: {page: 1, page_size: 100, job: 'php_environment', version: '1', php_version: '74', q: '生产标准'}});
  expect(button('比较差异').disabled).toBe(true);
});

test('polling stops when the current run pauses and refresh stays manual', async () => {
  jest.useFakeTimers();
  http.get.mockImplementation(url => Promise.resolve(url.includes('/baselines/') ? {items: []} : {...run, state: 'running'}));
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  http.get.mockImplementation(url => Promise.resolve(url.includes('/baselines/') ? {items: []} : run));
  await act(async () => {jest.advanceTimersByTime(5000);});
  const count = http.get.mock.calls.length;
  await act(async () => {jest.advanceTimersByTime(60000);});
  expect(http.get).toHaveBeenCalledTimes(count);
  await act(async () => {button('刷新详情').click();});
  expect(http.get).toHaveBeenCalledTimes(count + 1);
});

test('an in-flight poll cannot overwrite a confirmed action response', async () => {
  jest.useFakeTimers(); let resolvePoll;
  http.get.mockImplementation(url => url.includes('/baselines/') ? Promise.resolve({items: []}) : Promise.resolve({...run, state: 'running'}));
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  http.get.mockImplementation(() => new Promise(resolve => {resolvePoll = resolve;}));
  await act(async () => {jest.advanceTimersByTime(5000);});
  act(() => {button('取消未执行项').click();});
  http.post.mockResolvedValue({...run, state: 'cancelled'});
  await act(async () => {await Modal.confirm.mock.calls[0][0].onOk();});
  await act(async () => {resolvePoll({...run, state: 'running'});});
  expect(container.textContent).toContain('已取消');
  expect(button('取消未执行项')).toBeUndefined();
});

test('polling has a five minute bound across queued and running phases and restarts only after manual refresh', async () => {
  jest.useFakeTimers();
  http.get.mockImplementation(url => Promise.resolve(url.includes('/baselines/') ? {items: []} : {...run, state: 'queued'}));
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  for (let index = 0; index < 30; index += 1) await act(async () => {jest.advanceTimersByTime(5000);});
  http.get.mockImplementation(url => Promise.resolve(url.includes('/baselines/') ? {items: []} : {...run, state: 'running'}));
  for (let index = 0; index < 30; index += 1) await act(async () => {jest.advanceTimersByTime(5000);});
  const count = http.get.mock.calls.length;
  await act(async () => {jest.advanceTimersByTime(60000);});
  expect(http.get).toHaveBeenCalledTimes(count);
  expect(container.textContent).toContain('自动刷新已结束');
  await act(async () => {button('刷新详情').click();});
  await act(async () => {jest.advanceTimersByTime(5000);});
  expect(http.get).toHaveBeenCalledTimes(count + 2);
});

test('download is a current JSON snapshot and retains collection failure and stale marker after a failed refresh', async () => {
  const createUrl = jest.fn().mockReturnValue('blob:ops'); const revokeUrl = jest.fn();
  const originalCreate = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
  const originalRevoke = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
  Object.defineProperty(URL, 'createObjectURL', {configurable: true, writable: true, value: createUrl});
  Object.defineProperty(URL, 'revokeObjectURL', {configurable: true, writable: true, value: revokeUrl});
  const click = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  try {
    await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
    http.get.mockRejectedValueOnce(new Error('offline'));
    await act(async () => {button('刷新详情').click();});
    act(() => {button('下载 JSON 报告').click();});
    const blob = createUrl.mock.calls[0][0];
    const report = await new Promise(resolve => {const reader = new FileReader(); reader.onload = () => resolve(JSON.parse(reader.result)); reader.readAsText(blob);});
    expect(report.snapshot_stale).toBe(true);
    expect(report.run.targets[1]).toMatchObject({state: 'failed', category: 'ssh_auth'});
    expect(report.run.targets[0].checks[0].value).toBe('Debian 12');
    expect(revokeUrl).toHaveBeenCalledWith('blob:ops');
  } finally {
    click.mockRestore();
    if (originalCreate) Object.defineProperty(URL, 'createObjectURL', originalCreate); else delete URL.createObjectURL;
    if (originalRevoke) Object.defineProperty(URL, 'revokeObjectURL', originalRevoke); else delete URL.revokeObjectURL;
  }
});

test('records expose check counts and an explicit result action that locates the accessible result heading', async () => {
  window.history.replaceState({keep: true}, '', '/exec/operations?view=recent#results');
  http.get.mockImplementation(url => Promise.resolve(url === '/api/ops/catalog/' ? catalog : url === '/api/ops/runs/' ?
    {items: [{...run, counts: {...run.counts, warning_checks: 3, unknown_checks: 2}}], total: 1} : url.includes('/baselines/') ? {items: []} : run));
  await act(async () => {ReactDOM.render(<Operations/>, container);});
  expect(container.textContent).toContain('检查异常 3 项');
  expect(container.textContent).toContain('指标未知 2 项');
  await act(async () => {button('查看结果').click();});
  const heading = container.querySelector('h2');
  expect(heading.textContent).toContain('巡检结果 · 作业 #7');
  expect(document.activeElement).toBe(heading);
  expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  expect(container.querySelector('[data-active-tab]').getAttribute('data-active-tab')).toBe('records');
  expect(window.location.search).toBe('?view=recent&run_id=7');
  expect(window.location.hash).toBe('#results');
  expect(window.history.state).toEqual({keep: true});
  expect(http.post).not.toHaveBeenCalled();
});

test('older record payloads show unavailable check counts instead of zero', async () => {
  http.get.mockResolvedValue({items: [run], total: 1});
  await act(async () => {ReactDOM.render(<Records onOpen={jest.fn()}/>, container);});
  expect(container.textContent).toContain('指标统计待读取');
  expect(container.textContent).not.toContain('检查异常 0 项');
});

test.each(['7', '2147483647'])('reload restores bounded run_id %s and only reads the selected result', async id => {
  window.history.replaceState({}, '', `/exec/operations?run_id=${id}`);
  await act(async () => {ReactDOM.render(<Operations/>, container);});
  expect(http.get).toHaveBeenCalledWith(`/api/ops/runs/${id}/`);
  expect(container.querySelector('[data-active-tab]').getAttribute('data-active-tab')).toBe('records');
  expect(document.activeElement).toBe(container.querySelector('h2'));
  expect(http.post).not.toHaveBeenCalled();
  expect(Modal.confirm).not.toHaveBeenCalled();
});

test.each(['0', '-1', '1.5', '1e2', '01', '2147483648', '9007199254740992', '7/continue', '7&run_id=8'])('invalid run_id %s cannot request or execute a run', async id => {
  window.history.replaceState({}, '', `/exec/operations?run_id=${id}`);
  await act(async () => {ReactDOM.render(<Operations/>, container);});
  expect(http.get.mock.calls.some(([url]) => /^\/api\/ops\/runs\/[^/]+\/$/.test(url))).toBe(false);
  expect(http.post).not.toHaveBeenCalled();
  expect(container.querySelector('h2')).toBeNull();
});

test('confirmed creation switches to records and focuses its result without continuing work', async () => {
  await act(async () => {ReactDOM.render(<Operations/>, container);});
  choose('选择主机', [1]);
  act(() => {button('确认执行').click();});
  await act(async () => {await Modal.confirm.mock.calls[0][0].onOk();});
  expect(container.querySelector('[data-active-tab]').getAttribute('data-active-tab')).toBe('records');
  expect(document.activeElement).toBe(container.querySelector('h2'));
  expect(window.location.search).toBe('?run_id=7');
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(http.post.mock.calls[0][0]).toBe('/api/ops/runs/');
});

test('attention filtering includes successful hosts with abnormal or unknown checks and shows fixed failure advice', async () => {
  const attentionRun = {...run, targets: [run.targets[0], run.targets[1],
    {id: 13, host: {id: 3, name: '告警主机', hostname: '192.0.2.3'}, state: 'success', checks: [
      {key: 'disk', label: '根分区使用', status: 'warning', value: 99}, {key: 'kernel', label: '内核', status: 'unknown', value: null}]},
    {id: 14, host: {id: 4, name: '待核实主机'}, state: 'unknown', category: 'interrupted', checks: []},
    {id: 15, host: {id: 5, name: '尚未执行主机'}, state: 'pending', checks: []},
  ]};
  http.get.mockImplementation(url => Promise.resolve(url.includes('/baselines/') ? {items: []} : attentionRun));
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  expect(container.textContent).toContain('采集成功仅表示取得结果');
  expect(container.textContent).toContain('需处理 3 台');
  expect(container.textContent).toContain('指标异常 1 项');
  expect(container.textContent).toContain('指标未知 1 项');
  expect(container.textContent).toContain('异常 1 项（根分区使用）');
  expect(container.textContent).toContain('未知 1 项（内核）');
  expect(container.textContent).toContain('核对 SSH 用户、密钥及登录权限');
  expect(container.textContent).toContain('核对 Worker 状态');
  act(() => {button('只看需处理').click();});
  expect(button('只看需处理').getAttribute('aria-pressed')).toBe('true');
  expect(container.textContent).toContain('告警主机');
  expect(container.textContent).toContain('待核实主机');
  expect(container.textContent).not.toContain('Debian 12');
  expect(container.textContent).not.toContain('尚未执行主机');
  act(() => {button('只看需处理').click();});
  expect(container.textContent).toContain('Debian 12');
  expect(http.post).not.toHaveBeenCalled();
});

test('switching selected results prevents an older response from filling the new result section', async () => {
  let resolveOld;
  const otherRun = {...run, id: 8, job_name: '新选择作业', targets: []};
  http.get.mockImplementation(url => url === '/api/ops/runs/7/' ? new Promise(resolve => {resolveOld = resolve;}) : Promise.resolve(
    url === '/api/ops/catalog/' ? catalog : url === '/api/ops/runs/' ? {items: [run, otherRun], total: 2} : url.includes('/baselines/') ? {items: []} : otherRun));
  await act(async () => {ReactDOM.render(<Operations/>, container);});
  await act(async () => {[...container.querySelectorAll('button')].filter(item => item.textContent === '查看结果')[0].click();});
  await act(async () => {[...container.querySelectorAll('button')].filter(item => item.textContent === '查看结果')[1].click();});
  await act(async () => {resolveOld({...run, job_name: '旧响应作业'});});
  expect(container.querySelector('h2').textContent).toContain('#8');
  expect(container.textContent).toContain('作业 #8 · 新选择作业');
  expect(container.textContent).not.toContain('旧响应作业');
  expect(window.location.search).toBe('?run_id=8');
  expect(http.post).not.toHaveBeenCalled();
});
