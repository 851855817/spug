// Shared across mounted monitor pages: even a remount cannot overlap an older request.
let browserSlot = Promise.resolve();

async function inBrowserSlot(operation) {
  const previous = browserSlot;
  let release;
  browserSlot = new Promise(resolve => {release = resolve;});
  await previous;
  try {return await operation();} finally {release();}
}

export function createMonitorSampler({sample, onSnapshot, onError, beforeRound, onPending = () => {}, onManualResult = () => {}}) {
  let generation = 0;
  let timer;
  let wake;
  const timing = {};
  const pending = new Map();
  function nextDue(key, interval, force) {
    const previous = timing[key];
    if (!previous) return 0;
    if (previous.sampledAt !== undefined) return force ? 0 : previous.sampledAt + interval * 1000;
    return previous.blockedUntil;
  }
  function stop() {
    generation++;
    for (const key of pending.keys()) onPending(key, false);
    pending.clear();
    clearTimeout(timer);
    if (wake) {const resolve = wake; wake = null; resolve();}
  }
  function collect(key, interval = 30, manual = true) {
    if (pending.has(key)) return pending.get(key);
    const version = generation;
    const current = () => version === generation;
    onPending(key, true);
    const operation = inBrowserSlot(async () => {
      if (!current()) return null;
      const remaining = nextDue(key, interval, manual) - Date.now();
      if (remaining > 0) return {key, retry_after: Math.ceil(remaining / 1000), retry_reason: timing[key].reason || 'cache', local: true};
      try {
        const result = await sample(key, interval);
        if (!current()) return null;
        const retry = Number.isFinite(result?.retry_after) ? result.retry_after : 0;
        const sampledAt = result?.snapshot?.sampled_at;
        const successfulAge = (result?.retry_reason === 'collected' || result?.retry_reason === 'cache') &&
          result?.snapshot?.status === 'ok' && typeof sampledAt === 'number' && Number.isFinite(sampledAt);
        // Successful age follows interval changes; busy and failure deadlines stay absolute.
        timing[key] = successfulAge ? {sampledAt: Math.min(Date.now(), sampledAt * 1000)}
          : {blockedUntil: retry > 0 ? Date.now() + retry * 1000 : 0, reason: result?.retry_reason};
        if (result && result.key === key) onSnapshot(key, result.snapshot);
        return result;
      } catch (_) {
        if (!current()) return null;
        timing[key] = {blockedUntil: Date.now() + 60000, reason: 'backoff'};
        onError(key);
        return {key, retry_after: 60, retry_reason: 'backoff'};
      }
    }).then(result => {
      if (current() && manual && result) onManualResult(key, result);
      return result;
    }).finally(() => {
      if (pending.get(key) === operation) {pending.delete(key); onPending(key, false);}
    });
    pending.set(key, operation);
    return operation;
  }
  function wait(delay) {
    return new Promise(resolve => {
      wake = resolve;
      timer = setTimeout(() => {wake = null; resolve();}, delay);
    });
  }
  function update({keys, interval, active, once = false, force = false, onComplete}) {
    stop();
    if (!active) return;
    const version = generation;
    const current = () => version === generation;
    const due = Object.fromEntries(keys.map(key => [key, nextDue(key, interval, force)]));
    async function run() {
      let first = true;
      while (current()) {
        if (!first && beforeRound) {
          try {await beforeRound();} catch (_) { /* Keep the last successful cards. */ }
        }
        first = false;
        for (const key of keys) {
          if (!current()) return;
          if (due[key] > Date.now()) continue;
          const result = await collect(key, interval, force);
          if (!current()) return;
          const retry = Number.isFinite(result?.retry_after) ? result.retry_after : 0;
          due[key] = Date.now() + (result?.local ? retry : Math.max(interval, retry)) * 1000;
          if (keys.length > 1) await wait(200);
        }
        if (!current()) return;
        if (onComplete) onComplete();
        if (once) return;
        // Empty visible results still need card discovery, with no sample calls.
        const delay = keys.length ? Math.min(...Object.values(due)) - Date.now() : interval * 1000;
        await wait(Math.max(1, delay));
      }
    }
    run();
  }
  return {update, stop, collect};
}
