import { useCallback, useEffect, useRef, useState } from 'react';
import { http } from 'libs';

export default function useResourceMonitorSources(kind, revision, enabled = true) {
  const [sources, setSources] = useState({});
  const [saving, setSaving] = useState({});
  const [errors, setErrors] = useState({});
  const [loadError, setLoadError] = useState(false);
  const mounted = useRef(false);
  const readVersion = useRef(0);
  const pending = useRef(new Set());

  useEffect(() => {
    mounted.current = true;
    return () => {mounted.current = false;};
  }, []);

  useEffect(() => {
    const version = ++readVersion.current;
    if (!enabled) return;
    http.get('/api/baota/monitor/sources/', {params: {kind}}).then(result => {
      if (!mounted.current || version !== readVersion.current) return;
      setSources(Object.fromEntries(result.sources.map(source => [source.id, source])));
      setLoadError(false);
    }).catch(() => {
      if (mounted.current && version === readVersion.current) setLoadError(true);
    });
    return () => {
      // This is a request generation counter, not a rendered DOM ref.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      readVersion.current++;
    };
  }, [kind, revision, enabled]);

  const save = useCallback(async (id, value) => {
    if (!enabled || pending.current.has(id)) return;
    pending.current.add(id);
    // An older list read must never overwrite a newer persisted setting.
    readVersion.current++;
    setSaving(state => ({...state, [id]: true}));
    setErrors(state => ({...state, [id]: false}));
    try {
      const source = await http.post('/api/baota/monitor/settings/', {kind, id, enabled: value});
      if (mounted.current) setSources(state => ({...state, [id]: source}));
    } catch (_) {
      if (mounted.current) setErrors(state => ({...state, [id]: true}));
    } finally {
      pending.current.delete(id);
      if (mounted.current) setSaving(state => ({...state, [id]: false}));
    }
  }, [kind, enabled]);

  return {sources, saving, errors, loadError, save, enabled};
}
