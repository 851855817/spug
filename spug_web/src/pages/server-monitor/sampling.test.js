import { createMonitorSampler } from './sampling';

const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
let samplers;
let now;
const advance = milliseconds => {now += milliseconds; jest.advanceTimersByTime(milliseconds);};
beforeEach(() => {jest.useFakeTimers(); now = 0; jest.spyOn(Date, 'now').mockImplementation(() => now); samplers = [];});
afterEach(async () => {samplers.forEach(sampler => sampler.stop()); await flush(); jest.restoreAllMocks(); jest.useRealTimers();});
function make(sample, onSnapshot = jest.fn()) {
  const sampler = createMonitorSampler({sample, onSnapshot, onError: jest.fn()});
  samplers.push(sampler); return sampler;
}

test('initial sampling is sequential and refresh never overlaps an existing browser request', async () => {
  let complete;
  const sample = jest.fn().mockImplementationOnce(() => new Promise(resolve => {complete = resolve;}))
    .mockResolvedValue({key: 'b', snapshot: null, retry_after: 0});
  const sampler = make(sample);
  sampler.update({keys: ['a', 'b'], interval: 60, active: true});
  await flush(); expect(sample.mock.calls).toEqual([['a', 60]]);
  sampler.update({keys: ['b'], interval: 30, active: true});
  await flush(); expect(sample).toHaveBeenCalledTimes(1);
  complete({key: 'a', snapshot: {status: 'ok'}, retry_after: 0});
  await flush(); advance(250); await flush();
  expect(sample.mock.calls).toEqual([['a', 60], ['b', 30]]);
});

test('stopping or changing visible cards discards old responses and sends no queued work', async () => {
  let complete;
  const sample = jest.fn().mockReturnValue(new Promise(resolve => {complete = resolve;}));
  const onSnapshot = jest.fn(); const sampler = make(sample, onSnapshot);
  sampler.update({keys: ['a', 'b'], interval: 60, active: true}); await flush();
  sampler.stop(); complete({key: 'a', snapshot: {status: 'ok'}, retry_after: 0}); await flush();
  advance(300000); await flush();
  expect(sample).toHaveBeenCalledTimes(1); expect(onSnapshot).not.toHaveBeenCalled();
});

test('server retry_after waits for a later round instead of polling busy responses', async () => {
  const sample = jest.fn().mockResolvedValue({key: 'a', snapshot: null, retry_after: 120});
  const sampler = make(sample);
  sampler.update({keys: ['a'], interval: 30, active: true}); await flush();
  advance(119999); await flush(); expect(sample).toHaveBeenCalledTimes(1);
  advance(1); await flush(); expect(sample).toHaveBeenCalledTimes(2);
});

test('one failed card does not stop later visible cards', async () => {
  const sample = jest.fn().mockRejectedValueOnce('network').mockResolvedValue({key: 'b', snapshot: null, retry_after: 0});
  const sampler = make(sample);
  sampler.update({keys: ['a', 'b'], interval: 300, active: true}); await flush();
  advance(250); await flush();
  expect(sample.mock.calls).toEqual([['a', 300], ['b', 300]]);
});

test('a successful 300-second response recomputes age after switching to thirty seconds', async () => {
  now = 1000000;
  const sample = jest.fn().mockImplementation((key, interval) => Promise.resolve({key,
    snapshot: {status: 'ok', sampled_at: now / 1000, attempted_at: now / 1000},
    retry_after: interval, retry_reason: 'collected'}));
  const sampler = make(sample);
  sampler.update({keys: ['a'], interval: 300, active: true}); await flush();
  advance(1000);
  sampler.update({keys: ['a'], interval: 30, active: true}); await flush();
  expect(sample.mock.calls).toEqual([['a', 300]]);
  advance(30000); await flush();
  expect(sample.mock.calls).toEqual([['a', 300], ['a', 30]]);
});

test('a cache response recomputes successful snapshot age when interval shortens', async () => {
  now = 1000000;
  const sample = jest.fn().mockResolvedValueOnce({key: 'a', snapshot: {status: 'ok', sampled_at: 990, attempted_at: 990},
    retry_after: 290, retry_reason: 'cache'}).mockImplementation((key, interval) => Promise.resolve({key,
      snapshot: {status: 'ok', sampled_at: now / 1000, attempted_at: now / 1000}, retry_after: interval, retry_reason: 'collected'}));
  const sampler = make(sample);
  sampler.update({keys: ['a'], interval: 300, active: true}); await flush();
  advance(1000); sampler.update({keys: ['a'], interval: 30, active: true}); await flush();
  expect(sample).toHaveBeenCalledTimes(1);
  advance(19000); await flush();
  expect(sample).toHaveBeenCalledTimes(2);
});

