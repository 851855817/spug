// Server-owned appearance; no per-account overrides. K, 2026-10-28, AGPL-3.0.
import { validPreset } from './presets';
const CACHE='kops:site-appearance:v1';
const DEFAULT={version:1,preset:'classic-blue',revision:'default'};
const PUBLIC='/api/setting/appearance/public/';
const ADMIN='/api/setting/appearance/';

function validated(value) {
  if(!value || value.version!==1 || !validPreset(value.preset) ||
    !(value.revision==='default' || /^[0-9a-f]{32}$/.test(value.revision))) throw new Error('配色数据无效，请重试');
  return {version:1,preset:value.preset,revision:value.revision};
}
function cached() {
  try {return validated(JSON.parse(localStorage.getItem(CACHE)));} catch(_) {return {...DEFAULT};}
}
export function createAppearanceStore(request=(...args)=>window.fetch(...args)) {
  let state={current:cached(),loaded:false,error:'',saving:false};
  let pending=null, epoch=0;
  const listeners=new Set();
  function update(changes) {state={...state,...changes};listeners.forEach(fn=>fn(state));}
  function accept(current) {
    try {localStorage.setItem(CACHE,JSON.stringify(current));} catch(_) { /* Storage is optional. */ }
    update({current,loaded:true,error:''});
  }
  async function fetchValue(url,options) {
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),10000);
    try {
      const response=await request(url,{cache:'no-store',credentials:'same-origin',...options,signal:controller.signal});
      const body=await response.json();
      if(!response.ok || body.error) throw new Error(typeof body.error==='string' && body.error.length<=160
        ? body.error : '配色请求失败，请重试');
      return validated(body.data);
    } finally {clearTimeout(timeout);}
  }
  const store={
    getSnapshot:()=>state,
    subscribe:listener=>{listeners.add(listener);return ()=>listeners.delete(listener);},
    refresh() {
      if(pending) return pending;
      const version=epoch;
      pending=fetchValue(PUBLIC,{method:'GET',headers:{Accept:'application/json'}})
        .then(current=>{if(version===epoch) accept(current);})
        .catch(()=>{if(version===epoch) update({error:'暂时无法同步配色，已保留上次配色'});})
        .finally(()=>{pending=null;});
      return pending;
    },
    async save(preset,revision) {
      if(!validPreset(preset)) throw new Error('请选择有效配色模板');
      if(state.saving) throw new Error('正在保存，请稍候');
      const token=localStorage.getItem('token');
      if(!token) throw new Error('请先登录管理员账号');
      update({saving:true});
      try {
        const current=await fetchValue(ADMIN,{method:'POST',headers:{'Content-Type':'application/json','X-Token':token},
          body:JSON.stringify({preset,revision})});
        epoch+=1; accept(current); return current;
      } catch(error) {
        throw new Error(error && error.name!=='AbortError' && error.message ? error.message : '保存失败，请重试');
      } finally {update({saving:false});}
    },
  };
  return store;
}
export const appearanceStore=createAppearanceStore();
