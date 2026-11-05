import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { hasPermission, http } from 'libs';
import SSLMonitor from './index';
import Renewal from './Renewal';
import SourceSettings from './SourceSettings';

jest.mock('libs', () => ({hasPermission: jest.fn(), http: {get: jest.fn(), post: jest.fn()}}));
jest.mock('components', () => ({Breadcrumb: Object.assign(({children}) => <div>{children}</div>, {Item: ({children}) => <span>{children}</span>})}));
jest.mock('../schedule/PanelPreview', () => ({taskId}) => <div>宝塔只读逐项结果 #{taskId}</div>);
jest.mock('../../components/ResizableTable', () => ({columns, dataSource, pagination}) => <div>
  <table><tbody>{dataSource.map(row => <tr key={row.id || row.key}>{columns.map((col, index) => <td key={index}>{col.render ? col.render(row[col.dataIndex], row) : row[col.dataIndex]}</td>)}</tr>)}</tbody></table>
  {pagination && <button onClick={() => pagination.onChange(2, 20)}>下一页</button>}</div>);
jest.mock('antd', () => {
  const Wrapper = ({children, title}) => <div>{title}{children}</div>;
  const Button = ({children, onClick, disabled, loading, ...props}) => <button aria-label={props['aria-label']} disabled={disabled || loading} onClick={onClick}>{children}</button>;
  const Input = ({onChange, value, ...props}) => <input aria-label={props['aria-label']} value={value || ''} onChange={onChange}/>;
  Input.Search = ({onSearch, defaultValue, ...props}) => <input aria-label={props['aria-label']} defaultValue={defaultValue}
    onKeyDown={event => {if (event.key === 'Enter') onSearch(event.target.value);}}/>;
  return {Button, Input, Space: Wrapper, Tag: Wrapper, Spin: Wrapper,
    Modal: ({visible, title, children, footer, onCancel}) => visible ? <div role="dialog">{title}{children}{footer}<button onClick={onCancel}>关闭</button></div> : null,
    Alert: ({message, description}) => <div role="alert">{message}{description}</div>,
    Select: ({value, onChange, options, ...props}) => <select aria-label={props['aria-label']} value={value || ''} onChange={event => onChange(event.target.value || undefined)}>
      <option value="">全部</option>{(options || []).map(option => <option value={option.value} key={option.value}>{option.label}</option>)}</select>,
    Checkbox: ({children, checked, onChange, disabled, ...props}) => <label><input type="checkbox" aria-label={props['aria-label']} checked={!!checked} onChange={onChange} disabled={disabled}/>{children}</label>,
    Switch: ({checked, onChange, disabled, ...props}) => <input type="checkbox" aria-label={props['aria-label']} checked={!!checked} onChange={event => onChange(event.target.checked)} disabled={disabled}/>,
    Empty: ({description}) => <div>{description}</div>};
});

const row = {id: 1, domain: 'app.example.test', source_key: 'host:1', source_name: '生产服务器', state: 'expiring',
  not_before: '2026-08-01T12:00:00Z', expires_at: '2026-11-05T12:00:00Z', days_remaining: 1,
  sampled_at: '2026-11-04T12:00:00Z', stale: false, renewal: {engine: 'certbot', supported: true},
  local_certificate: {fingerprint: 'LOCAL', cert_path: '/etc/letsencrypt/live/app/fullchain.pem'}, public_tls: {state: 'normal', certificate: {fingerprint: 'CDN'}}};
const source = {key: 'host:1', name: '生产服务器', kind: 'host', address: '192.0.2.10', enabled: true, groups: []};
const prepared = {id: 8, state: 'prepared', kind: 'renew', revision: 'review-1', phase: 'awaiting_confirmation',
  summary: {source: '生产服务器', domains: ['app.example.test'], cert_path: '/etc/letsencrypt/live/app/fullchain.pem', engine: 'certbot', validation: 'http-01', reload: 'nginx reload', impacts: ['重新加载 Nginx']},
  acknowledgements: [{code: 'ssh_renew', label: '确认只续签当前订单'}, {code: 'service_reload', label: '确认重新加载 Nginx'}]};
const flush = async () => {for (let i = 0; i < 30; i++) await Promise.resolve();};
let container;
const render = async element => act(async () => {ReactDOM.render(element, container); await flush();});
const click = async text => act(async () => {const button = [...container.querySelectorAll('button')].find(item => item.textContent === text); expect(button).toBeDefined(); button.click(); await flush();});
beforeEach(() => {
  jest.useFakeTimers(); hasPermission.mockReturnValue(true);
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
  container = document.createElement('div'); document.body.appendChild(container);
  http.get.mockImplementation(path => Promise.resolve(path.endsWith('sources/') ? {items: [source]} : path.includes('reminders/')
    ? {items: [], total: 0, unread_count: 0} : path.includes('/jobs/') ? prepared
      : {items: [row], total: 51, stats: {total: 51, normal: 40, expiring: 7, expired: 2, error: 2}, sources: [source], active_jobs: []}));
  http.post.mockResolvedValue({id: 8, state: 'queued', kind: 'renew'});
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.useRealTimers(); jest.resetAllMocks();});

test('local list, auto refresh and statistic filters perform GET only; unknown caches stay visibly stale', async () => {
  await render(<SSLMonitor/>);
  expect(container.textContent).toContain('SSL证书巡检'); expect(container.textContent).toContain('app.example.test');
  expect(container.textContent).toContain('2026-11-05 20:00:00'); expect(http.post).not.toHaveBeenCalled();
  const stat = container.querySelector('[data-stat="expiring"]');
  await act(async () => {stat.click(); await flush();});
  expect(http.get).toHaveBeenLastCalledWith('/api/monitor/ssl/', expect.objectContaining({params: expect.objectContaining({state: 'expiring', page: 1})}));
  await act(async () => {jest.advanceTimersByTime(30000); await flush();});
  expect(http.post).not.toHaveBeenCalled();
  await click('刷新缓存'); expect(http.post).not.toHaveBeenCalled();
});

test('ordinary accounts never request certificates, settings, jobs or reminders', async () => {
  hasPermission.mockReturnValue(false); await render(<SSLMonitor/>);
  expect(http.get).not.toHaveBeenCalled(); expect(http.post).not.toHaveBeenCalled();
});

test('active local jobs refresh at 2.5 seconds without sending remote checks', async () => {
  const original = http.get.getMockImplementation();
  http.get.mockImplementation(path => path === '/api/monitor/ssl/' ? Promise.resolve({items: [row], total: 1, stats: {}, sources: [source],
    active_jobs: [{id: 9, state: 'running', phase: 'check'}]}) : original(path));
  await render(<SSLMonitor/>);
  const reads = () => http.get.mock.calls.filter(([path]) => path === '/api/monitor/ssl/').length;
  expect(reads()).toBe(1);
  await act(async () => {jest.advanceTimersByTime(2500); await flush();}); expect(reads()).toBe(2);
  expect(http.post).not.toHaveBeenCalled();
});

test('manual checking opens a local progress job and posts only enabled selected sources', async () => {
  http.post.mockResolvedValue({jobs: [{id: 13, kind: 'check', state: 'queued'}]});
  const original = http.get.getMockImplementation();
  http.get.mockImplementation(path => path.includes('/jobs/13/') ? Promise.resolve({id: 13, kind: 'check', state: 'running', phase: 'check', message: '正在检查来源'}) : original(path));
  await render(<SSLMonitor/>); await click('立即巡检');
  expect(http.post).toHaveBeenCalledWith('/api/monitor/ssl/checks/', {source_keys: ['host:1']});
  expect(container.textContent).toContain('巡检任务结果'); expect(container.textContent).toContain('正在检查来源');
});

test('fractional server days are displayed clearly and stale cached certificates offer no renew action', async () => {
  const original = http.get.getMockImplementation();
  http.get.mockImplementation(path => path === '/api/monitor/ssl/' ? Promise.resolve({items: [{...row, stale: true, days_remaining: 2.345678}],
    total: 1, stats: {normal: 0, error: 1}, sources: [source], active_jobs: []}) : original(path));
  await render(<SSLMonitor/>); expect(container.textContent).toContain('旧缓存'); expect(container.textContent).not.toContain('2.345678');
  expect([...container.querySelectorAll('button')].some(button => button.textContent === '续签')).toBe(false);
});

test('renewal result explicitly reports local deployment, reload and public TLS separately', async () => {
  http.get.mockResolvedValue({...prepared, state: 'failed', result: {local_updated: true, reload_verified: true, public_updated: false}});
  await render(<Renewal jobId={8} onClose={() => {}}/>);
  expect(container.textContent).toContain('本地部署：已更新'); expect(container.textContent).toContain('服务重载：已核实');
  expect(container.textContent).toContain('公网证书：尚未生效'); expect(container.textContent).not.toContain('确认并开始续签');
});

test('more than ten enabled sources require a narrower selection before a remote check', async () => {
  const original = http.get.getMockImplementation();
  http.get.mockImplementation(path => path === '/api/monitor/ssl/' ? Promise.resolve({items: [], total: 0, stats: {}, active_jobs: [],
    sources: Array.from({length: 11}, (_, index) => ({...source, key: `host:${index + 1}`}))}) : original(path));
  await render(<SSLMonitor/>);
  const button = [...container.querySelectorAll('button')].find(item => item.textContent === '立即巡检'); expect(button.disabled).toBe(true);
  expect(container.textContent).toContain('每次最多检查 10 个来源'); expect(http.post).not.toHaveBeenCalled();
});

test('Baota renewal displays original per-target read-only results instead of an ignored task link', async () => {
  http.get.mockResolvedValue({...prepared, panel_task_id: 33}); await render(<Renewal jobId={8} onClose={() => {}}/>);
  expect(container.textContent).toContain('宝塔只读逐项结果 #33');
  expect(http.post).not.toHaveBeenCalled();
});

test('renew action creates only one preflight on double click and never starts on GET', async () => {
  let finish;
  http.post.mockImplementation(() => new Promise(resolve => {finish = resolve;}));
  await render(<SSLMonitor/>);
  await act(async () => {const button = [...container.querySelectorAll('button')].find(item => item.textContent === '续签'); button.click(); button.click(); await flush();});
  expect(http.post).toHaveBeenCalledTimes(1); expect(http.post).toHaveBeenCalledWith('/api/monitor/ssl/renew/', {certificate_id: 1});
  await act(async () => {finish({id: 8, state: 'queued'}); await flush();});
  expect(http.get).toHaveBeenCalledWith('/api/monitor/ssl/jobs/8/');
  expect(container.textContent).toContain('确认只续签当前订单');
  expect(http.post).toHaveBeenCalledTimes(1);
});

test('frozen plan requires every acknowledgement and starts once with only codes and revision', async () => {
  await render(<Renewal jobId={8} onClose={() => {}}/>);
  expect(container.textContent).toContain('http-01'); expect(container.textContent).toContain('/etc/letsencrypt/live/app/fullchain.pem');
  const start = [...container.querySelectorAll('button')].find(item => item.textContent === '确认并开始续签'); expect(start.disabled).toBe(true);
  const boxes = container.querySelectorAll('input[type="checkbox"]');
  await act(async () => {Simulate.change(boxes[0], {target: {checked: true}}); await flush();}); expect(start.disabled).toBe(true);
  await act(async () => {Simulate.change(boxes[1], {target: {checked: true}}); await flush();}); expect(start.disabled).toBe(false);
  await act(async () => {start.click(); start.click(); await flush();});
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(http.post).toHaveBeenCalledWith('/api/monitor/ssl/renew/8/start/', {revision: 'review-1', acknowledgements: ['ssh_renew', 'service_reload']});
});

test('uncertain results offer read-back verification and never a start or retry action', async () => {
  http.get.mockResolvedValue({...prepared, state: 'uncertain', message: 'SSH 连接中断，执行结果待核实'});
  await render(<Renewal jobId={8} onClose={() => {}}/>);
  expect(container.textContent).toContain('待核实'); expect(container.textContent).not.toContain('确认并开始续签');
  expect(container.textContent).not.toContain('放弃预检');
  await act(async () => {jest.advanceTimersByTime(10000); await flush();}); expect(http.get).toHaveBeenCalledTimes(1); expect(http.post).not.toHaveBeenCalled();
  await click('只读核验结果'); expect(http.post).toHaveBeenCalledWith('/api/monitor/ssl/renew/8/verify/', {});
});

test('abandoning a readonly prepared renewal cancels admission without sending renewal', async () => {
  http.get.mockResolvedValue(prepared);
  const onClose = jest.fn(), onUpdate = jest.fn();
  await render(<Renewal jobId={8} onClose={onClose} onUpdate={onUpdate}/>);
  await click('放弃预检');
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(http.post).toHaveBeenCalledWith('/api/monitor/ssl/renew/8/cancel/', {});
  expect(onClose).toHaveBeenCalled();
});

test('queued read-back verification keeps polling local results until it finishes', async () => {
  http.get.mockResolvedValueOnce({...prepared, state: 'uncertain'})
    .mockResolvedValueOnce({...prepared, state: 'queued', phase: 'verify'})
    .mockResolvedValueOnce({...prepared, state: 'success', message: '已核实本地与公网证书更新'});
  await render(<Renewal jobId={8} onClose={() => {}}/>); await click('只读核验结果');
  await act(async () => {jest.advanceTimersByTime(2500); await flush();});
  expect(container.textContent).toContain('已核实本地与公网证书更新');
  expect(http.post).toHaveBeenCalledTimes(1); expect(http.post).toHaveBeenCalledWith('/api/monitor/ssl/renew/8/verify/', {});
});

test('source settings save only changed sources without invalidating unrelated preflights', async () => {
  http.get.mockResolvedValue({items: [{key: 'host:2', kind: 'host', name: 'SSH 主机'}, {key: 'panel:3', kind: 'panel', name: '宝塔面板'}]});
  await render(<SourceSettings onClose={() => {}} onSaved={() => {}}/>);
  expect(http.post).not.toHaveBeenCalled(); expect(container.textContent).toContain('每 6 小时');
  expect([...container.querySelectorAll('input[type="checkbox"]')].every(input => !input.checked)).toBe(true);
  await act(async () => {Simulate.change(container.querySelector('input[aria-label="启用 SSH 主机"]'), {target: {checked: true}}); await flush();});
  await click('保存配置');
  expect(http.post.mock.calls.map(([, body]) => body)).toEqual([
    {key: 'host:2', revision: '', enabled: true, reminders: false, interval_hours: 6}]);
});

test('certificate details distinguish local deployment from CDN public TLS', async () => {
  await render(<SSLMonitor/>); await click('详情');
  expect(container.textContent).toContain('源站本地证书'); expect(container.textContent).toContain('公网 SNI TLS');
  expect(container.textContent).toContain('CDN'); expect(container.textContent).toContain('LOCAL');
});

test('revoked source authorization requires an explicit reconfirmation without changing healthy sources', async () => {
  http.get.mockResolvedValue({items: [{...source, status: 'disabled', revision: 'old-owner'},
    {...source, key: 'host:2', name: '健康来源', status: 'ok', revision: 'healthy'}]});
  await render(<SourceSettings onClose={() => {}} onSaved={() => {}}/>);
  expect(container.textContent).toContain('重新授权');
  expect(http.post).not.toHaveBeenCalled();
  await click('重新授权');
  expect(http.post).not.toHaveBeenCalled();
  await click('保存配置');
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(http.post).toHaveBeenCalledWith('/api/monitor/ssl/sources/', {
    key: 'host:1', revision: 'old-owner', enabled: true, reminders: false, interval_hours: 6});
});

test('Baota configuration guidance opens the existing readonly panel preflight instead of SSH tool installation', async () => {
  http.get.mockResolvedValue({items: [{...row, source_key: 'panel:3', renewal: {engine: 'baota', supported: false,
    reason: '配置引用尚未完整核实'}}], total: 1, sources: [], active_jobs: []});
  await render(<SSLMonitor/>); await click('配置指导');
  expect(container.textContent).toContain('宝塔证书续签指导');
  expect(container.querySelector('a[href="/exec/panel?panel_id=3&operation=ssl_renew"]')).not.toBeNull();
  expect(container.textContent).not.toContain('install-cert');
  expect(http.post).not.toHaveBeenCalled();
});

function reminderFixture() {
  let muted = false;
  const reminder = () => ({id: 24, certificate_id: row.id, domain: row.domain, source_name: source.name, muted, message: '证书即将到期'});
  http.get.mockImplementation((path, config) => Promise.resolve(path.includes('reminders/')
    ? {items: !!Number(config.params.muted) === muted ? [reminder()] : [], total: !!Number(config.params.muted) === muted ? 1 : 0, unread_count: muted ? 0 : 1}
    : {items: [{...row, reminder: {id: 24, muted}}], total: 1, sources: [{...source, reminders: true}], stats: {}, active_jobs: []}));
  http.post.mockImplementation((path, data) => {muted = data.muted; return Promise.resolve(reminder());});
}
const rowButton = label => [...container.querySelectorAll('tbody button')].find(button => button.textContent === label);
const inReminder = label => [...container.querySelector('[aria-label="SSL 到期提醒"]').querySelectorAll('button')].find(button => button.textContent === label);

test('certificate list mutes and restores the same reminder and refreshes the reminder section', async () => {
  reminderFixture(); await render(<SSLMonitor/>);
  expect(rowButton('不再提醒')).toBeDefined(); expect(http.post).not.toHaveBeenCalled();
  await act(async () => {rowButton('不再提醒').click(); await flush();});
  expect(http.post).toHaveBeenLastCalledWith('/api/monitor/ssl/reminders/24/mute/', {muted: true});
  expect(rowButton('恢复提醒')).toBeDefined(); expect(inReminder('不再提醒')).toBeUndefined();
  expect(container.querySelector('tbody').textContent).toContain('app.example.test');
  await act(async () => {rowButton('恢复提醒').click(); await flush();});
  expect(http.post).toHaveBeenLastCalledWith('/api/monitor/ssl/reminders/24/mute/', {muted: false});
  expect(rowButton('不再提醒')).toBeDefined(); expect(inReminder('不再提醒')).toBeDefined();
});

test('muting from reminder section also refreshes the corresponding certificate row', async () => {
  reminderFixture(); await render(<SSLMonitor/>);
  await act(async () => {inReminder('不再提醒').click(); await flush();});
  expect(rowButton('恢复提醒')).toBeDefined(); expect(http.post).toHaveBeenCalledTimes(1);
});

test('failed certificate mute preserves the existing state and offers a controlled error', async () => {
  reminderFixture(); http.post.mockRejectedValue(new Error('private fixture transport detail')); await render(<SSLMonitor/>);
  await act(async () => {rowButton('不再提醒').click(); await flush();});
  expect(rowButton('不再提醒')).toBeDefined(); expect(rowButton('恢复提醒')).toBeUndefined();
  expect(container.textContent).toContain('提醒设置保存失败'); expect(container.textContent).not.toContain('private fixture');
});

test('certificate mute is single flight and late responses after account change do not refresh domains', async () => {
  localStorage.setItem('id', 'reminder-owner'); reminderFixture();
  let finish; http.post.mockImplementation(() => new Promise(resolve => {finish = resolve;})); await render(<SSLMonitor/>);
  await act(async () => {const button=rowButton('不再提醒'); button.click(); button.click(); await flush();});
  expect(http.post).toHaveBeenCalledTimes(1); const reads=http.get.mock.calls.length;
  localStorage.setItem('id', 'another-owner');
  await act(async () => {finish({muted: true}); await flush();});
  expect(http.get).toHaveBeenCalledTimes(reads);
  localStorage.removeItem('id');
});

test('certificates without valid reminders do not submit mute requests or enable source reminders', async () => {
  await render(<SSLMonitor/>);
  expect(rowButton('不再提醒')).toBeUndefined(); expect(container.querySelector('tbody').textContent).toContain('未开启提醒');
  expect(http.post).not.toHaveBeenCalled();
});

test('enabled reminders without a valid current reminder show no mute action', async () => {
  const original = http.get.getMockImplementation();
  http.get.mockImplementation((path, config) => path === '/api/monitor/ssl/' ? Promise.resolve({items: [{...row, reminder: null}],
    total: 1, sources: [{...source, reminders: true}], stats: {}, active_jobs: []}) : original(path, config));
  await render(<SSLMonitor/>);
  expect(rowButton('不再提醒')).toBeUndefined(); expect(container.querySelector('tbody').textContent).toContain('暂无到期提醒');
  expect(http.post).not.toHaveBeenCalled();
});
