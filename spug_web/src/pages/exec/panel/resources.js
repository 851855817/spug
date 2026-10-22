import { useEffect, useState } from 'react';
import { http } from 'libs';

// Every resource read, including reads from different expanded panels, shares this bound.
let active = 0;
const queue = [];
function completed() {active--; drain();}
function drain() {
  while (active < 2 && queue.length) {
    const entry = queue.shift();
    if (!entry.valid()) {entry.resolve(null); continue;}
    active++;
    Promise.resolve().then(entry.read).then(entry.resolve, entry.reject).finally(completed);
  }
}
export function readResource(params, valid = () => true) {
  return new Promise((resolve, reject) => {
    queue.push({read: () => http.get('/api/baota/resources/', {params}), valid, resolve, reject});
    drain();
  });
}

export function useResources(panelId, requests, enabled, onResource) {
  const [state, setState] = useState({data: {}, errors: {}, loading: false});
  const signature = JSON.stringify(requests);
  useEffect(() => {
    if (!enabled) return;
    let valid = true;
    setState({data: {}, errors: {}, loading: true});
    const selected = JSON.parse(signature);
    Promise.all(selected.map(async request => {
      const key = request.site_id ? `site:${request.site_id}` : request.kind;
      try {
        const result = await readResource({panel_id: panelId, ...request}, () => valid);
        if (!valid || !result) return;
        const data = request.kind === 'site_options' ? {[key]: result.site_options, compatibility: result.compatibility} : result;
        setState(value => ({...value, data: {...value.data, ...data}}));
        if (onResource) onResource(data);
      } catch (error) {
        if (valid) setState(value => ({...value, errors: {...value.errors, [key]: String(error || '资源读取失败')}}));
      }
    })).then(() => {if (valid) setState(value => ({...value, loading: false}));});
    return () => {valid = false;};
    // onResource is a notification callback; only selectors control a request generation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelId, signature, enabled]);
  return state;
}
