import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import TaskItem from './TaskItem';

jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  return {Space: Wrapper, Typography: {Text: Wrapper, Paragraph: Wrapper}, Tag: Wrapper,
    Alert: ({message, description}) => <div>{message}{description}</div>, Descriptions: Object.assign(Wrapper, {Item: Wrapper}),
    Input: ({value, onChange, ...props}) => <input {...props} value={value} onChange={onChange}/>,
    Pagination: ({current, total, pageSize, onChange}) => <div>第 {current} 页
      <button disabled={current <= 1} onClick={() => onChange(current - 1)}>上一页</button>
      <button disabled={current * pageSize >= total} onClick={() => onChange(current + 1)}>下一页</button></div>};
});

const phpChild = (state = 'ready', extra = {}) => ({state, snapshot: {key: 'php-update:74', label: 'PHP 7.4 同分支更新',
  before: {runtime: {version: '7.4.32'}}, plan: {target_version: '7.4.33', impact: {website_refs: []}}}, ...extra});
const renderPHP = child => act(() => {ReactDOM.render(<TaskItem operation="php_update" child={child}/>, container);});

const dnsFailure = () => ({state: 'failed', intents: [], snapshot: {state: 'failed',
  message: '无法核对该网站的目标配置', before: {ssl: {auth_type: 'dns'}}, plan: {renewal: true}},
  result: {state: 'failed', message: '无法核对该网站的目标配置', evidence: {}}});
const renderSSL = (child, operation = 'ssl_renew') => act(() => {
  ReactDOM.render(<TaskItem operation={operation} child={child}/>, container);
});

test('saved DNS renewal preflight failure explains reason and remedy without changing history', () => {
  const child = dnsFailure(); const original = JSON.stringify(child);
  renderSSL(child);
  expect(container.textContent).toContain('原证书使用 DNS 验证，当前同订单续签仅支持 HTTP 验证');
  expect(container.textContent).toContain('请到原宝塔面板按 DNS 方式续签');
  expect(container.textContent).not.toContain('无法核对该网站的目标配置');
  expect(JSON.stringify(child)).toBe(original);
});

test('new DNS renewal diagnosis shows the same concise remedy', () => {
  const child = dnsFailure();
  child.result.message = child.snapshot.message = '原证书使用 DNS 验证，当前同订单续签仅支持 HTTP 验证';
  renderSSL(child);
  expect(container.textContent).toContain('请到原宝塔面板按 DNS 方式续签');
  expect(container.textContent).not.toContain('续签成功');
});

test.each(['ssl_apply', 'site_rewrite'])('legacy DNS hints never apply to other operations (%s)', operation => {
  renderSSL(dnsFailure(), operation);
  expect(container.textContent).toContain('无法核对该网站的目标配置');
  expect(container.textContent).not.toContain('请到原宝塔面板');
});

test.each(['http', 'tls', ''])('legacy non-DNS evidence is never guessed (%s)', auth => {
  const child = dnsFailure(); child.snapshot.before.ssl.auth_type = auth;
  renderSSL(child);
  expect(container.textContent).toContain('无法核对该网站的目标配置');
  expect(container.textContent).not.toContain('请到原宝塔面板');
});

test.each(['snapshot', 'intents', 'missingIntents', 'remote', 'differentError', 'resultState', 'childState'])(
  'DNS preflight compatibility never replaces execution or unrelated failures (%s)', condition => {
    const child = dnsFailure();
    if (condition === 'snapshot') child.snapshot.state = 'ready';
    if (condition === 'intents') child.intents = [{phase: 'apply'}];
    if (condition === 'missingIntents') delete child.intents;
    if (condition === 'remote') child.result.remote = {phase: 'apply'};
    if (condition === 'differentError') child.result.message = '其他明确错误';
    if (condition === 'resultState') child.result.state = 'uncertain';
    if (condition === 'childState') child.state = 'uncertain';
    renderSSL(child);
    expect(container.textContent).toContain(child.result.message);
    expect(container.textContent).not.toContain('请到原宝塔面板');
  });

