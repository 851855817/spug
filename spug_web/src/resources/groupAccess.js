const nodeId = node => node.value ?? node.key ?? node.id;

export function canManageGroup(tree, id, access) {
  if (id === undefined || id === null || id === '') return false;
  for (const node of tree) {
    if (Number(nodeId(node)) === Number(id)) return access.is_supper === true || node.can_manage === true;
    if (canManageGroup(node.children || [], id, access)) return true;
  }
  return false;
}

export function manageableGroupTree(tree, access) {
  return tree.map(node => ({...node, disabled: access.is_supper !== true && node.can_manage !== true,
    children: manageableGroupTree(node.children || [], access)}));
}

export function canChangeResourceGroups(access, kind, id) {
  if (!id || access.is_supper === true) return true;
  const resources = kind === 'host' ? access.hosts : access.panels;
  return (resources || []).some(row => row.id === Number(id) && row.source === 'owned');
}

export function canAssignGroups(tree, ids, access, original = [], resource = {}) {
  const previous = new Set(original.map(Number)), selected = new Set(ids.map(Number));
  const unchanged = ids.length === 0 || (selected.size === previous.size && [...selected].every(id => previous.has(id)));
  if (!unchanged && !canChangeResourceGroups(access, resource.kind, resource.id)) return false;
  return ids.every(id => original.some(value => Number(value) === Number(id)) || canManageGroup(tree, id, access));
}
