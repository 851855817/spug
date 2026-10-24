import { selectPanels, parseSiteRows, buildTaskRequest, summarizeTask, stateColor, startPayload, panelBlocker } from './helpers';

const panels = [{id: 1, name: 'A', group_id: 2}, {id: 2, name: 'B', group_id: 3}];
const groups = [{id: 1}, {id: 2, parent_id: 1}, {id: 3, parent_id: 2}];

test('recursive groups and explicit panels produce a stable deduplicated selection', () => {
  expect(selectPanels(panels, groups, [2], [1, 2]).map(x => x.id)).toEqual([1, 2]);
  expect(selectPanels(panels, [{id: 1, parent_id: 2}, {id: 2, parent_id: 1}], [], [1])).toEqual([panels[0]]);
});

test('TSV maps each website only to its explicit panel and preserves additional domains', () => {
  const rows = parseSiteRows('1\ta.example.com,www.a.example.com\t/www/a\t74\t80\tA\n2\tb.example.com\t/www/b\t00\t8080\tB');
  const request = buildTaskRequest('site_add', panels, {}, rows);
  expect(request.params_by_panel['1'].entries[0].domains).toEqual(['a.example.com', 'www.a.example.com']);
  expect(request.params_by_panel['2'].entries[0]).toMatchObject({php_version: '00', port: 8080});
  expect(request.params_by_panel['1'].entries).toHaveLength(1);
});

test.each(['*.example.com', 'https://a.com', 'bad_domain.com', 'a.com:80', '127.0.0.1', 'example.123'])('rejects illegal DNS %s', domain => {
  const rows = parseSiteRows(`1\t${domain}\t/www/a\t74\t80\n2\tb.com\t/www/b\t00\t80`);
  expect(() => buildTaskRequest('site_add', panels, {}, rows)).toThrow(/域名/);
});

test('site identifiers are scoped to their own panel and never copied', () => {
  const resources = {1: {sites: [{id: 10}]}, 2: {sites: [{id: 20}]}};
  const values = {1: {sites: [10], enabled: false}, 2: {sites: [20], enabled: true}};
  expect(buildTaskRequest('site_basedir', panels, values, [], resources).params_by_panel['2'].sites).toEqual([20]);
  expect(() => buildTaskRequest('site_basedir', panels, {...values, 2: {sites: [10], enabled: true}}, [], resources)).toThrow(/网站.*面板/);
  expect(() => buildTaskRequest('site_add', [panels[0]], {}, [{panel_id: 2}])).toThrow(/面板/);
});

test('SSL selectors and exact version fields submit clean panel parameters', () => {
  expect(buildTaskRequest('ssl_apply', [panels[0]], {1: {all_sites: true, mode: 'ignored'}}).params_by_panel['1']).toEqual({all_sites: true});
  expect(buildTaskRequest('software_install', [panels[0]], {1: {name: 'php', m_version: '7.4', min_version: '33', install_type: '1', sites: [9]}}).params_by_panel['1'])
    .toEqual({name: 'php', m_version: '7.4', min_version: '33', install_type: '1'});
  expect(() => buildTaskRequest('ssl_apply', [panels[0]], {1: {sites: [10], domains: {10: ['*.a.com']}}})).toThrow(/域名/);
});

test('completed aggregates target errors even with no children; remote and uncertainty never become green', () => {
  const task = {state: 'completed', targets: [{state: 'failed', children: []}, {children: [{state: 'success'}, {state: 'uncertain'}, {state: 'remote'}]}]};
  expect(summarizeTask(task)).toMatchObject({success: 1, failed: 1, uncertain: 1, remote: 1});
  ['completed', 'failed', 'uncertain', 'remote'].forEach(state => expect(stateColor(state)).not.toBe('green'));
});