test('PHP upgrade review separates expected version from actual evidence and states health coverage', () => {
  renderPHP(phpChild());
  expect(container.textContent).toContain('升级核对');
  expect(container.textContent).toContain('预检版本：7.4.32');
  expect(container.textContent).toContain('预计目标：7.4.33');
  expect(container.textContent).toContain('实际读回：尚未读回');
  expect(container.textContent).toContain('FPM、默认 CLI 与网站请求未核实');
  expect(container.textContent).toContain('本次预检未发现使用该分支的网站');
});

test('task success with CLI version evidence is not shown as complete website health verification', () => {
  renderPHP(phpChild('success', {result: {state: 'success', evidence: {version: '7.4.33', runtime_scope: 'branch_cli_binary'}}}));
  expect(container.textContent).toContain('实际读回：7.4.33');
  expect(container.textContent).toContain('核验范围：目标分支 PHP CLI 二进制');
  expect(container.textContent).toContain('CLI 扩展：缺少对照记录');
  expect(container.textContent).toContain('FPM、默认 CLI 与网站请求未核实');
});

test('missing impact evidence is unknown rather than a zero-site assertion', () => {
  renderPHP({state: 'failed', snapshot: {before: {}, plan: {}}});
  expect(container.textContent).toContain('影响网站：未生成清单');
  expect(container.textContent).not.toContain('未发现使用该分支的网站');
});

test('frozen website impact is lazy, searchable by path and paginated without exposing raw HTML', () => {
  const refs = Array.from({length: 23}, (_, index) => ({id: index + 1, name: `site-${index + 1}.test`, path: `/www/root-${index + 1}`}));
  refs[0].name = '<img src=x onerror=alert(1)>';
  const child = phpChild(); child.snapshot.plan.impact.website_refs = refs;
  renderPHP(child);
  expect(container.textContent).toContain('影响网站（23）');
  expect(container.textContent).not.toContain('/www/root-23');
  const list = container.querySelector('details[aria-label="影响网站清单"]');
  act(() => {list.open = true; list.dispatchEvent(new Event('toggle', {bubbles: true}));});
  expect(container.querySelectorAll('[data-upgrade-site]').length).toBe(10);
  expect(container.querySelector('img')).toBeNull();
  expect(container.textContent).toContain('<img src=x onerror=alert(1)>');
  act(() => {[...container.querySelectorAll('button')].find(x => x.textContent === '下一页').click();});
  expect(container.textContent).toContain('/www/root-11');
  expect(container.textContent).not.toContain('/www/root-1 ');
  const input = container.querySelector('input[aria-label="检索影响网站"]');
  act(() => {Simulate.change(input, {target: {value: 'root-23'}});});
  expect(container.querySelectorAll('[data-upgrade-site]').length).toBe(1);
  expect(container.textContent).toContain('/www/root-23');
  expect(container.textContent).toContain('匹配 1 / 23 个');
  act(() => {Simulate.change(input, {target: {value: 'no-such-site'}});});
  expect(container.textContent).toContain('没有匹配的网站');
});

test('legacy panel candidate and unsupported actual version stay explicit', () => {
  act(() => {ReactDOM.render(<TaskItem operation="panel_upgrade" child={{state: 'uncertain', snapshot: {
    before: {panel_version: '9.2.0'}, plan: {mode: 'legacy_stable', checked_version: '13.1.0'}},
    result: {evidence: {version: '14.0.0', version_supported: false}}}}/>, container);});
  expect(container.textContent).toContain('检查候选：13.1.0');
  expect(container.textContent).toContain('实际读回：14.0.0');
  expect(container.textContent).toContain('读回版本尚未适配');
  expect(container.textContent).toContain('官方后台决定实际版本');
  expect(container.textContent).not.toContain('影响网站（0）');
});

test('new PHP preflight shows recorded module baseline without claiming post-update verification', () => {
  const child = phpChild();
  child.snapshot.before.runtime.cli_modules = ['core', 'pdo_mysql'];
  renderPHP(child);
  expect(container.textContent).toContain('CLI 扩展：已记录 2 个，等待更新后对照');
  expect(container.textContent).not.toContain('CLI 扩展：未发现缺失');
});

