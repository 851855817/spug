import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Empty, Input, Pagination, Select, Spin, Switch } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { AuthDiv, Breadcrumb } from 'components';
import { hasPermission } from 'libs';
import ResourceCard, { statusLabels, statusOf } from './ResourceCard';
import LinkSettings from './LinkSettings';
import useServerMonitor, { usePageVisibility } from './useServerMonitor';
import styles from './index.module.less';

export default function ServerMonitor() {
  const allowed = hasPermission('monitor.monitor.view');
  const monitor = useServerMonitor(allowed);
  const visible = usePageVisibility();
  const [automatic, setAutomatic] = useState(true);
  const [interval, setInterval] = useState(60);
  const [hideIP, setHideIP] = useState(false);
  const [group, setGroup] = useState('');
  const [source, setSource] = useState('');
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(12);
  const [linkCard, setLinkCard] = useState(null);
  const [manualRound, setManualRound] = useState(false);
  const manualRequest = useRef(0);
  const controls = useRef();
  controls.current = {automatic, visible};
  useEffect(() => () => {manualRequest.current++;}, []);
  useEffect(() => {
    if (!visible) {manualRequest.current++; setManualRound(false);}
  }, [visible]);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!visible) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 10000);
    return () => window.clearInterval(timer);
  }, [visible]);
  const rows = useMemo(() => monitor.cards.filter(card =>
    (!group || card.groups.some(item => item.key === group)) &&
    (!source || card.sources.some(item => item.kind === source)) &&
    (!status || statusOf(card, interval, now) === status) &&
    (!search || `${card.name} ${hideIP ? '' : card.address}`.toLowerCase().includes(search.trim().toLowerCase()))),
  [monitor.cards, group, source, status, search, hideIP, interval, now]);
  const currentPage = Math.min(page, Math.max(1, Math.ceil(rows.length / pageSize)));
  const shown = rows.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const keys = JSON.stringify(shown.map(card => card.key));
  const active = allowed && monitor.ready && !monitor.loading && (automatic || manualRound) && visible;
  const sampler = monitor.sampler;
  useEffect(() => {
    sampler.update({keys: JSON.parse(keys), interval, active, once: !automatic, force: manualRound, onComplete: () => setManualRound(false)});
    return () => sampler.stop();
  }, [sampler, keys, interval, active, automatic, manualRound, monitor.revision]);

  const groups = [...new Map(monitor.cards.flatMap(card => card.groups).map(item => [item.key, item])).values()];
  function filter(setter, value) {sampler.stop(); manualRequest.current++; setManualRound(false); setter(value); setPage(1);}
  function toggleAutomatic(value) {
    sampler.stop(); manualRequest.current++; setManualRound(false); setAutomatic(value);
  }
  async function manualRefresh() {
    const version = ++manualRequest.current;
    setManualRound(false);
    const success = await monitor.refresh();
    if (success && version === manualRequest.current && controls.current.visible) setManualRound(true);
  }
  if (!allowed) return null;
  return <AuthDiv auth="monitor.monitor.view">
    <Breadcrumb><Breadcrumb.Item>首页</Breadcrumb.Item><Breadcrumb.Item>监控中心</Breadcrumb.Item><Breadcrumb.Item>服务器监控</Breadcrumb.Item></Breadcrumb>
    <main className={styles.layout}>
      <div className={styles.heading}><div><h2>服务器监控</h2><span>当前页 {shown.length} 台 / 共 {rows.length} 台 · {visible && automatic ? '自动采集' : '采集已暂停'}</span></div>
        <div className={styles.controls}>
          <label>隐藏 IP <Switch size="small" aria-label="隐藏 IP" checked={hideIP} onChange={value => filter(setHideIP, value)}/></label>
          <label>自动刷新 <Switch size="small" aria-label="自动刷新" checked={automatic} onChange={toggleAutomatic}/></label>
          <Select aria-label="采集间隔" value={String(interval)} onChange={value => setInterval(Number(value))}
            options={[30, 60, 300].map(value => ({value: String(value), label: value === 300 ? '5 分钟' : `${value} 秒`}))}/>
          <Button icon={<ReloadOutlined/>} loading={monitor.loading} onClick={manualRefresh}>刷新</Button>
        </div>
      </div>
      <div className={styles.filters}>
        <Input allowClear placeholder="搜索名称或 IP" value={search} onChange={event => filter(setSearch, event.target.value)}/>
        <Select aria-label="分组筛选" value={group} onChange={value => filter(setGroup, value)} options={[
          {value: '', label: '全部分组'}, ...groups.map(item => ({value: item.key, label: item.name})),
        ]}/>
        <Select aria-label="来源筛选" value={source} onChange={value => filter(setSource, value)} options={[
          {value: '', label: '全部来源'}, {value: 'panel', label: '宝塔 API'}, {value: 'host', label: 'SSH 主机'},
        ]}/>
        <Select aria-label="状态筛选" value={status} onChange={value => filter(setStatus, value)} options={[
          {value: '', label: '全部状态'}, ...Object.entries(statusLabels).map(([value, label]) => ({value, label})),
        ]}/>
      </div>
      {monitor.error && <Alert type="error" showIcon message="监控列表加载失败，请刷新重试"/>}
      <Spin spinning={monitor.loading}>
        {monitor.ready && !monitor.cards.length ? <div className={styles.empty}><Empty description="还没有开启监控的服务器"/>
          <p>请在 <a href="/host">主机管理</a> 或 <a href="/baota">宝塔面板管理</a> 中开启监控。</p></div>
          : monitor.ready && !shown.length ? <Empty description="没有符合筛选条件的服务器"/>
            : <div className={styles.grid}>{shown.map(card => <ResourceCard key={card.key} card={card} hideIP={hideIP} interval={interval} now={now} onLink={setLinkCard}/>)}</div>}
      </Spin>
      {!!rows.length && <div className={styles.pagination}><Pagination current={currentPage} pageSize={pageSize} total={rows.length}
        showSizeChanger pageSizeOptions={['12', '24', '48']} showTotal={total => `共 ${total} 台服务器`}
        onChange={(value, size) => {sampler.stop(); manualRequest.current++; setManualRound(false); setPage(value); setPageSize(size);}}/></div>}
    </main>
    {linkCard && <LinkSettings card={linkCard} hideIP={hideIP} onCancel={() => setLinkCard(null)}
      onSaved={() => {setLinkCard(null); monitor.refresh();}}/>}
  </AuthDiv>;
}
