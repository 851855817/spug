// Production UI with synthetic A/B/admin APIs. No customer or production access.
const fs = require('fs');
const path = require('path');
const http = require('http');
const assert = require('assert/strict');
const crypto = require('crypto');
const {chromium} = require(process.env.KOPS_QA_PLAYWRIGHT || 'playwright');
const root = path.resolve(__dirname, '../..');
const build = path.join(root, 'spug_web/build');
const output = path.resolve(process.argv[2] || path.join(root, 'docs/releases/account-resource-qa'));
const permissions = ['baota.panel.view', 'baota.panel.edit', 'baota.panel.add', 'baota.panel.login',
  'baota.panel.test', 'baota.panel.del', 'monitor.server.view', 'monitor.server.configure'];
const panel = (id, name, group_id = 1) => ({id, name, group_id, url: `http://192.0.2.${id}:8888/entry`,
  username: 'fixture', has_password: true, has_api_key: true, api_base_url: `http://192.0.2.${id}:8888`,
  api_version: '13.1.0', api_checked_at: '2026-11-06 12:00:00', created_at: '2026-11-06 12:00:00', desc: ''});
const panels = [panel(1, 'A-owned'), panel(2, 'B-private', 2), panel(3, 'shared-readonly', null)];
const policy = {limit: 2, grants: [{kind: 'panel', id: 3, level: 'read'}]};
const report = {mocked_api_only: true, real_browser: true, assertions: [], screenshots: [],
  requests: [], blocked_outbound: [], unexpected_api: [], runtime_errors: [], passed: false};
