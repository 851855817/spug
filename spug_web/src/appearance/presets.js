// Shared site palettes by K, 2026-10-28. Released under AGPL-3.0.
const light = {
  surface:'#FFFFFF', raised:'#F8FAFC', text:'#172B4D', muted:'#53617A', border:'#CBD5E1',
  subtle:'#F1F5F9', onPrimary:'#FFFFFF', sidebarText:'#C9D7E7', sidebarActive:'#FFFFFF',
  success:'#166534', successBg:'#F0FDF4', warning:'#92400E', warningBg:'#FFFBEB',
  error:'#B91C1C', errorBg:'#FEF2F2', disabled:'#64748B', shadow:'#102A4310',
};
export const PRESETS = [
  {id:'classic-blue',name:'经典蓝',description:'清晰明快的默认配色',mode:'light',
    colors:{...light,primary:'#2563FC',primaryHover:'#1D4ED8',page:'#F0F2F5',sidebar:'#101F35',selected:'#E8EFFF'}},
  {id:'jade',name:'青碧',description:'柔和青绿，适合日常浏览',mode:'light',
    colors:{...light,primary:'#0F766E',primaryHover:'#115E59',page:'#F2F7F5',sidebar:'#102E2B',text:'#16352F',selected:'#E0F2ED'}},
  {id:'indigo',name:'靛紫',description:'沉静紫调，突出操作重点',mode:'light',
    colors:{...light,primary:'#6D28D9',primaryHover:'#5B21B6',page:'#F5F3FA',sidebar:'#251A3C',text:'#29203E',selected:'#EEE8FC'}},
  {id:'graphite',name:'石墨灰',description:'低饱和配色，专注运维数据',mode:'light',
    colors:{...light,primary:'#475569',primaryHover:'#334155',page:'#F1F3F5',sidebar:'#1E293B',text:'#1E293B',selected:'#E7ECF2'}},
  {id:'night',name:'深色夜间',description:'降低大面积亮度，适合夜间值班',mode:'dark',colors:{
    primary:'#60A5FA',primaryHover:'#93C5FD',onPrimary:'#101827',page:'#101827',surface:'#172234',raised:'#1E2C42',
    text:'#E5EDF7',muted:'#A8B5CA',border:'#526178',subtle:'#223149',selected:'#243D5D',
    sidebar:'#0B1320',sidebarText:'#C9D7E7',sidebarActive:'#101827',
    success:'#86EFAC',successBg:'#143029',warning:'#FCD34D',warningBg:'#352C1B',
    error:'#FCA5A5',errorBg:'#3B222C',disabled:'#94A3B8',shadow:'#000000',
  }},
];
export const presetFor = id => PRESETS.find(row=>row.id===id) || PRESETS[0];
export const validPreset = id => PRESETS.some(row=>row.id===id);
export function themeVariables(id) {
  const colors=presetFor(id).colors;
  return Object.fromEntries(Object.entries(colors).map(([key,value])=>[
    '--kops-'+key.replace(/[A-Z]/g,char=>'-'+char.toLowerCase()),value]));
}
