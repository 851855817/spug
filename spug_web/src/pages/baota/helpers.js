export function buildGroupTree(groups) {
  const nodes = new Map(groups.map(group => [group.id, {
    key: group.id, value: group.id, title: group.name, name: group.name, children: [],
  }]));
  const roots = [];
  groups.forEach(group => {
    const node = nodes.get(group.id);
    const parent = nodes.get(group.parent_id);
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  });
  return roots;
}

export function descendantIds(groups, groupId) {
  const ids = new Set([groupId]);
  const queue = [groupId];
  for (let i = 0; i < queue.length; i++) {
    groups.filter(group => group.parent_id === queue[i]).forEach(group => {
      if (!ids.has(group.id)) {
        ids.add(group.id);
        queue.push(group.id);
      }
    });
  }
  return ids;
}

export function filterPanels(panels, groups, groupId, search) {
  const selected = groupId ? descendantIds(groups, groupId) : null;
  const query = search.trim().toLowerCase();
  return panels.filter(panel => (!selected || selected.has(panel.group_id)) &&
    [panel.name, panel.url, panel.username].some(value => value.toLowerCase().includes(query)));
}
