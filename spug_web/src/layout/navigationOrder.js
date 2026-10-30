// Preferences contain route paths only; they never create routes or grant access.
export function navigationOrderKey() {
  try {return `spug:nav-order:v1:${localStorage.getItem('id') || 'current'}`;}
  catch (_) {return 'spug:nav-order:v1:current';}
}

export function readNavigationOrder(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '{}');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter(([group, order]) => /^[a-z][a-z0-9-]*$/.test(group) && Array.isArray(order))
      .map(([group, order]) => [group, [...new Set(order.filter(path => typeof path === 'string' && path.length <= 200))]]));
  } catch (_) {return {};}
}

export function orderChildren(children, saved) {
  const paths = Array.isArray(saved) ? saved : [];
  const byPath = new Map(children.map(child => [child.path, child]));
  const ordered = [];
  paths.forEach(path => {if (byPath.has(path)) {ordered.push(byPath.get(path)); byPath.delete(path);}});
  return ordered.concat([...byPath.values()]);
}

export function moveChild(paths, from, target) {
  if (!paths.includes(from) || !paths.includes(target) || from === target) return paths;
  const next = paths.filter(path => path !== from);
  next.splice(paths.indexOf(target), 0, from);
  return next;
}
