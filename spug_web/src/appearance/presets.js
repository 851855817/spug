// Shared site palettes by K, 2026-10-28. Released under AGPL-3.0.
const light = {
  surface:'#FFFFFF', raised:'#F8FAFC', text:'#172B4D', muted:'#53617A', border:'#CBD5E1',
  subtle:'#F1F5F9', borderSoft:'#E2E8F0', onPrimary:'#FFFFFF', sidebarText:'#C9D7E7', sidebarActive:'#FFFFFF',
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
    text:'#E5EDF7',muted:'#A8B5CA',border:'#526178',borderSoft:'#3B4A60',subtle:'#223149',selected:'#243D5D',
    sidebar:'#0B1320',sidebarText:'#C9D7E7',sidebarActive:'#101827',
    success:'#86EFAC',successBg:'#143029',warning:'#FCD34D',warningBg:'#352C1B',
    error:'#FCA5A5',errorBg:'#3B222C',disabled:'#94A3B8',shadow:'#000000',
  }},
  {id:'obsidian-gold',name:'曜石金',description:'炭黑底色 · 哑光香槟金',mode:'dark',collection:'refined',colors:{
    primary:'#D6BA83',primaryHover:'#E6CCA0',onPrimary:'#151719',page:'#121416',surface:'#1B1D20',raised:'#23262A',
    text:'#EEEAE2',muted:'#B8B0A1',border:'#56524B',borderSoft:'#3C3A36',subtle:'#2B2D30',selected:'#39342A',
    sidebar:'#0E1012',sidebarText:'#CEC8BE',sidebarActive:'#151719',
    success:'#96D9B0',successBg:'#1B3025',warning:'#E8CA89',warningBg:'#352D20',
    error:'#EFA5A5',errorBg:'#392527',disabled:'#A19C92',shadow:'#000000',
  }},
  {id:'midnight-teal',name:'深海青',description:'深海蓝灰 · 沉静雾青',mode:'dark',collection:'refined',colors:{
    primary:'#75C8BD',primaryHover:'#9BDDD3',onPrimary:'#0F1E24',page:'#0F1E24',surface:'#15272D',raised:'#1C3239',
    text:'#E0EFEE',muted:'#A6BEBD',border:'#48646A',borderSoft:'#334D53',subtle:'#243C43',selected:'#214640',
    sidebar:'#0A171C',sidebarText:'#BED5D3',sidebarActive:'#0F1E24',
    success:'#91DBAB',successBg:'#18342B',warning:'#E9CE8C',warningBg:'#343120',
    error:'#F0A9A9',errorBg:'#3A282E',disabled:'#91AAAA',shadow:'#000000',
  }},
  {id:'moonlit-violet',name:'月影紫',description:'墨紫灰底 · 柔和月光紫',mode:'dark',collection:'refined',colors:{
    primary:'#BCACE2',primaryHover:'#D2C5EE',onPrimary:'#1B1924',page:'#1B1924',surface:'#252330',raised:'#302D3B',
    text:'#ECE8F4',muted:'#B9B1C8',border:'#625B72',borderSoft:'#474151',subtle:'#373244',selected:'#40364F',
    sidebar:'#15131D',sidebarText:'#CFC5DF',sidebarActive:'#1B1924',
    success:'#9AD9AF',successBg:'#22332B',warning:'#EACD91',warningBg:'#383022',
    error:'#EFA8B0',errorBg:'#402930',disabled:'#A69DB5',shadow:'#000000',
  }},
  {id:'porcelain',name:'暖瓷白',description:'温润瓷白 · 摩卡棕调',mode:'light',collection:'refined',colors:{...light,
    primary:'#795B46',primaryHover:'#624735',page:'#F2EFEA',surface:'#FFFDF9',raised:'#F7F4EF',
    text:'#38322D',muted:'#686157',border:'#D4CCC1',borderSoft:'#E6DED3',subtle:'#EFE9E1',selected:'#EDE2D5',
    sidebar:'#2B2621',sidebarText:'#DED5C8',shadow:'#30271912',
  }},
  {id:'pine-mist',name:'雾松绿',description:'雾白纸感 · 低饱和松绿',mode:'light',collection:'refined',colors:{...light,
    primary:'#426858',primaryHover:'#315344',page:'#EDF2EF',surface:'#FCFEFC',raised:'#F2F6F2',
    text:'#23392E',muted:'#57665B',border:'#C6D2C8',borderSoft:'#DBE4DD',subtle:'#E6EEE8',selected:'#DEEAE2',
    sidebar:'#182D25',sidebarText:'#C8D9CE',shadow:'#17362712',
  }},
  {id:'champagne',name:'香槟砂',description:'浅砂岩底 · 克制古铜金',mode:'light',collection:'refined',colors:{...light,
    primary:'#80623A',primaryHover:'#654D2E',page:'#F3F0E8',surface:'#FFFEFA',raised:'#F8F5EC',
    text:'#393429',muted:'#6B6251',border:'#D8CFBC',borderSoft:'#E7E0D2',subtle:'#F0EADC',selected:'#EBE1CA',
    sidebar:'#2C291F',sidebarText:'#E0D7C3',shadow:'#39301812',
  }},
];
export const presetFor = id => PRESETS.find(row=>row.id===id) || PRESETS[0];
export const validPreset = id => PRESETS.some(row=>row.id===id);
const nightAccents=['#1B2B46','#233A5C','#2D496F','#385D8D','#4677B5','#60A5FA','#7FB2EB','#ABD1F5','#D5E9F9','#ECF5FB'];
function mix(first,second,weight) {
  return '#'+[1,3,5].map(offset=>Math.round(parseInt(first.slice(offset,offset+2),16)*weight+
    parseInt(second.slice(offset,offset+2),16)*(1-weight)).toString(16).padStart(2,'0')).join('').toUpperCase();
}
export function themeVariables(id) {
  const preset=presetFor(id),colors=preset.colors;
  const variables=Object.fromEntries(Object.entries(colors).map(([key,value])=>[
    '--kops-'+key.replace(/[A-Z]/g,char=>'-'+char.toLowerCase()),value]));
  // Complete on every switch: returning to an older preset must not retain new accents.
  const accents=preset.id==='night' ? nightAccents : [
    ...[.15,.25,.3,.45,.65,1].map(weight=>mix(colors.primary,colors.surface,weight)),
    ...[.15,.3,.5,.7].map(weight=>mix('#FFFFFF',colors.primary,weight)),
  ];
  accents.forEach((value,index)=>{variables['--kops-dark-primary-'+(index+1)]=value;});
  variables['--kops-focus-ring']=colors.primary+'33';
  variables['--kops-focus-subtle']=colors.primary+'1F';
  variables['--kops-accent-hover']=preset.id==='night' ? '#4681D4' : colors.primaryHover;
  variables['--kops-accent-active']=preset.id==='night' ? '#8AC2FF' : colors.primaryHover;
  variables['--kops-range-border']=preset.id==='night' ? '#076EED' : colors.primary;
  variables['--kops-range-hover']=preset.id==='night' ? '#054CA2' : colors.selected;
  return variables;
}