test.each([
  [{state: 'changed', added: ['redis'], removed: ['pdo_mysql']}, '缺失 1 个 · 新增 1 个', '缺失：pdo_mysql'],
  [{state: 'changed', added: ['redis'], removed: []}, '未发现缺失 · 新增 1 个', '新增：redis'],
  [{state: 'unchanged', added: [], removed: []}, '未发现缺失', null],
  [{state: 'unavailable', reason: 'readback_unavailable'}, '读回清单未核实', null],
])('PHP module comparisons stay distinct from task status and FPM health (%j)', (check, summary, detail) => {
  renderPHP(phpChild('uncertain', {result: {evidence: {version: '7.4.33', cli_module_check: check}}}));
  expect(container.textContent).toContain(`CLI 扩展：${summary}`);
  expect(container.textContent).toContain('FPM、默认 CLI 与网站请求未核实');
  if (detail) {
    const changes = container.querySelector('details[aria-label="CLI 扩展变化"]');
    act(() => {changes.open = true; changes.dispatchEvent(new Event('toggle', {bubbles: true}));});
    expect(container.textContent).toContain(detail);
  }
});
let container;
beforeEach(() => {container = document.createElement('div'); document.body.appendChild(container);});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove();});

test.each([['individual', '单独设置'], ['common', '统一设置']])('upload frozen preview shows directory, %s source and overwrite counts with details closed', (source, label) => {
  act(() => {ReactDOM.render(<TaskItem operation="file_upload" child={{state: 'ready', snapshot: {label: '上传文件 / 目录',
    target: {target_dir: '/www/frozen', directory_source: source}, before: {new_files: 5, overwrite_files: 2},
    plan: {file_count: 7, total_bytes: 21, manifest_sha256: 'technical-hash'}}}}/>, container);});
  expect(container.textContent).toContain('最终目录：/www/frozen');
  expect(container.textContent).toContain(`目录来源：${label}`);
  expect(container.textContent).toContain('预计新增：5 个'); expect(container.textContent).toContain('预计覆盖：2 个');
  expect(container.querySelector('details').open).toBe(false);
  expect(container.textContent).not.toContain('technical-hash');
});

test('skipped operations retain the concrete reason without a technical dump', () => {
  act(() => {ReactDOM.render(<TaskItem operation="site_rewrite" child={{state: 'skipped', snapshot: {
    label: 'example.test', message: 'OpenLiteSpeed 尚未适配', plan: {}}}}/>, container);});
  expect(container.textContent).toContain('OpenLiteSpeed 尚未适配');
});

test.each([
  ['software_install', {blockers: ['可用内存低于 512 MiB'], requirements: {memory_mb: 512}}, '可用内存低于 512 MiB'],
  ['software_uninstall', {impact: {sites: ['customer.example'], databases: ['customer_db']}}, 'customer.example'],
])('preflight business evidence stays available for %s', (operation, plan, data) => {
  act(() => {ReactDOM.render(<TaskItem operation={operation} child={{state: 'failed', snapshot: {
    message: '请查看预检明细', plan}}}/>, container);});
  const details = container.querySelector('details');
  act(() => {details.open = true; details.dispatchEvent(new Event('toggle', {bubbles: true}));});
  expect(container.textContent).toContain(data);
});

test('existing SSL keeps its validity data, concrete sync scope and verification limit', () => {
  act(() => {ReactDOM.render(<TaskItem operation="ssl_apply" child={{state: 'already', snapshot: {
    message: 'include 与实际 TLS 未核验', before: {ssl: {expires_at: '2027-01-01'},
      ssl_sync_inventory: {matching_sites: [{name: 'other.example'}], truncated: true}}, plan: {}}}}/>, container);});
  expect(container.textContent).toContain('include 与实际 TLS 未核验');
  const details = container.querySelector('details');
  act(() => {details.open = true; details.dispatchEvent(new Event('toggle', {bubbles: true}));});
  expect(container.textContent).toContain('2027-01-01'); expect(container.textContent).toContain('other.example');
});
