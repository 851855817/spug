import { buildGroupTree, descendantIds, filterPanels, inferAPIBaseURL } from './helpers';

const groups = [
  {id: 1, name: '生产', parent_id: null},
  {id: 2, name: '华东', parent_id: 1},
  {id: 3, name: '测试', parent_id: null},
];

test('builds the group hierarchy without mutating input', () => {
  const tree = buildGroupTree(groups);
  expect(tree.map(x => x.key)).toEqual([1, 3]);
  expect(tree[0].children[0].title).toBe('华东');
  expect(groups[0].children).toBeUndefined();
});

test('parent selection includes panels in descendant groups', () => {
  expect([...descendantIds(groups, 1)]).toEqual([1, 2]);
  const panels = [{id: 1, group_id: 2, name: '客户 A', url: 'https://a.com', username: 'admin'},
    {id: 2, group_id: 3, name: '客户 B', url: 'https://b.com', username: 'test'}];
  expect(filterPanels(panels, groups, 1, '').map(x => x.id)).toEqual([1]);
  expect(filterPanels(panels, groups, 0, 'ADMIN').map(x => x.id)).toEqual([1]);
  expect(filterPanels(panels, groups, 0, 'b.com').map(x => x.id)).toEqual([2]);
});

test('descendant traversal terminates even with malformed cyclic input', () => {
  const cycle = [{id: 1, parent_id: 2}, {id: 2, parent_id: 1}];
  expect([...descendantIds(cycle, 1)]).toEqual([1, 2]);
});

test.each([
  ['https://panel.example:8888/security-entry?from=login#top', 'https://panel.example:8888'],
  ['http://192.0.2.10:8888/secret', 'http://192.0.2.10:8888'],
  ['https://[2001:db8::1]:8443/entry', 'https://[2001:db8::1]:8443'],
  ['https://panel.example/', 'https://panel.example'],
])('infers the API origin from login address %s', (address, expected) => {
  expect(inferAPIBaseURL(address)).toBe(expected);
});

test.each(['', 'panel.example:8888', '/entry', 'file:///etc/passwd',
  'https://account:password@panel.example:8888/entry', 'https://panel.example\\entry',
  'https://panel.example/invalid path'])('does not infer from invalid or credential-bearing URL %s', address => {
  expect(inferAPIBaseURL(address)).toBe('');
});
