import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox } from 'antd';
import { hasPermission, http } from 'libs';
import { usePageVisibility } from '../server-monitor/useServerMonitor';
import { API, dateUTC8, text } from './helpers';
import styles from './index.module.less';

export default function Reminders({revision: externalRevision = 0, onChanged}) {
  const allowed = hasPermission('admin'), visible = usePageVisibility();
  const [data, setData] = useState(null), [error, setError] = useState(''), [muted, setMuted] = useState(false);
  const [page, setPage] = useState(1), [revision, setRevision] = useState(0), [pending, setPending] = useState(null);
  const mounted = useRef(false), locked = useRef(false), owner = useRef(localStorage.getItem('id'));
  const sameOwner = () => owner.current === localStorage.getItem('id') && hasPermission('admin');
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  useEffect(() => {
    if (!allowed || !visible || !sameOwner()) return;
    let valid = true, timer;
    setData(null); setError('');
    http.get(`${API}reminders/`, {params: {muted: muted ? 1 : 0, page, page_size: 5}}).then(result => {
      if (valid && sameOwner()) setData(result);
    }, () => {if (valid && sameOwner()) {setData(null); setError('SSL 提醒读取失败，请刷新重试。');}}).finally(() => {
      if (valid && sameOwner()) timer = window.setTimeout(() => setRevision(value => value + 1), 60000);
    });
    return () => {valid = false; window.clearTimeout(timer);};
  }, [allowed, visible, muted, page, revision, externalRevision]); // owner identity is checked before applying every response.
  async function mute(item) {
    if (locked.current || !sameOwner()) return;
    locked.current = true; setPending(item.id); setError('');
    try {
      await http.post(`${API}reminders/${item.id}/mute/`, {muted: !item.muted});
      if (mounted.current && sameOwner()) {setRevision(value => value + 1); if (onChanged) onChanged();}
    } catch (_) {if (mounted.current && sameOwner()) setError('提醒设置保存失败，请刷新后重试。');}
    finally {locked.current = false; if (mounted.current && sameOwner()) setPending(null);}
  }
  if (!allowed || !sameOwner()) return null;
  return <section className={styles.reminders} aria-label="SSL 到期提醒">
    <div className={styles.reminderHeading}><b>SSL 到期提醒{data ? ` · ${data.total} 条` : ''}</b><div>
      <Checkbox checked={muted} onChange={event => {setMuted(event.target.checked); setPage(1);}}>查看已忽略</Checkbox>
      <Button size="small" type="link" onClick={() => setRevision(value => value + 1)}>刷新提醒</Button>
    </div></div>
    {error && <Alert type="error" message={error}/>}
    {!data && !error && <small>正在读取本地提醒…</small>}
    {data?.items?.map(item => <div className={styles.reminderRow} key={item.id}>
      <div><a href={`/monitor/ssl?q=${encodeURIComponent(item.domain || '')}`}>{text(item.domain)}</a> <small className={styles.secondaryInline}>{text(item.source_name)}</small>
        <small className={styles.secondary}>{text(item.message)} · 到期 {dateUTC8(item.expires_at)}（UTC+8）</small></div>
      <Button type="link" size="small" disabled={pending !== null} loading={pending === item.id} onClick={() => mute(item)}>{item.muted ? '恢复提醒' : '不再提醒'}</Button>
    </div>)}
    {data && !data.items?.length && <small>{muted ? '暂无已忽略提醒' : '暂无有效到期提醒'}</small>}
    {data && (data.total > 5 || page > 1) && <div className={styles.actions}>
      <Button size="small" disabled={page === 1} onClick={() => setPage(value => value - 1)}>上一页</Button>
      <small>第 {page} 页 · 共 {data.total} 条</small><Button size="small" disabled={page * 5 >= data.total} onClick={() => setPage(value => value + 1)}>下一页</Button>
    </div>}
  </section>;
}
