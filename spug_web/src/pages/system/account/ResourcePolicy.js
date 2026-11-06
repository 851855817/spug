import React, {useEffect, useState} from 'react';
import {Alert, InputNumber, Select, Spin} from 'antd';
import http from 'libs/http';
import {quotaText} from '../../../resources/access';

export function EffectivePreview({data}) {
  return <details><summary>当前已保存策略的最终生效预览</summary>
    <p>功能权限与资源级别同时生效；角色或分配修改后，保存并重新打开以核对最新结果。</p>
    <div>{data.is_supper ? '超级管理员：全局功能与资源' : (data.permissions || []).join('、') || '无功能权限'}</div>
    {['hosts', 'panels'].map(kind => <div key={kind}><b>{kind === 'hosts' ? 'SSH 主机' : '宝塔面板'}</b>
      {(data[kind] || []).map(row => <div key={row.id}>{row.name} #{row.id} · {row.level === 'manage' ? '可管理' : '只读'}
        {row.source === 'owned' ? ' · 本人资源' : row.source === 'assigned' ? ' · 管理员分配' : ''}</div>)}</div>)}
  </details>;
}

export default function ResourcePolicy({userId, onChange, onReady}) {
  const [data, setData] = useState(null), [error, setError] = useState(false);
  const [draft, setDraft] = useState({});
  useEffect(() => {
    let active = true;
    if (onReady) onReady(false);
    const request = userId ? http.get('/api/account/resources/', {params: {user_id: userId}}) : http.get('/api/account/resources/');
    request.then(value => {if (active) {setData(value); if (onReady) onReady(true);}}, () => {if (active) setError(true);});
    return () => {active = false;};
  }, [userId, onReady]);
  if (error) return <Alert type="error" message="资源策略读取失败，请关闭后重试。已有分配不会被清空。"/>;
  if (!data) return <Spin/>;
  const limit = Object.prototype.hasOwnProperty.call(draft, 'limit') ? draft.limit : userId ? data.quota.limit : 0;
  const grants = draft.grants || (userId ? data.grants || [] : []);
  function change(patch) {const next = {...draft, ...patch}; setDraft(next); onChange(next);}
  function grant(kind, id, level) {
    change({grants: [...grants.filter(row => row.kind !== kind || row.id !== id), ...(level ? [{kind, id, level}] : [])]});
  }
  return <section aria-label="账号资源策略" style={{margin: '12px 32px'}}>
    <h3>服务器额度与资源分配</h3>
    {userId && <p>{quotaText(data.quota)}</p>}
    <p>SSH 与宝塔共享额度；明确关联的同一台服务器计 1 台。新账号默认 0，选择“不限”才不设上限。分配不会授予对应模块功能权限。</p>
    <Select aria-label="额度模式" value={limit === null ? 'unlimited' : 'limited'} style={{width: 150}}
      onChange={mode => change({limit: mode === 'unlimited' ? null : 0})}
      options={[{value: 'limited', label: '限制服务器数量'}, {value: 'unlimited', label: '不限'}]}/>
    {limit !== null && <InputNumber aria-label="服务器上限" min={0} max={2147483647} precision={0} value={limit}
      onChange={value => {if (Number.isInteger(value) && value >= 0) change({limit: value});}}/>}
    <div style={{maxHeight: 260, overflow: 'auto', margin: '12px 0'}}>
      {['host', 'panel'].map(kind => (data[`available_${kind === 'host' ? 'hosts' : 'panels'}`] || []).map(row => <div key={`${kind}:${row.id}`} style={{display: 'flex', justifyContent: 'space-between', marginBottom: 8}}>
        <span>{kind === 'host' ? 'SSH' : '宝塔'} · {row.name} #{row.id}</span>
        <Select aria-label={`${kind}-${row.id}授权`} style={{width: 120}}
          value={grants.find(item => item.kind === kind && item.id === row.id)?.level || ''} onChange={level => grant(kind, row.id, level)}
          options={[{value: '', label: '不分配'}, {value: 'read', label: '只读'}, {value: 'manage', label: '可管理'}]}/>
      </div>))}
    </div>
    {userId && <EffectivePreview data={data}/>}
  </section>;
}
