import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Modal, Select, Switch } from 'antd';
import { hasPermission, http } from 'libs';
import ResizableTable from '../../components/ResizableTable';
import { API, dateUTC8, sourceDefaults, text } from './helpers';
import styles from './index.module.less';

export default function SourceSettings({onClose, onSaved}) {
  const [items, setItems] = useState([]), [loading, setLoading] = useState(true), [saving, setSaving] = useState(false);
  const [error, setError] = useState(''), [query, setQuery] = useState(''), [revision, setRevision] = useState(0);
  const [reauthorize, setReauthorize] = useState(new Set());
  const mounted = useRef(false), pending = useRef(false), originals = useRef(new Map());
  useEffect(() => {
    mounted.current = true;
    return () => {mounted.current = false;};
  }, []);
  useEffect(() => {
    if (!hasPermission('admin')) return;
    let valid = true;
    setLoading(true); setError('');
    http.get(`${API}sources/`).then(result => {if (valid) {
      const sources = (result.items || []).map(sourceDefaults);
      originals.current = new Map(sources.map(source => [source.key, source])); setItems(sources); setReauthorize(new Set());
    }},
      () => {if (valid) setError('读取来源配置失败，请重新读取后再保存。');}).finally(() => {if (valid) setLoading(false);});
    return () => {valid = false;};
  }, [revision]);
  function update(key, patch) {setItems(current => current.map(item => item.key === key ? {...item, ...patch} : item));}
  async function save() {
    if (pending.current || loading || !items.length || !hasPermission('admin')) return;
    pending.current = true; setSaving(true); setError('');
    try {
      for (const item of items) {
        if (!mounted.current || !hasPermission('admin')) return;
        const original = originals.current.get(item.key);
        if (!reauthorize.has(item.key) && original && ['enabled', 'reminders', 'interval_hours'].every(key => original[key] === item[key])) continue;
        const {key, enabled, reminders, interval_hours, revision} = item;
        await http.post(`${API}sources/`, {key, revision, enabled, reminders, interval_hours});
      }
      if (mounted.current) {onSaved(); onClose();}
    } catch (_) {
      if (mounted.current) setError('保存未全部完成，可能有来源配置已变化。请重新读取后核对并保存。');
    } finally {pending.current = false; if (mounted.current) setSaving(false);}
  }
  const columns = [
    {title: '服务器 / 来源', key: 'source', width: 240, render: (_, row) => <><b>{row.name}</b><small className={styles.secondary}>{row.kind === 'panel' ? '宝塔面板' : 'SSH 主机'} · {row.address || '地址未登记'}</small></>},
    {title: '启用巡检', key: 'enabled', width: 105, render: (_, row) => <Switch aria-label={`启用 ${row.name}`} checked={row.enabled} disabled={saving} onChange={enabled => update(row.key, {enabled})}/>},
    {title: '到期提醒', key: 'reminders', width: 105, render: (_, row) => <Switch aria-label={`${row.name} 到期提醒`} checked={row.reminders} disabled={saving || !row.enabled} onChange={reminders => update(row.key, {reminders})}/>},
    {title: '巡检频率', key: 'interval', width: 150, render: (_, row) => <Select aria-label={`${row.name} 巡检频率`} value={row.interval_hours} disabled={saving}
      options={[1, 6, 12, 24].map(value => ({value, label: `每 ${value} 小时`}))} onChange={value => update(row.key, {interval_hours: Number(value)})}/>},
    {title: '最近成功 / 来源状态', key: 'last', width: 220, render: (_, row) => <><div>{dateUTC8(row.last_success_at)}</div><small>{text(row.message || '尚未巡检')}</small>
      {row.enabled && ['disabled', 'invalidated'].includes(row.status) && <div><Button size="small" disabled={saving || reauthorize.has(row.key)}
        onClick={() => setReauthorize(current => new Set([...current, row.key]))}>{reauthorize.has(row.key) ? '保存后重新授权' : '重新授权'}</Button></div>}</>},
  ];
  return <Modal visible title="巡检来源设置" width={1000} onCancel={saving ? undefined : onClose} footer={<>
    <Button disabled={saving} onClick={onClose}>取消</Button><Button disabled={saving} onClick={() => setRevision(value => value + 1)}>重新读取</Button>
    <Button type="primary" loading={saving} disabled={loading || !!error || !items.length} onClick={save}>保存配置</Button>
  </>}>
    <Alert type="info" showIcon message="来源默认关闭，每 6 小时巡检一次。"
      description="勾选对应服务器并保存后开启巡检；保存配置本身不立即连接服务器。到期提醒需单独开启，来源变更后旧结果会标记失效。"/>
    {error && <Alert type="error" message={error}/>}
    <Input aria-label="搜索来源" value={query} placeholder="搜索主机或宝塔面板" onChange={event => setQuery(event.target.value)} style={{margin: '16px 0'}}/>
    <ResizableTable resizeKey="ssl-sources" rowKey="key" columns={columns} loading={loading} pagination={false}
      scroll={{x: 920}} dataSource={items.filter(item => `${item.name} ${item.address}`.toLowerCase().includes(query.toLowerCase()))}/>
  </Modal>;
}
