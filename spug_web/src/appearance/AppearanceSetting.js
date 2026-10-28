// Shared appearance settings by K, 2026-10-28. Released under AGPL-3.0.
import React, {useEffect,useState} from 'react';
import {Alert,Button,Modal,message} from 'antd';
import {PRESETS,presetFor,themeVariables} from './presets';
import {appearanceStore} from './store';
import {useAppearance} from './Provider';
import styles from './appearance.module.css';

export default function AppearanceSetting({store=appearanceStore}) {
  const state=useAppearance(store);
  const [draft,setDraft]=useState(null),[error,setError]=useState('');
  useEffect(()=>{store.refresh();},[store]);
  const current=presetFor(state.current.preset),selected=presetFor(draft ? draft.preset : current.id);
  async function apply(preset=selected.id,revision=draft ? draft.revision : state.current.revision) {
    setError('');
    try {await store.save(preset,revision);setDraft(null);message.success('配色已应用全站');}
    catch(error) {setError(error.message);throw error;}
  }
  function restore() {
    const revision=state.current.revision;
    Modal.confirm({title:'恢复经典蓝并应用全站？',content:'所有账号将统一使用经典蓝配色。',
      okText:'恢复并应用',cancelText:'取消',onOk:()=>apply('classic-blue',revision)});
  }
  function reload() {setDraft(null);setError('');store.refresh();}
  return <section aria-label="外观配色" className={styles.settings}>
    <h2>外观配色</h2>
    <p className={styles.subtitle}>管理员统一设置，所有账号使用相同配色。选择模板可预览，应用后全站生效。</p>
    <div className={styles.current}>当前生效：{current.name}</div>
    {(error||state.error) && <Alert type="error" showIcon message={error||state.error}
      action={<Button onClick={reload} disabled={state.saving}>重新载入</Button>}/>}
    <fieldset className={styles.choices} disabled={!state.loaded||state.saving}>
      <legend>选择配色模板</legend>
      <div className={styles.grid}>{PRESETS.map(preset=><label key={preset.id}
        className={`${styles.choice} ${selected.id===preset.id ? styles.selected : ''}`}>
        <div className={styles.swatches} aria-hidden="true">
          <i style={{background:preset.colors.sidebar}}/><i style={{background:preset.colors.primary}}/>
          <i style={{background:preset.colors.page}}/><i style={{background:preset.colors.surface}}/>
        </div>
        <div className={styles.choiceName}><input type="radio" name="site-palette" value={preset.id} checked={selected.id===preset.id}
          onChange={()=>{setDraft({preset:preset.id,revision:state.current.revision});setError('');}}/>
          <span>{preset.name}</span>{current.id===preset.id && <small>使用中</small>}</div>
        <p>{preset.description}</p>
      </label>)}</div>
    </fieldset>
    <div className={styles.previewTitle}>配色预览 <small>示例数据</small></div>
    <div className={styles.preview} style={themeVariables(selected.id)} aria-label={`${selected.name}配色预览`}>
      <aside><b>KOps · 云枢</b><span>首页</span><span className={styles.active}>服务器监控</span><span>批量执行</span><span>系统管理</span></aside>
      <div className={styles.previewMain}>
        <header>运维工作台 <span>{selected.name}</span></header>
        <div className={styles.previewBody}>
          <div className={styles.demoCard}><div className={styles.demoHeading}><b>示例主机</b><span className={styles.good}>正常</span></div>
            <p>CPU 使用率 <strong>24%</strong></p><div className={styles.meter}><i/></div>
            <div className={styles.demoMuted}>内存 2.4 / 8 GB</div></div>
          <div className={styles.demoTable}><div><b>运维任务</b><b>结果</b></div>
            <div><span>基础巡检</span><span className={styles.good}>正常</span></div>
            <div><span>证书检查</span><span className={styles.warn}>即将到期</span></div>
            <div><span>连接检查</span><span className={styles.bad}>异常</span></div></div>
          <div className={styles.demoFooter}><span>查看执行记录</span><span className={styles.demoButton}>查看详情</span></div>
        </div>
      </div>
    </div>
    <div className={styles.actions}>
      <Button type="primary" loading={state.saving} disabled={!state.loaded||selected.id===current.id}
        onClick={()=>apply().catch(()=>{})}>应用全站</Button>
      <Button disabled={!state.loaded||state.saving||current.id==='classic-blue'} onClick={restore}>恢复经典蓝</Button>
      {draft && <Button disabled={state.saving} onClick={()=>{setDraft(null);setError('');}}>取消选择</Button>}
    </div>
    <p className={styles.subtitle}>保存后当前页面立即更新，其他已打开页面将在一分钟内同步。正常、告警和异常状态保留各自含义。</p>
  </section>;
}
