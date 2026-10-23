import React from 'react';
import { Button, Progress, Tag } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import styles from './index.module.less';

const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
export function bytes(value) {
  if (!number(value)) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {value /= 1024; index++;}
  return `${value.toFixed(index && value < 10 ? 1 : 0)} ${units[index]}`;
}
function time(value) {
  if (value === null || value === undefined || value === '') return '—';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}
export function statusOf(card, interval = 60, now = Date.now()) {
  const snapshot = card.snapshot;
  if (snapshot?.status === 'ok' && typeof snapshot.sampled_at === 'number' &&
    now / 1000 - snapshot.sampled_at > interval + 45) return 'stale';
  return snapshot?.status || 'pending';
}
export const statusLabels = {pending: '待采集', ok: '正常', stale: '数据过期', error: '采集失败'};

function Meter({label, percent, detail}) {
  const known = number(percent) && percent <= 100;
  return <div className={styles.meter}>
    <div className={styles.metricLabel}><span>{label}</span><b>{known ? `${percent.toFixed(1).replace(/\.0$/, '')}%` : '—'}</b></div>
    {known ? <Progress percent={percent} showInfo={false} size="small" strokeColor={percent >= 90 ? '#ed5e68' : '#36b7a4'}/>
      : <div className={styles.unknown}>—</div>}
    {detail && <div className={styles.detail}>{detail}</div>}
  </div>;
}

function Disk({disk}) {
  return <Meter label={`磁盘 ${disk.path || '—'}`} percent={disk.percent} detail={`${bytes(disk.used_bytes)} / ${bytes(disk.total_bytes)}`}/>;
}

export default function ResourceCard({card, hideIP, onLink, onCollect, collecting, collectDisabled, collectionResult, interval = 60, now}) {
  const snapshot = card.snapshot;
  const metrics = snapshot?.metrics;
  const disks = metrics?.disks || [];
  const status = statusOf(card, interval, now);
  return <article className={styles.card} data-monitor-key={card.key}>
    <header className={styles.cardHeader}>
      <div className={styles.identity}><h3 title={card.name}>{card.name}</h3>
        <span className={styles.address}>{hideIP ? '地址已隐藏' : card.address || '—'}</span></div>
      <div className={styles.cardActions}>
        <Button size="small" icon={<ReloadOutlined/>} loading={collecting} disabled={collectDisabled}
          onClick={() => onCollect(card.key)}>立即采集</Button>
        <Tag color={{ok: 'green', stale: 'orange', error: 'red'}[status]}>{statusLabels[status] || '未知状态'}</Tag>
      </div>
    </header>
    <div className={styles.meta}>
      <span>{card.groups.map(group => group.name).join(' / ') || '未分组'}</span>
      <span>{card.sources.map(source => source.kind === 'panel' ? '宝塔 API' : 'SSH').filter((name, index, names) => names.indexOf(name) === index).join(' + ')}</span>
    </div>
    <div className={styles.overview}>
      <div><span>负载 1 / 5 / 15 分钟</span><b>{[0, 1, 2].map(index => number(metrics?.load?.[index]) ? metrics.load[index].toFixed(2) : '—').join(' / ')}</b></div>
      <div><span>网络 ↑ / ↓</span><b>{bytes(metrics?.network?.up_bytes_sec)}/s / {bytes(metrics?.network?.down_bytes_sec)}/s</b></div>
    </div>
    <Meter label={`CPU${number(metrics?.cpu?.cores) ? ` · ${metrics.cpu.cores} 核` : ''}`} percent={metrics?.cpu?.percent}/>
    <Meter label="内存" percent={metrics?.memory?.percent} detail={`${bytes(metrics?.memory?.used_bytes)} / ${bytes(metrics?.memory?.total_bytes)}`}/>
    {disks.length ? <Disk disk={disks[0]}/> : <Meter label="磁盘"/>}
    {disks.length > 1 && <details className={styles.disks}><summary>其余 {disks.length - 1} 个磁盘</summary>
      {disks.slice(1).map((disk, index) => <Disk key={`${disk.path}:${index}`} disk={disk}/>)}</details>}
    {snapshot?.status !== 'ok' && snapshot?.message && <div className={styles.statusMessage} role={status === 'error' ? 'alert' : undefined}>{snapshot.message}</div>}
    {collectionResult && <div className={styles.collectionResult} role="status">{collecting ? '正在采集…' : collectionResult}</div>}
    <footer className={styles.cardFooter}>
      <div><div>更新：{time(snapshot?.sampled_at)}</div>
        {status !== 'ok' && snapshot?.attempted_at && <div>尝试：{time(snapshot.attempted_at)}</div>}</div>
      {card.can_link && card.panel_id && <Button type="link" size="small" onClick={() => onLink(card)}>关联设置</Button>}
    </footer>
  </article>;
}
