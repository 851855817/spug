import http from './http';

jest.mock('./history', () => ({location: {pathname: '/'}, push: jest.fn()}));
jest.mock('./functools', () => ({X_TOKEN: 'fixture-token'}));
jest.mock('./i18n', () => ({t: value => value, langMode: 'zh'}));
jest.mock('antd', () => ({message: {error: jest.fn()}}));

const adapter = data => async config => ({status: 200, data, headers: {}, config});
let onChange;
beforeEach(() => {onChange = jest.fn(); window.addEventListener('resource-access-changed', onChange);});
afterEach(() => {window.removeEventListener('resource-access-changed', onChange); jest.clearAllMocks();});

test.each(['/api/host/', '/api/host/import/', '/api/host/import/cloud/', '/api/host/import/cloud/?type=ali',
  '/api/baota/', '/api/baota/monitor/link/', '/api/account/resources/'])('successful write to %s refreshes effective permissions and quota', async url => {
  await expect(http.post(url, {}, {adapter: adapter({data: 2})})).resolves.toBe(2);
  expect(onChange).toHaveBeenCalledTimes(1);
});

test('cloud-region reads do not request a quota refresh', async () => {
  await http.get('/api/host/import/region/', {adapter: adapter({data: []})});
  expect(onChange).not.toHaveBeenCalled();
});

test('failed cloud import does not claim resources changed', async () => {
  await expect(http.post('/api/host/import/cloud/', {}, {adapter: adapter({error: 'Rejected fixture'})})).rejects.toBe('Rejected fixture');
  expect(onChange).not.toHaveBeenCalled();
});
