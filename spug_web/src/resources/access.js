// Display controls only. Every request is independently authorized by the API.
export function createResourceAccess(data = {}) {
  const has = code => data.is_supper === true || !code || code.split('&').every(item => (data.permissions || []).includes(item));
  const resources = kind => kind === 'host' ? data.hosts || [] : data.panels || [];
  const canManage = (kind, id, code) => has(code) && (data.is_supper === true || resources(kind).some(row => row.id === Number(id) && row.level === 'manage'));
  const canAll = (kind, ids, code) => ids.length > 0 && ids.every(id => canManage(kind, id, code));
  const canSource = (key, code) => {
    const [kind, id] = String(key || '').split(':');
    return ['host', 'panel'].includes(kind) && canManage(kind, Number(id), code);
  };
  return {...data, has, canManage, canAll, canSource,
    canAdd: data.is_supper === true || (!!data.quota && (data.quota.limit === null || data.quota.remaining > 0))};
}

export function accountResourceFields(editing, draft) {
  const result = editing ? {} : {server_limit: 0, resource_grants: []};
  if (Object.prototype.hasOwnProperty.call(draft, 'limit')) result.server_limit = draft.limit;
  if (Object.prototype.hasOwnProperty.call(draft, 'grants')) result.resource_grants = draft.grants;
  return result;
}

export function quotaText(quota) {
  if (!quota) return '额度加载中';
  return `已用 ${quota.used} / ${quota.limit === null ? '不限' : quota.limit} 台 · 剩余 ${quota.remaining === null ? '不限' : quota.remaining} 台`;
}