test('start includes only exact explicit acknowledgements and never edited parameters', () => {
  const task = {state: 'prepared', required_acknowledgements: ['ssl_panel_sync'], targets: [{children: [{state: 'ready'}]}]};
  expect(() => startPayload(task, [])).toThrow(/确认/);
  expect(startPayload(task, ['ssl_panel_sync'])).toEqual({acknowledgements: ['ssl_panel_sync']});
  expect(() => startPayload({...task, state: 'running'}, ['ssl_panel_sync'])).toThrow();
});

test('optional batch policy validates integer bounds and leaves legacy starts unchanged', () => {
  const task = {state: 'prepared', targets: [{children: [{state: 'ready'}]}]};
  expect(startPayload(task, [], {batch_size: 2, failure_threshold: 1})).toEqual({acknowledgements: [], execution_policy: {batch_size: 2, failure_threshold: 1}});
  expect(startPayload(task, [])).toEqual({acknowledgements: []});
  [0, 11, 1.5, null].forEach(batch_size => expect(() => startPayload(task, [], {batch_size, failure_threshold: 1})).toThrow());
  expect(() => startPayload(task, [], {batch_size: 2, failure_threshold: 3})).toThrow();
});

test('known blocked panels send empty parameters while ready panels remain strictly validated', () => {
  const ready = {...panels[0], api_configured: true, api_checked: true, api_version: '13.1.0'};
  const blocked = {...panels[1], api_configured: false, api_checked: false, api_version: ''};
  expect(buildTaskRequest('software_install', [ready, blocked], {1: {name: 'php', m_version: '7.4', min_version: '33'}}).params_by_panel['2']).toEqual({});
  expect(() => buildTaskRequest('software_install', [ready], {})).toThrow(/软件/);
});

test('9.2 panels have usable operation forms and PHP updates retain exact branch selection', () => {
  const panel = {...panels[0], api_configured: true, api_checked: true, api_version: '9.2.0'};
  expect(panelBlocker(panel)).toBe('');
  expect(buildTaskRequest('php_update', [panel], {1: {php_version: '74', m_version: '7.4', min_version: '34'}}).params_by_panel['1'])
    .toEqual({php_version: '74', m_version: '7.4', min_version: '34', install_type: '0'});
  expect(buildTaskRequest('panel_upgrade', [panel], {1: {channel: 'stable'}}, [], {1: {upgrade_mode: 'legacy_stable'}}).params_by_panel['1']).toEqual({channel: 'stable'});
  expect(panelBlocker({...panel, api_version: '9.2.1'})).toMatch(/未适配/);
});

test('upgrade payload follows freshly read resource mode after panel version changes', () => {
  const panel = {...panels[0], api_version: '9.2.0'};
  expect(buildTaskRequest('panel_upgrade', [panel], {1: {version: '13.1.0'}}, [], {1: {upgrade_mode: 'exact'}}).params_by_panel['1'])
    .toEqual({version: '13.1.0'});
  expect(buildTaskRequest('panel_upgrade', [{...panel, api_version: '11.7.0'}], {1: {channel: 'stable'}}, [], {1: {upgrade_mode: 'legacy_stable'}}).params_by_panel['1'])
    .toEqual({channel: 'stable'});
  expect(() => buildTaskRequest('panel_upgrade', [panel], {1: {channel: 'stable'}})).toThrow(/读取.*升级/);
});

test('a failed or pending upgrade query cannot submit a previously selected channel', () => {
  const panel = {...panels[0], api_version: '9.2.0'};
  const values = {1: {channel: 'stable'}};
  expect(() => buildTaskRequest('panel_upgrade', [panel], values, [], {1: {
    upgrade_mode: 'legacy_stable', upgrade_available: false, upgrade_error: '升级信息暂不可用，请重新读取'}}))
    .toThrow(/升级信息暂不可用/);
  expect(() => buildTaskRequest('panel_upgrade', [panel], values, [], {1: {
    upgrade_mode: 'legacy_stable', upgrade_available: true, upgrade_loading: true}}))
    .toThrow(/读取中/);
});
