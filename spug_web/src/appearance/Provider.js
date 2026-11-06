// Root appearance synchronization by K, 2026-10-28. Released under AGPL-3.0.
import { useEffect, useState } from 'react';
import { ConfigProvider } from 'antd';
import { appearanceStore } from './store';
import { presetFor, themeVariables } from './presets';

export function useAppearance(store=appearanceStore) {
  const [state,setState]=useState(store.getSnapshot);
  useEffect(()=>{const unsubscribe=store.subscribe(setState);setState(store.getSnapshot());return unsubscribe;},[store]);
  return state;
}
export default function AppearanceProvider({children,store=appearanceStore}) {
  const {current}=useAppearance(store);
  useEffect(()=>{
    const preset=presetFor(current.preset),root=document.documentElement;
    root.dataset.kopsTheme=preset.id;root.dataset.kopsMode=preset.mode;
    root.style.colorScheme=preset.mode;
    for(const [key,value] of Object.entries(themeVariables(preset.id))) root.style.setProperty(key,value);
    ConfigProvider.config({theme:{primaryColor:preset.colors.primary,infoColor:preset.colors.primary}});
  },[current.preset]);
  useEffect(()=>{
    let timer;
    const restart=()=>{
      clearInterval(timer);
      if(document.visibilityState!=='hidden') {store.refresh();timer=setInterval(()=>store.refresh(),60000);}
    };
    const focus=()=>{if(document.visibilityState!=='hidden')store.refresh();};
    restart();document.addEventListener('visibilitychange',restart);window.addEventListener('focus',focus);
    return ()=>{clearInterval(timer);document.removeEventListener('visibilitychange',restart);window.removeEventListener('focus',focus);};
  },[store]);
  return children;
}
