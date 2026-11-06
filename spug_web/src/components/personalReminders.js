// Personal reminder preferences by K, 2026-10-27. Released under AGPL-3.0.
import { useEffect, useState } from 'react';
import { http } from 'libs';

const API = '/api/setting/user/';
const PREFIX = 'mute:v1:';
let activeStore;
const account = () => `${localStorage.getItem('id') || ''}:${localStorage.getItem('token') || ''}`;
const same = (a, b) => Boolean(a && b && a.identity === b.identity && a.cycle === b.cycle);
const date = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;

export function siteReminder(row) {
  const id = row?.id || (row?.panel_id && row?.site_id ? `${row.panel_id}:${row.site_id}` : '');
  const cycle = date(row?.expires_at);
  return id && cycle ? {identity: `certificate:${id}`, cycle, label: String(row.name || '网站证书').slice(0, 200)} : null;
}
export function serverReminder(row) {
  const id = row?.key || row?.asset_key, cycle = date(row?.expires_on);
  return id && cycle ? {identity: `server:${id}`, cycle, label: String(row.name || '服务器').slice(0, 200)} : null;
}
export function notificationReminder(row) {
  return row?.id && row?.created_at ? {identity: `notification:${row.id}`, cycle: String(row.created_at),
    label: String(row.title || '事务提醒').slice(0, 200)} : null;
}

function parse(response) {
  if (!response || typeof response !== 'object' || Array.isArray(response)) throw new Error('Invalid preferences');
  const records = {};
  Object.entries(response).forEach(([key, value]) => {
    if (!/^mute:v1:[0-9a-f]{24}$/.test(key) || typeof value !== 'string' || value.length > 2048) return;
    try {
      const row = JSON.parse(value);
      if (row?.version === 1 && typeof row.muted === 'boolean' && typeof row.identity === 'string' &&
        /^(server|certificate|notification):/.test(row.identity) && row.identity.length <= 100 &&
        typeof row.cycle === 'string' && row.cycle.length <= 100 && typeof row.label === 'string' && row.label.length <= 200) records[key] = row;
    } catch (_) { /* A damaged preference never hides an actual reminder. */ }
  });
  return records;
}

export function getReminderPreferences() {
  const owner = account();
  if (activeStore?.owner === owner) return activeStore;
  const listeners = new Set();
  let loading;
  const store = {owner, snapshot: {status: 'idle', records: {}, pending: false, error: ''},
    subscribe(listener) {listeners.add(listener); return () => listeners.delete(listener);},
    update(patch) {store.snapshot = {...store.snapshot, ...patch}; listeners.forEach(listener => listener(store.snapshot));},
    isMuted(item) {return Object.values(store.snapshot.records).some(row => row.muted && same(row, item));},
    async load(force = false) {
      if (loading) return loading;
      if (store.snapshot.pending) return;
      if (!force && store.snapshot.status === 'ready') return;
      store.update({status: 'loading', error: ''});
      loading = Promise.resolve().then(() => http.get(API)).then(response => {
        if (account() === owner) store.update({records: parse(response), status: 'ready', error: ''});
      }).catch(() => {if (account() === owner) store.update({status: 'error', error: '个人提醒设置读取失败，请重试。'});})
        .finally(() => {loading = null;});
      return loading;
    },
    async change(item, muted) {
      if (!item || account() !== owner || store.snapshot.pending || store.snapshot.status !== 'ready') throw new Error('Not ready');
      store.update({pending: true, error: ''});
      try {
        // Read current account state again so restoring also clears duplicates from another browser.
        const records = parse(await http.get(API));
        if (account() !== owner) throw new Error('Account changed');
        let keys = Object.keys(records).filter(key => same(records[key], item));
        if (muted && !keys.length) {
          keys = Object.keys(records).filter(key => records[key].identity === item.identity).slice(0, 1);
          if (!keys.length) {
            const bytes = new Uint8Array(12); window.crypto.getRandomValues(bytes);
            keys = [PREFIX + Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')];
          }
        }
        for (const key of keys) {
          if (account() !== owner) throw new Error('Account changed');
          const response = await http.post(API, {key, value: JSON.stringify({...item, version: 1, muted})});
          if (account() === owner) store.update({records: parse(response)});
        }
      } catch (_) {
        if (account() === owner) store.update({error: muted ? '不再提醒保存失败，请重试。' : '恢复提醒失败，请重试。'});
        throw new Error('Preference save failed');
      } finally {store.update({pending: false});}
    },
    mute(item) {return store.change(item, true);},
    restore(item) {return store.change(item, false);},
  };
  activeStore = store;
  return store;
}

export function usePersonalReminders(enabled = true) {
  const store = getReminderPreferences();
  const [snapshot, setSnapshot] = useState(store.snapshot);
  useEffect(() => {
    setSnapshot(store.snapshot);
    const unsubscribe = store.subscribe(setSnapshot);
    if (enabled) store.load();
    return unsubscribe;
  }, [store, enabled]);
  return {...snapshot, isMuted: store.isMuted, mute: store.mute, restore: store.restore, reload: () => store.load(true)};
}
