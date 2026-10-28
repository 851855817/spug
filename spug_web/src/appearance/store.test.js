import { createAppearanceStore } from './store';

const value=(preset='classic-blue',revision='a'.repeat(32))=>({version:1,preset,revision});
const response=(data,status=200)=>({ok:status===200,status,json:async()=>({data,error:''})});
let request,store;
beforeEach(()=>{localStorage.clear(); localStorage.setItem('token','fixture'); request=jest.fn(); store=createAppearanceStore(request);});

test('coalesces reads and accepts only a valid server theme', async()=>{
  let resolve; request.mockReturnValue(new Promise(done=>resolve=done));
  const one=store.refresh(),two=store.refresh(); expect(request).toHaveBeenCalledTimes(1);
  resolve(response(value('jade'))); await Promise.all([one,two]);
  expect(store.getSnapshot().current.preset).toBe('jade');
  expect(request.mock.calls[0][1].headers['X-Token']).toBeUndefined();
  request.mockResolvedValue(response(value('invalid'))); await store.refresh();
  expect(store.getSnapshot().current.preset).toBe('jade');
  expect(store.getSnapshot().error).toBeTruthy();
});
test('failed reads preserve last successful colors and a failed first read keeps the default', async()=>{
  request.mockRejectedValue(new Error('offline')); await store.refresh();
  expect(store.getSnapshot().current.preset).toBe('classic-blue');
  request.mockResolvedValue(response(value('night'))); await store.refresh();
  request.mockRejectedValue(new Error('offline')); await store.refresh();
  expect(store.getSnapshot().current.preset).toBe('night');
});
test('save uses expected revision and current credentials; failed save never changes effective theme',async()=>{
  request.mockResolvedValue(response(value())); await store.refresh();
  localStorage.setItem('token','current-fixture');
  request.mockResolvedValue({ok:false,status:409,json:async()=>({error:'配色已被其他管理员修改，请刷新后重试'})});
  await expect(store.save('night','a'.repeat(32))).rejects.toThrow('其他管理员');
  expect(store.getSnapshot().current.preset).toBe('classic-blue');
  const call=request.mock.calls[1]; expect(JSON.parse(call[1].body)).toEqual({preset:'night',revision:'a'.repeat(32)});
  expect(call[1].headers['X-Token']).toBe('current-fixture');
  request.mockResolvedValue(response(value('night','b'.repeat(32))));
  await store.save('night','a'.repeat(32)); expect(store.getSnapshot().current.preset).toBe('night');
});
test('a stale read finishing after a successful save cannot undo it', async()=>{
  let resolve; request.mockReturnValueOnce(new Promise(done=>resolve=done));
  const reading=store.refresh();
  request.mockResolvedValue(response(value('indigo','b'.repeat(32))));
  await store.save('indigo','default'); resolve(response(value())); await reading;
  expect(store.getSnapshot().current.preset).toBe('indigo');
});
test('cache does not trust arbitrary theme payloads or expose credentials',()=>{
  localStorage.setItem('kops:site-appearance:v1',JSON.stringify({...value(),preset:'bad',token:'secret-fixture'}));
  store=createAppearanceStore(request); expect(store.getSnapshot().current.preset).toBe('classic-blue');
});
