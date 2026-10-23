import { useCallback, useEffect, useRef, useState } from 'react';
import { http } from 'libs';
import { createMonitorSampler } from './sampling';

export function usePageVisibility() {
  const [visible, setVisible] = useState(document.visibilityState !== 'hidden');
  useEffect(() => {
    const changed = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  return visible;
}

export default function useServerMonitor(allowed) {
  const [cards, setCards] = useState([]);
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  const mounted = useRef(false);
  const request = useRef(0);
  const readRef = useRef();
  const sampler = useRef();
  if (!sampler.current) sampler.current = createMonitorSampler({
    sample: (key, interval) => http.post('/api/baota/monitor/sample/', {key, interval}, {timeout: 45000}),
    onSnapshot: (key, snapshot) => {
      if (mounted.current) setCards(state => state.map(card => card.key === key ? {...card, snapshot} : card));
    },
    onError: key => {
      if (mounted.current) setCards(state => state.map(card => card.key === key ? {...card, snapshot: {
        ...card.snapshot, status: card.snapshot?.sampled_at ? 'stale' : 'error',
        message: '采集失败，请稍后刷新', attempted_at: Date.now() / 1000,
      }} : card));
    },
    beforeRound: () => readRef.current(false),
  });

  const refresh = useCallback(async (manual = true) => {
    if (!allowed || !mounted.current) return;
    const version = ++request.current;
    if (manual) {sampler.current.stop(); setLoading(true);}
    try {
      const result = await http.get('/api/baota/monitor/cards/');
      if (!mounted.current || version !== request.current) return;
      setCards(result.cards);
      setReady(true);
      setError(false);
      return true;
    } catch (_) {
      if (mounted.current && version === request.current) setError(true);
      return false;
    } finally {
      if (mounted.current && version === request.current && manual) {
        setRevision(value => value + 1);
        setLoading(false);
      }
    }
  }, [allowed]);
  readRef.current = refresh;

  useEffect(() => {
    mounted.current = true;
    refresh();
    const activeSampler = sampler.current;
    return () => {mounted.current = false; activeSampler.stop();};
  }, [refresh]);

  return {cards, ready, loading, error, revision, refresh, sampler: sampler.current};
}
