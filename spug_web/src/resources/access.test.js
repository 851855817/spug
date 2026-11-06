import {createResourceAccess, accountResourceFields, quotaText} from './access';

test('effective resource levels and independent feature permissions both bound writes', () => {
  const access = createResourceAccess({permissions: ['baota.panel.edit', 'exec.panel.execute'],
    hosts: [{id: 1, level: 'manage'}], panels: [{id: 2, level: 'read'}, {id: 3, level: 'manage'}]});
  expect(access.canManage('panel', 2, 'baota.panel.edit')).toBe(false);
  expect(access.canManage('panel', 3, 'baota.panel.edit')).toBe(true);
  expect(access.canManage('panel', 3, 'baota.panel.login')).toBe(false);
  expect(access.canManage('panel', 99, 'baota.panel.edit')).toBe(false);
  expect(access.canAll('panel', [2, 3], 'exec.panel.execute')).toBe(false);
  expect(access.canAll('panel', [], 'exec.panel.execute')).toBe(false);
  expect(createResourceAccess().canManage('host', 1)).toBe(false);
});

test('administrator has global resource scope and explicit unlimited quota differs from zero', () => {
  expect(createResourceAccess({is_supper: true}).canManage('panel', 99, 'baota.panel.login')).toBe(true);
  expect(quotaText({used: 3, limit: null, remaining: null})).toContain('不限');
  expect(quotaText({used: 0, limit: 0, remaining: 0})).toContain('0 / 0');
});

test('editing unrelated account fields omits policy replacement; zero, unlimited and empty grants remain explicit', () => {
  expect(accountResourceFields(true, {})).toEqual({});
  expect(accountResourceFields(false, {})).toEqual({server_limit: 0, resource_grants: []});
  expect(accountResourceFields(true, {limit: null, grants: []})).toEqual({server_limit: null, resource_grants: []});
  expect(accountResourceFields(true, {limit: 0})).toEqual({server_limit: 0});
});
