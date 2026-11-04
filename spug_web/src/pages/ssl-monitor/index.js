import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Select } from 'antd';
import { CheckCircleOutlined, ClockCircleOutlined, CloseCircleOutlined, GlobalOutlined, ReloadOutlined, SafetyCertificateOutlined, SettingOutlined } from '@ant-design/icons';
import { Breadcrumb } from 'components';
import { hasPermission, http } from 'libs';
import ResizableTable from '../../components/ResizableTable';
import { usePageVisibility } from '../server-monitor/useServerMonitor';
import { API, certificateState, dateUTC8, isPollingJob, jobStates, remaining, states, statFilters, text } from './helpers';
import Status from './Status';
import SourceSettings from './SourceSettings';
import CertificateDetail from './CertificateDetail';
import Renewal from './Renewal';
import Guidance from './Guidance';
import Reminders from './Reminders';
import styles from './index.module.less';

const icons = [GlobalOutlined, CheckCircleOutlined, ClockCircleOutlined, CloseCircleOutlined, SafetyCertificateOutlined];
function initialFilters() {
  const query = new URLSearchParams(window.location.search), result = {};
  if (Object.prototype.hasOwnProperty.call(states, query.get('state'))) result.state = query.get('state');
  if (query.get('q')) result.q = query.get('q').slice(0, 200);
  return result;
}
export default function SSLMonitor() {
  const allowed = hasPermission('admin'), visible = usePageVisibility();
  const [filters, setFilters] = useState(initialFilters), [page, setPage] = useState(1), [pageSize, setPageSize] = useState(20);
  const [data, setData] = useState(null), [loading, setLoading] = useState(false), [error, setError] = useState('');
  const [revision, setRevision] = useState(0), [settings, setSettings] = useState(false), [detail, setDetail] = useState(null);
  const [jobId, setJobId] = useState(null), [guidance, setGuidance] = useState(null), [actionError, setActionError] = useState('');
  const [preflighting, setPreflighting] = useState(null), [checking, setChecking] = useState(false), [queued, setQueued] = useState([]);
  const mounted = useRef(false), preflightLock = useRef(false), checkLock = useRef(false), owner = useRef(localStorage.getItem('id'));
  const sameOwner = () => owner.current === localStorage.getItem('id') && hasPermission('admin');
  const refresh = () => setRevision(value => value + 1);
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  useEffect(() => {
    if (!allowed || !visible || !sameOwner()) return;
    let valid = true, timer, delay = 30000;
    setLoading(true); setError('');
    http.get(API, {params: {...filters, page, page_size: pageSize}}).then(result => {
      if (valid && sameOwner()) {setData(result); setQueued([]); delay = result.active_jobs?.some(isPollingJob) ? 2500 : 30000;}
    }, () => {if (valid && sameOwner()) {setData(null); setError('读取 SSL 巡检缓存失败，请刷新重试。');}}).finally(() => {
      if (valid && sameOwner()) {
        setLoading(false);
        timer = window.setTimeout(() => setRevision(value => value + 1), delay);
      }
    });
    return () => {valid = false; window.clearTimeout(timer);};
    // Each result schedules one subsequent local read, without remote collection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed, visible, filters, page, pageSize, revision]);
  function filter(key, value) {setFilters(current => ({...current, [key]: value})); setPage(1);}
  async function preflight(row) {
    if (preflightLock.current || !sameOwner()) return;
    preflightLock.current = true; setPreflighting(row.id); setActionError('');
    try {
      const job = await http.post(`${API}renew/`, {certificate_id: row.id});
      if (mounted.current && sameOwner()) setJobId(job.id);
    } catch (_) {if (mounted.current && sameOwner()) setActionError('未能创建续签预检，请刷新本地结果后核对来源状态。');}
    finally {preflightLock.current = false; if (mounted.current && sameOwner()) setPreflighting(null);}
  }
  const sources = data?.sources || [];
  const groups = [...new Map(sources.flatMap(source => source.groups || []).filter(group => group.key).map(group => [group.key, group])).values()];
  const targets = sources.filter(source => source.enabled && (!filters.source || source.key === filters.source) &&
    (!filters.group || source.groups?.some(group => group.key === filters.group)));
  async function check(sourceKeys) {
    if (checkLock.current || !sourceKeys.length || !sameOwner()) return;
    if (sourceKeys.length > 10) {setActionError('每次最多检查 10 个来源，请通过分组或服务器筛选缩小范围。'); return;}
    checkLock.current = true; setChecking(true); setActionError('');
    try {
      const result = await http.post(`${API}checks/`, {source_keys: sourceKeys});
      if (mounted.current && sameOwner()) {setQueued(result.jobs || []); if (result.jobs?.[0]?.id) setJobId(result.jobs[0].id); refresh();}
    } catch (_) {if (mounted.current && sameOwner()) setActionError('巡检排队未确认，请刷新缓存查看任务。手工巡检冷却 60 秒。');}
    finally {checkLock.current = false; if (mounted.current && sameOwner()) setChecking(false);}
  }
  const jobs = [...new Map([...(data?.active_jobs || []), ...queued].map(job => [job.id, job])).values()];
  const busy = jobs.some(isPollingJob);
  const columns = [
    {title: '域名', dataIndex: 'domain', width: 235, render: (value, row) => <><span className={styles.domain}>{value}</span><small className={styles.secondary}>TLS {row.port || 443} · {row.renewal?.engine || '客户端待检测'}</small></>},
    {title: '服务器', dataIndex: 'source_name', width: 170, render: (value, row) => <><span>{value}</span><small className={styles.secondary}>{sources.find(source => source.key === row.source_key)?.address}</small></>},
    {title: '证书状态', key: 'state', width: 120, render: (_, row) => <Status state={certificateState(row)}>{states[certificateState(row)]}</Status>},
    {title: '签发时间', dataIndex: 'not_before', width: 170, render: value => <span className={styles.date}>{dateUTC8(value)}</span>},
    {title: '到期时间', dataIndex: 'expires_at', width: 170, render: value => <span className={styles.date}>{dateUTC8(value)}</span>},
    {title: '剩余天数', dataIndex: 'days_remaining', width: 120, render: (value, row) => <span className={styles[certificateState(row) === 'expired' ? 'errorText' : 'remaining']}>{remaining(value)}</span>},
    {title: '说明 / 最近巡检', key: 'reason', width: 225, render: (_, row) => <><span className={styles.reason}>{text(row.message || (row.stale ? '来源变更或巡检失败，请重新检查' : '—'))}</span><small className={styles.secondary}>{dateUTC8(row.sampled_at)}</small></>},
    {title: '操作', key: 'actions', width: 150, fixed: 'right', render: (_, row) => <div className={styles.rowActions}>
      <Button type="link" onClick={() => setDetail(row)}>详情</Button>
      {row.latest_job_id && <Button type="link" onClick={() => setJobId(row.latest_job_id)}>任务</Button>}
      {row.renewal?.supported && !row.stale && !['unknown', 'unconfigured', 'error'].includes(certificateState(row))
        ? <Button type="link" loading={preflighting === row.id} disabled={preflighting !== null || !!error} onClick={() => preflight(row)}>续签</Button>
        : <Button type="link" onClick={() => setGuidance(row)}>配置指导</Button>}
    </div>},
  ];
  if (!allowed || !sameOwner()) return null;
  return <>
    <Breadcrumb><Breadcrumb.Item>首页</Breadcrumb.Item><Breadcrumb.Item>监控中心</Breadcrumb.Item><Breadcrumb.Item>SSL 监控</Breadcrumb.Item></Breadcrumb>
    <main className={styles.page}>
      <div className={styles.heading}><div><span className={styles.eyebrow}>SSL CERTIFICATE MONITOR</span><h1>SSL证书巡检</h1><p>掌握网站证书状态，让每一次安全连接持续有效。</p></div>
        <div className={styles.actions}><Button icon={<SettingOutlined/>} onClick={() => setSettings(true)}>来源设置</Button>
          <Button type="primary" disabled={busy || !targets.length || targets.length > 10} loading={checking} onClick={() => check(targets.map(source => source.key))}>立即巡检</Button></div>
      </div>
      <div className={styles.stats}>{statFilters.map((stat, index) => {const Icon = icons[index]; return <button key={stat.key} data-stat={stat.key} data-tone={stat.tone} className={styles.stat}
        aria-pressed={filters.state === stat.state} onClick={() => filter('state', stat.state)}>
        <div><span className={styles.statLabel}>{stat.label}</span><strong>{data?.stats?.[stat.key] ?? '—'}</strong><small>{stat.note}</small></div><span className={styles.statIcon}><Icon/></span>
      </button>;})}</div>
      <Reminders/>
      {targets.length > 10 && <Alert className={styles.message} type="info" showIcon message="每次最多检查 10 个来源，请通过分组或服务器筛选缩小范围。"/>}
      {error && <Alert className={styles.message} type="error" showIcon message={error}/>}
      {actionError && <Alert className={styles.message} type="warning" showIcon message={actionError}/>}
      {!!jobs.length && <div className={styles.jobs}><b>巡检与续签进度</b>{jobs.map(job => <Button type="link" size="small" key={job.id} onClick={() => setJobId(job.id)}>#{job.id} · {jobStates[job.state] || '状态未知'} · {text(job.message || job.phase)}</Button>)}</div>}
      <section className={styles.panel}>
        <div className={styles.filters}>
          <Input.Search aria-label="搜索域名或来源" defaultValue={filters.q} placeholder="搜索域名或服务器名称" allowClear onSearch={value => filter('q', value.trim().slice(0, 200))}/>
          <Select aria-label="分组筛选" allowClear placeholder="全部分组" value={filters.group} onChange={value => {setFilters(current => ({...current, group: value, source: undefined})); setPage(1);}}
            options={groups.map(group => ({value: group.key, label: group.name}))}/>
          <Select aria-label="服务器筛选" allowClear showSearch optionFilterProp="label" placeholder="全部服务器" value={filters.source} onChange={value => filter('source', value)}
            options={sources.filter(source => !filters.group || source.groups?.some(group => group.key === filters.group)).map(source => ({value: source.key, label: `${source.name} · ${source.kind === 'panel' ? '宝塔' : 'SSH'}`}))}/>
          <Select aria-label="证书状态筛选" allowClear placeholder="全部状态" value={filters.state} onChange={value => filter('state', value)} options={Object.entries(states).map(([value, label]) => ({value, label}))}/>
          <Button icon={<ReloadOutlined/>} loading={loading} onClick={refresh}>刷新缓存</Button>
        </div>
        <div className={styles.tableHeader}><h2>证书列表 <span>共 {data?.total ?? '—'} 个域名</span></h2><span>到期优先 · 时间 UTC+8</span></div>
        <ResizableTable className={styles.table} resizeKey="ssl-certificates" rowKey="id" loading={loading} columns={columns} dataSource={data?.items || []} scroll={{x: 1460}}
          locale={{emptyText: error ? '缓存读取失败' : '暂无证书。请在来源设置中明确开启主机或宝塔面板巡检。'}}
          pagination={{current: page, pageSize, total: data?.total || 0, showSizeChanger: true, showTotal: total => `共 ${total} 条`,
            onChange: (next, size) => {setPage(size === pageSize ? next : 1); setPageSize(size);}}}/>
      </section>
      <p className={styles.footnote}>自动刷新仅读取本地缓存。立即巡检会连接已启用来源，手工巡检冷却 60 秒。公网 TLS 与源站证书独立验证；旧缓存和未知状态不计为正常。</p>
      {settings && <SourceSettings onClose={() => setSettings(false)} onSaved={refresh}/>}
      {detail && <CertificateDetail row={detail} onClose={() => setDetail(null)}/>}
      {jobId && <Renewal key={jobId} jobId={jobId} onClose={() => setJobId(null)} onUpdate={refresh}/>}
      {guidance && <Guidance row={guidance} onClose={() => setGuidance(null)} onRecheck={() => {const source = sources.find(source => source.key === guidance.source_key);
        if (source?.enabled) {setGuidance(null); check([source.key]);} else {setGuidance(null); setSettings(true);}}}/>}
    </main>
  </>;
}