test.each(['busy', 'backoff'])('%s keeps its hard retry even when an old successful snapshot exists', async retry_reason => {
  now = 1000000;
  const sample = jest.fn().mockResolvedValue({key: 'a', snapshot: {status: retry_reason === 'busy' ? 'ok' : 'stale',
    sampled_at: 990, attempted_at: retry_reason === 'busy' ? 990 : 1000}, retry_after: 120, retry_reason});
  const sampler = make(sample);
  sampler.update({keys: ['a'], interval: 300, active: true}); await flush();
  advance(1000); sampler.update({keys: ['a'], interval: 30, active: true}); await flush();
  advance(118999); await flush(); expect(sample).toHaveBeenCalledTimes(1);
  advance(1); await flush(); expect(sample).toHaveBeenCalledTimes(2);
});

test('manual refresh may revalidate successful cache but cannot bypass a busy deadline', async () => {
  now = 1000000;
  const sample = jest.fn().mockResolvedValueOnce({key: 'a', snapshot: {status: 'ok', sampled_at: 1000, attempted_at: 1000},
    retry_after: 300, retry_reason: 'collected'}).mockResolvedValue({key: 'a',
    snapshot: {status: 'ok', sampled_at: 1000, attempted_at: 1000}, retry_after: 120, retry_reason: 'busy'});
  const sampler = make(sample);
  sampler.update({keys: ['a'], interval: 300, active: true}); await flush();
  advance(1000); sampler.update({keys: ['a'], interval: 300, active: true, force: true, once: true}); await flush();
  expect(sample).toHaveBeenCalledTimes(2);
  advance(1000); sampler.update({keys: ['a'], interval: 30, active: true, force: true, once: true}); await flush();
  expect(sample).toHaveBeenCalledTimes(2);
});

test('an active empty page rereads cards at its interval without collecting or busy looping', async () => {
  const sample = jest.fn(); const beforeRound = jest.fn().mockResolvedValue(undefined);
  const sampler = createMonitorSampler({sample, beforeRound, onSnapshot: jest.fn(), onError: jest.fn()});
  samplers.push(sampler);
  sampler.update({keys: [], interval: 60, active: true}); await flush();
  advance(59999); await flush(); expect(beforeRound).not.toHaveBeenCalled();
  advance(1); await flush(); expect(beforeRound).toHaveBeenCalledTimes(1);
  advance(60000); await flush(); expect(beforeRound).toHaveBeenCalledTimes(2);
  expect(sample).not.toHaveBeenCalled();
  sampler.stop(); advance(300000); await flush();
  expect(beforeRound).toHaveBeenCalledTimes(2);
});

test('manual requests for the same target share one operation, including an automatic operation', async () => {
  let complete;
  const sample = jest.fn().mockReturnValue(new Promise(resolve => {complete = resolve;}));
  const sampler = make(sample);
  sampler.update({keys: ['a'], interval: 60, active: true}); await flush();
  const first = sampler.collect('a');
  expect(sampler.collect('a')).toBe(first);
  expect(sample).toHaveBeenCalledTimes(1);
  complete({key: 'a', snapshot: {status: 'ok', sampled_at: 0}, retry_after: 60, retry_reason: 'collected'});
  await first;
  expect(sample).toHaveBeenCalledTimes(1);
});

test('a failed manual request keeps the shared backoff without blocking another target', async () => {
  const sample = jest.fn().mockRejectedValueOnce('network').mockResolvedValue({key: 'b', snapshot: null, retry_after: 0});
  const sampler = make(sample);
  const failure = await sampler.collect('a');
  expect(failure).toMatchObject({retry_reason: 'backoff', retry_after: 60});
  advance(1000);
  expect(await sampler.collect('a')).toMatchObject({retry_reason: 'backoff', retry_after: 59});
  await sampler.collect('b');
  expect(sample.mock.calls).toEqual([['a', 30], ['b', 30]]);
});

test('stop drops queued manual requests before they can contact another source', async () => {
  let complete;
  const sample = jest.fn().mockReturnValue(new Promise(resolve => {complete = resolve;}));
  const onSnapshot = jest.fn(); const sampler = make(sample, onSnapshot);
  const first = sampler.collect('a'); await flush();
  const second = sampler.collect('b'); await flush();
  sampler.stop();
  complete({key: 'a', snapshot: {status: 'ok'}, retry_after: 30, retry_reason: 'collected'});
  await Promise.all([first, second]);
  expect(sample).toHaveBeenCalledTimes(1);
  expect(onSnapshot).not.toHaveBeenCalled();
});

test('a manual sample moves the next automatic deadline without adding a whole extra interval', async () => {
  const sample = jest.fn().mockImplementation(key => Promise.resolve({key,
    snapshot: {status: 'ok', sampled_at: now / 1000}, retry_after: 30, retry_reason: 'collected'}));
  const sampler = make(sample);
  sampler.update({keys: ['a'], interval: 300, active: true}); await flush();
  advance(31000); await sampler.collect('a');
  advance(269000); await flush();
  expect(sample).toHaveBeenCalledTimes(2);
  advance(31000); await flush();
  expect(sample.mock.calls).toEqual([['a', 300], ['a', 30], ['a', 300]]);
});
