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
  const [collecting, setCollecting] = useState({});
  const [collectionResult, setCollectionResult] = useState({});
  const mounted = useRef(false);
  const request = useRef(0);
  const snapshotVersion = useRef(0);
  const snapshotUpdates = useRef({});
  const readRef = useRef();
  const sampler = useRef();
  if (!sampler.current) sampler.current = createMonitorSampler({
    sample: (key, interval) => http.post('/api/baota/monitor/sample/', {key, interval}, {timeout: 45000}),
    onSnapshot: (key, snapshot) => {
      snapshotUpdates.current[key] = ++snapshotVersion.current;
      if (mounted.current) setCards(state => state.map(card => card.key === key ? {...card, snapshot} : card));
    },
    onError: key => {
      snapshotUpdates.current[key] = ++snapshotVersion.current;
      if (mounted.current) setCards(state => state.map(card => card.key === key ? {...card, snapshot: {
        ...card.snapshot, status: card.snapshot?.sampled_at ? 'stale' : 'error',
        message: '采集失败，请稍后刷新', attempted_at: Date.now() / 1000,
      }} : card));
    },
    beforeRound: () => readRef.current(false),
    onPending: (key, value) => {
      if (mounted.current) setCollecting(state => ({...state, [key]: value}));
    },
    onManualResult: (key, result) => {
      const retry = Math.max(1, Math.ceil(result.retry_after || 30));
      const text = result.retry_reason === 'cache' ? `已显示缓存，${retry} 秒后可再采集`
        : result.retry_reason === 'busy' ? `采集繁忙，${retry} 秒后重试`
          : result.retry_reason === 'backoff' ? `采集暂缓，${retry} 秒后重试`
            : result.snapshot?.status === 'ok' ? '采集完成' : '采集失败，请检查来源配置';
      if (mounted.current) setCollectionResult(state => ({...state, [key]: text}));
    },
  });

  const refresh = useCallback(async (manual = true) => {
    if (!allowed || !mounted.current) return;
    const version = ++request.current;
    const readSnapshotVersion = snapshotVersion.current;
    if (manual) {sampler.current.stop(); setLoading(true);}
    try {
      const result = await http.get('/api/baota/monitor/cards/');
      if (!mounted.current || version !== request.current) return;
      setCards(state => result.cards.map(card => {
        const previous = state.find(item => item.key === card.key);
        // A list request can start before a targeted sample finishes. Preserve
        // the newer attempt only while the source configuration is unchanged.
        const sameSource = previous && JSON.stringify(previous.sources) === JSON.stringify(card.sources) &&
          previous.linked_host_id === card.linked_host_id;
        const oldAttempt = previous?.snapshot?.attempted_at || previous?.snapshot?.sampled_at || 0;
        const newAttempt = card.snapshot?.attempted_at || card.snapshot?.sampled_at || 0;
        return sameSource && snapshotUpdates.current[card.key] > readSnapshotVersion && oldAttempt > newAttempt
          ? {...card, snapshot: previous.snapshot} : card;
      }));
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

  return {cards, ready, loading, error, revision, refresh, collecting, collectionResult, sampler: sampler.current};
}
