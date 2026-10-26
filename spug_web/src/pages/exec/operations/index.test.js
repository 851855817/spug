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

test.each(['linux_inspection', 'php_environment', 'web_environment'])('compact results for %s show health, cause and actual data without baseline requests', async job => {
  http.get.mockResolvedValue({...run, job, state: 'completed', audit: [{event: 'created', actor_id: 1}], targets: [
    run.targets[0], run.targets[1], {id: 13, host: {name: '磁盘告警'}, state: 'success', checks: [
      {key: 'disk', label: '根分区使用率', status: 'warning', value: 99, summary: '使用率超过阈值'}]},
  ]});
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  expect(container.textContent).toContain('正常 1');
  expect(container.textContent).toContain('异常 2');
  expect(container.textContent).toContain('根分区使用率：使用率超过阈值');
  expect(container.textContent).toContain('SSH认证失败');
  expect(container.textContent).toContain('99');
  expect(container.textContent).toContain('Debian 12');
  expect(container.textContent).not.toContain('配置基线与差异');
  expect(container.textContent).not.toContain('作业记录');
  expect(http.get.mock.calls.map(([url]) => url)).toEqual(['/api/ops/runs/7/']);
  expect(http.post).not.toHaveBeenCalled();
  choose('结果状态', ['abnormal']);
  expect(container.textContent).not.toContain('Debian 12');
  expect(container.textContent).toContain('磁盘告警');
});

test.each([
  ['success', [{key: 'x', label: '数据', status: 'ok', value: 0}], '正常'],
  ['success', [{key: 'x', label: '数据', status: 'unknown', value: null}], '待核实'],
  ['success', [{key: 'x', label: '数据', status: 'unrecognized', value: 1}], '待核实'],
  ['success', [{key: 'x', label: '数据', status: 'ok', value: null}], '待核实'],
  ['success', [], '待核实'], ['unknown', [], '待核实'], ['failed', [], '异常'],
  ['pending', [], '待执行'], ['running', [], '采集中'], ['cancelled', [], '已取消'],
])('compact results never infer normal from missing or incomplete evidence (%s, %j)', async (state, checks, expected) => {
  http.get.mockResolvedValue({...run, state: 'completed', targets: [{id: 11, host: {name: '测试主机'}, state, checks}]});
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  const row = [...container.querySelectorAll('section')].find(item => item.textContent.startsWith('测试主机'));
  expect(row.children[1].textContent).toBe(expected);
});

test.each([
  ['php_environment', 'display_errors', true, '显示错误', 'display_errors=On', '非实际报错记录'],
  ['php_environment', 'expose_php', true, '公开 PHP 标识', 'expose_php=On', '公开 PHP 版本'],
  ['php_environment', 'allow_url_include', true, '允许远程包含', 'allow_url_include=On', '远程文件包含'],
  ['php_environment', 'extension_curl', false, 'cURL 扩展', 'cURL 扩展未加载', 'PHP CLI'],
  ['php_environment', 'extension_openssl', false, 'OpenSSL 扩展', 'OpenSSL 扩展未加载', 'PHP CLI'],
  ['php_environment', 'extension_mbstring', false, 'mbstring 扩展', 'mbstring 扩展未加载', 'PHP CLI'],
  ['linux_inspection', 'memory_available_percent', 7, '可用内存 %', '可用内存 7%', '低于 10%'],
  ['linux_inspection', 'root_used_percent', 94, '根分区使用 %', '根分区使用率 94%', '达到 90%'],
  ['web_environment', 'port_80_listening', false, 'TCP 80 监听', 'TCP 80 未监听', '监听配置'],
  ['web_environment', 'port_443_listening', false, 'TCP 443 监听', 'TCP 443 未监听', '监听配置'],
])('historical generic warnings explain the measured cause for %s/%s without collecting again', async (job, key, value, label, cause, detail) => {
  http.get.mockResolvedValue({...run, job, state: 'completed', targets: [{id: 11, host: {name: '历史主机'}, state: 'success',
    checks: [{key, label, status: 'warning', value, summary: '需要关注'}]}]});
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  const row = container.querySelector('section');
  expect(row.children[2].textContent).toContain(cause);
  expect(row.children[2].textContent).toContain(detail);
  expect(row.textContent).not.toContain('需要关注');
  expect(http.get.mock.calls.map(([url]) => url)).toEqual(['/api/ops/runs/7/']);
  expect(http.post).not.toHaveBeenCalled();
});

test('PHP switches have unambiguous names and values rather than implying an actual PHP error', async () => {
  http.get.mockResolvedValue({...run, job: 'php_environment', state: 'completed', targets: [{id: 11, state: 'success',
    checks: [{key: 'display_errors', label: '显示错误', status: 'warning', value: true, summary: '需要关注'}]}]});
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  expect(container.textContent).toContain('PHP 错误显示开关');
  expect(container.textContent).toContain('On（已开启）');
});

test.each([['unrecognized', '1'], ['php_environment', '2']])('unknown job/schema %s/%s does not invent a version-one diagnosis', async (job, version) => {
  http.get.mockResolvedValue({...run, job, version, state: 'completed', targets: [{id: 11, state: 'success',
    checks: [{key: 'display_errors', label: '未知配置', status: 'warning', value: false, summary: '需要关注'}]}]});
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  expect(container.textContent).not.toContain('display_errors=On');
  expect(container.textContent).toContain('未提供具体判定依据');
});

test.each([
  ['memory_available_percent', true], ['memory_available_percent', -1],
  ['memory_available_percent', 10], ['root_used_percent', 101], ['root_used_percent', 89.99],
])('inconsistent historical warning %s=%s does not invent a threshold diagnosis', async (key, value) => {
  http.get.mockResolvedValue({...run, job: 'linux_inspection', state: 'completed', targets: [{id: 11, state: 'success',
    checks: [{key, label: '资源指标', status: 'warning', value, summary: '需要关注'}]}]});
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  expect(container.textContent).toContain('未提供具体判定依据');
  expect(container.textContent).not.toMatch(/告警线|已监听|未监听/);
});

test('the exact ninety-percent disk boundary explains the warning and preserves specific evidence', async () => {
  http.get.mockResolvedValue({...run, job: 'linux_inspection', state: 'completed', targets: [{id: 11, state: 'success',
    checks: [{key: 'root_used_percent', label: '根分区使用 %', status: 'warning', value: 90, summary: '原始具体说明'}]}]});
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  expect(container.textContent).toContain('根分区使用率 90%（达到 90% 告警线）');
  expect(container.textContent).toContain('原始具体说明');
});

test('an unknown key in a known v1 job keeps its measured value without applying another rule', async () => {
  http.get.mockResolvedValue({...run, job: 'php_environment', state: 'completed', targets: [{id: 11, state: 'success',
    checks: [{key: 'custom_switch', label: '自定义配置', status: 'warning', value: true, summary: '需要关注'}]}]});
  await act(async () => {ReactDOM.render(<RunDetail runId={7}/>, container);});
  expect(container.textContent).toContain('当前值：是；本条记录未提供具体判定依据');
  expect(container.textContent).not.toMatch(/display_errors=On|已监听/);
});
