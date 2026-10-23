// Shared across mounted monitor pages: even a remount cannot overlap an older request.
let browserSlot = Promise.resolve();

async function inBrowserSlot(operation) {
  const previous = browserSlot;
  let release;
  browserSlot = new Promise(resolve => {release = resolve;});
  await previous;
  try {return await operation();} finally {release();}
}

export function createMonitorSampler({sample, onSnapshot, onError, beforeRound}) {
  let generation = 0;
  let timer;
  let wake;
  const timing = {};
  function nextDue(key, interval, force) {
    const previous = timing[key];
    if (!previous) return 0;
    if (previous.sampledAt !== undefined) return force ? 0 : previous.sampledAt + interval * 1000;
    return previous.blockedUntil;
  }
  function stop() {
    generation++;
    clearTimeout(timer);
    if (wake) {const resolve = wake; wake = null; resolve();}
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
          try {
            const result = await inBrowserSlot(() => current() ? sample(key, interval) : null);
            if (!current()) return;
            const retry = Number.isFinite(result?.retry_after) ? result.retry_after : 0;
            const sampledAt = result?.snapshot?.sampled_at;
            const successfulAge = (result?.retry_reason === 'collected' || result?.retry_reason === 'cache') &&
              result?.snapshot?.status === 'ok' && typeof sampledAt === 'number' && Number.isFinite(sampledAt);
            due[key] = Date.now() + Math.max(interval, retry) * 1000;
            // Successful cache age follows a changed interval. A lease or failure
            // deadline is absolute, including when it returns an older ok snapshot.
            timing[key] = successfulAge ? {sampledAt: Math.min(Date.now(), sampledAt * 1000)}
              : {blockedUntil: retry > 0 ? Date.now() + retry * 1000 : 0};
            if (result && result.key === key) onSnapshot(key, result.snapshot);
          } catch (_) {
            if (!current()) return;
            due[key] = Date.now() + Math.max(60, interval) * 1000;
            timing[key] = {blockedUntil: Date.now() + 60000};
            onError(key);
          }
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
  return {update, stop};
}
