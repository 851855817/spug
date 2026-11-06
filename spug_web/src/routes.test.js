import {hasPermission, updatePermissions} from './libs/functools';

jest.mock('libs', () => ({t: value => value}));
const pages = ['./pages/home', './pages/dashboard', './pages/host', './pages/baota', './pages/exec/task',
  './pages/exec/template', './pages/exec/transfer', './pages/exec/panel', './pages/panel-inventory',
  './pages/deploy/app', './pages/deploy/repository', './pages/deploy/request', './pages/schedule',
  './pages/config/environment', './pages/config/service', './pages/config/app', './pages/config/setting',
  './pages/monitor', './pages/server-monitor', './pages/ssl-monitor', './pages/server-expiry',
  './pages/alarm/alarm', './pages/alarm/group', './pages/alarm/contact', './pages/system/account',
  './pages/system/role', './pages/system/setting', './pages/system/health', './pages/system/login',
  './pages/system/credential', './pages/welcome/index', './pages/welcome/info', './pages/pipeline', './pages/pipeline/Editor'];
let routes;
beforeAll(() => {
  pages.forEach(path => jest.doMock(path, () => ({__esModule: true, default: () => null,
    LegacyOperations: () => null, SiteInventory: () => null, BackupInventory: () => null})));
  routes = require('./routes').default;
});
afterEach(() => {localStorage.clear(); updatePermissions();});

test('file-distribution-only accounts can see both the batch menu and its file distribution entry', () => {
  localStorage.setItem('is_supper', 'false'); localStorage.setItem('permissions', JSON.stringify(['exec.transfer.do']));
  updatePermissions();
  const batch = routes.find(route => route.child?.some(child => child.path === '/exec/transfer'));
  expect(hasPermission(batch.auth)).toBe(true);
  const visible = batch.child.filter(child => !child.hidden && (!child.auth || hasPermission(child.auth)));
  expect(visible.map(child => child.path)).toEqual(['/exec/transfer']);
});