const check = name => report.assertions.push(name);
const server = http.createServer((req, res) => {
  const pathname = decodeURIComponent(new URL(req.url, 'http://127.0.0.1').pathname);
  let target = path.resolve(build, '.' + pathname);
  if (!target.startsWith(build + path.sep) || !fs.existsSync(target) || !fs.statSync(target).isFile()) target = path.join(build, 'index.html');
  const ext = path.extname(target);
  res.setHeader('Content-Type', ({'.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json', '.svg': 'image/svg+xml'})[ext] || 'application/octet-stream');
  res.end(fs.readFileSync(target));
});
let browser;
async function main() {
  assert(fs.existsSync(path.join(build, 'asset-manifest.json')), 'Build the production UI first');
  fs.mkdirSync(output, {recursive: true});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  report.fixture = base;
  const manifest = JSON.parse(fs.readFileSync(path.join(build, 'asset-manifest.json')));
  report.build_asset = manifest.files['main.js'];
  report.build_sha256 = crypto.createHash('sha256').update(fs.readFileSync(path.join(build, report.build_asset.replace(/^\//, '')))).digest('hex');
  browser = await chromium.launch({headless: true,
    executablePath: process.env.KOPS_QA_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    args: ['--disable-background-networking', '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1']});
  async function session(actor) {
    let revoked = false;
    const ctx = await browser.newContext({viewport: {width: 1440, height: 1080}});
    await ctx.addInitScript(({actor}) => {
      localStorage.setItem('id', actor === 'admin' ? '99' : actor === 'A' ? '1' : '2');
      localStorage.setItem('token', 'fixture-' + actor);
      // Deliberately stale elevated cache: effective API permissions must replace it.
      localStorage.setItem('is_supper', 'true');
      localStorage.setItem('nickname', actor);
      localStorage.setItem('spug:language', 'zh');
      window.WebSocket = class {send() {} close() {}};
    }, {actor});
    const effective = who => ({user_id: who === 'admin' ? 99 : who === 'A' ? 1 : 2,
      is_supper: who === 'admin', permissions: who === 'B' ? ['baota.panel.view'] : permissions,
      quota: {used: who === 'A' ? 2 : 1, limit: who === 'admin' ? null : who === 'A' ? policy.limit : 0,
        remaining: who === 'admin' || policy.limit === null ? null : Math.max(0, (who === 'A' ? policy.limit : 0) - (who === 'A' ? 2 : 1))},
      hosts: [], panels: (who === 'admin' ? panels : who === 'A' ? [panels[0], panels[2]] : [panels[1]])
        .map(p => ({id: p.id, name: p.name, kind: 'panel', source: p.id === 3 ? 'assigned' : 'owned',
          level: who === 'A' && (p.id === 3 || revoked) ? 'read' : 'manage'})),
      grants: who === 'A' ? policy.grants : [], available_hosts: [], available_panels: panels});
    const page = await ctx.newPage();
    page.on('pageerror', err => report.runtime_errors.push(err.message));
    await ctx.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.origin !== base) {report.blocked_outbound.push(req.url()); return route.abort();}
      if (!url.pathname.startsWith('/api/')) return route.continue();
      const item = {actor, method: req.method(), path: url.pathname};
      if (req.postData()) item.data = req.postDataJSON();
      report.requests.push(item);
      let data, error = '';
      if (item.method === 'GET') {
        if (item.path === '/api/account/resources/') data = effective(url.searchParams.has('user_id') ? 'A' : actor);
        else if (item.path === '/api/baota/') data = actor === 'admin' ? panels : actor === 'A' ? [panels[0], panels[2]] : [panels[1]];
        else if (item.path === '/api/baota/group/') data = [{id: actor === 'B' ? 2 : 1, name: actor + '-group', parent_id: null, can_manage: actor !== 'B'}];
        else if (item.path === '/api/baota/monitor/sources/') data = {sources: (actor === 'admin' ? panels : actor === 'A' ? [panels[0], panels[2]] : [panels[1]])
          .map(p => ({id: p.id, kind: 'panel', name: p.name, enabled: false})), hosts: []};
        else if (item.path === '/api/account/role/') data = [{id: 1, name: '模拟网站运维'}];
        else if (item.path === '/api/account/user/') data = [{id: 1, username: 'A', nickname: '员工 A', is_supper: false,
          is_active: true, role_ids: [1], type: 'default', resource_quota: effective('A').quota}];
        else if (item.path === '/api/notify/') data = [];
        else if (item.path === '/api/setting/user/') data = {};
        else if (item.path.startsWith('/api/setting/appearance')) data = {version: 1, preset: 'porcelain', revision: 'fixture'};
        else {report.unexpected_api.push(item); error = 'Unexpected fixture API';}
      } else if (actor === 'admin' && item.method === 'POST' && item.path === '/api/account/user/') {
        if (Object.hasOwn(item.data, 'server_limit')) policy.limit = item.data.server_limit;
        if (Object.hasOwn(item.data, 'resource_grants')) policy.grants = item.data.resource_grants;
        data = {};
      } else {report.unexpected_api.push(item); error = 'No remote mutations are available in this fixture';}
      return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({data, error})});
    });
    return {page, ctx, revoke: () => {revoked = true;}};
  }
  async function shot(page, name) {await page.screenshot({path: path.join(output, name), fullPage: true}); report.screenshots.push(name);}
  const a = await session('A');
  await a.page.goto(base + '/baota');
  await a.page.getByRole('row').filter({hasText: 'A-owned'}).waitFor();
  const own = a.page.getByRole('row').filter({hasText: 'A-owned'});
  const read = a.page.getByRole('row').filter({hasText: 'shared-readonly'});
  assert.equal(await a.page.getByText('B-private', {exact: true}).count(), 0); check('A sees own and explicitly shared records, no B private record');
  assert.equal(await own.getByRole('button', {name: '编辑', exact: true}).isEnabled(), true);
  assert.equal(await read.getByRole('button', {name: '编辑', exact: true}).isDisabled(), true);
  assert.equal(await read.getByRole('button', {name: /打开/}).isDisabled(), true);
  assert.equal(await read.getByRole('switch').isDisabled(), true); check('Read sharing cannot edit, sign in or enable monitoring despite action permissions');
  assert.equal(await a.page.getByRole('button', {name: /添加面板/}).isDisabled(), true); check('Exhausted unified quota disables creation');
  await shot(a.page, 'A-scope-and-readonly.png');
  await own.getByRole('button', {name: '编辑', exact: true}).click();
  const openEditor = a.page.getByRole('dialog');
  await openEditor.getByRole('button', {name: /^保\s*存$/}).waitFor();
  a.revoke(); await a.page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await a.page.waitForFunction(() => [...document.querySelectorAll('tr')].find(r => r.textContent.includes('A-owned'))?.querySelector('button:not([disabled])')?.textContent !== '编辑');
  await a.page.waitForTimeout(200);
  assert.equal(await own.getByRole('button', {name: '编辑', exact: true}).isDisabled(), true); check('Focus refresh applies revoked manage grant to an already open page');
  assert.equal(await openEditor.getByRole('button', {name: /^保\s*存$/}).isDisabled(), true);
  check('Revocation also disables saving an already open panel editor');
  await openEditor.getByRole('button', {name: /^取\s*消$/}).click();
  await openEditor.waitFor({state: 'hidden'});
  const b = await session('B'); await b.page.goto(base + '/baota');
  await b.page.getByRole('row').filter({hasText: 'B-private'}).waitFor();
  assert.equal(await b.page.getByText('A-owned', {exact: true}).count(), 0);
  assert.equal(await b.page.getByRole('button', {name: '编辑', exact: true}).count(), 0); check('B remains isolated and stale superuser cache does not grant write actions');
  const admin = await session('admin'); await admin.page.goto(base + '/system/account');
  await admin.page.getByRole('row').filter({hasText: '员工 A'}).waitFor();
  const account = admin.page.getByRole('row').filter({hasText: '员工 A'});
  await account.getByRole('button', {name: '编辑', exact: true}).click();
  let modal = admin.page.getByRole('dialog');
  await modal.getByRole('heading', {name: '服务器额度与资源分配'}).waitFor();
  await modal.locator('summary').click();
  assert.equal(await modal.getByText('baota.panel.view', {exact: false}).count() > 0, true); check('Administrator sees saved effective permissions, assigned resources and used quota');
  await modal.getByText('A-owned #1 · 可管理 · 本人资源', {exact: true}).waitFor();
  await modal.getByText('shared-readonly #3 · 只读 · 管理员分配', {exact: true}).waitFor();
  check('Effective preview distinguishes personally owned and administrator-assigned resources');
  await shot(admin.page, 'admin-account-resource-policy.png');
  await modal.getByRole('button', {name: /确\s*定/}).click();
  await modal.waitFor({state: 'hidden'});
  const unchanged = report.requests.filter(r => r.method === 'POST').at(-1).data;
  assert(!Object.hasOwn(unchanged, 'server_limit') && !Object.hasOwn(unchanged, 'resource_grants')); check('Saving unrelated account fields preserves existing quota and grants');
  await account.getByRole('button', {name: '编辑', exact: true}).click(); modal = admin.page.getByRole('dialog');
  await modal.getByLabel('服务器上限').fill('0');
  await modal.getByRole('button', {name: /确\s*定/}).click(); await modal.waitFor({state: 'hidden'});
  assert.equal(report.requests.filter(r => r.method === 'POST').at(-1).data.server_limit, 0); check('Explicit zero quota is submitted as zero');
  await account.getByRole('button', {name: '编辑', exact: true}).click(); modal = admin.page.getByRole('dialog');
  await modal.locator('.ant-select[aria-label="额度模式"] .ant-select-selector').click();
  await admin.page.getByText('不限', {exact: true}).last().click();
  await modal.getByRole('button', {name: /确\s*定/}).click(); await modal.waitFor({state: 'hidden'});
  assert.equal(report.requests.filter(r => r.method === 'POST').at(-1).data.server_limit, null); check('Explicit unlimited quota is submitted as null');
  await account.getByRole('button', {name: '编辑', exact: true}).click(); modal = admin.page.getByRole('dialog');
  await modal.locator('.ant-select[aria-label="panel-3授权"] .ant-select-selector').click();
  await admin.page.getByText('可管理', {exact: true}).last().click();
  await modal.getByRole('button', {name: /确\s*定/}).click(); await modal.waitFor({state: 'hidden'});
  const assigned = report.requests.filter(r => r.method === 'POST').at(-1).data;
  assert.deepEqual(assigned.resource_grants, [{kind: 'panel', id: 3, level: 'manage'}]);
  assert(!Object.hasOwn(assigned, 'server_limit'));
  check('Administrator can change one assigned resource to manage without rewriting the quota');
  await admin.page.getByRole('button', {name: /新建/, exact: false}).first().click(); modal = admin.page.getByRole('dialog');
  await modal.getByLabel('服务器上限').waitFor();
  assert.equal(await modal.getByLabel('服务器上限').inputValue(), '0');
  check('New ordinary account starts with zero server quota');
  await modal.getByRole('button', {name: /取\s*消/}).click(); await modal.waitFor({state: 'hidden'});
  assert.equal(report.runtime_errors.length, 0); assert.equal(report.blocked_outbound.length, 0);
  assert.equal(report.unexpected_api.length, 0); check('Only recognized loopback fixture requests; no customer operations or runtime exceptions');
  report.passed = true;
}
main().catch(error => {report.failure = error.stack; process.exitCode = 1;}).finally(async () => {
  if (browser) await browser.close(); await new Promise(resolve => server.close(resolve));
  fs.mkdirSync(output, {recursive: true}); fs.writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({passed: report.passed, assertions: report.assertions.length, report: path.join(output, 'browser-report.json'), failure: report.failure}));
});
