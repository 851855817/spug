import React, { useEffect, useRef, useState } from 'react';
import moment from 'moment';
import { Alert, Button, Card, Input, Select, Space, Tag, TreeSelect, Typography } from 'antd';
import { AuthDiv, Breadcrumb } from 'components';
import ResizableTable from '../../components/ResizableTable';
import { hasPermission, http } from 'libs';
import { buildGroupTree, descendantIds } from '../baota/helpers';

const SSL_STATES = {valid: '有效（面板登记）', expiring: '30 天内临期', expired: '已过期', unconfigured: '未配置 SSL', unknown: '未知'};
const BACKUP_STATES = {no_record: '无备份记录', local_missing: '本地文件缺失', empty: '本地文件为空', in_progress: '进行中',
  panel_reported: '面板登记有备份', unknown: '未知', overdue: '超过阈值'};
const COLLECTION_STATES = {never_collected: '尚未采集', unconfigured: 'API 未配置', ok: '已采集', stale: '旧缓存',
  invalidated: '配置已变化 · 缓存失效', error: '采集失败', busy: '已有采集进行中', cooldown: '冷却中'};
const color = state => ['expired', 'local_missing', 'empty', 'error', 'invalidated'].includes(state) ? 'red' :
  ['expiring', 'stale', 'no_record', 'overdue'].includes(state) ? 'orange' : ['valid', 'ok'].includes(state) ? 'green' : 'default';
const date = value => value && moment(value).isValid() ? moment(value).format('YYYY-MM-DD HH:mm:ss') : '—';
const shortMessage = value => String(value || '').replace(/[\r\n\t]/g, ' ').slice(0, 160);
function size(value) {
  if (value == null || !Number.isFinite(Number(value))) return '未知';
  if (Number(value) < 1024) return `${value} B`;
  const exponent = Math.min(Math.floor(Math.log(Number(value)) / Math.log(1024)), 4);
  return `${(Number(value) / Math.pow(1024, exponent)).toFixed(1)} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][exponent]}`;
}

export default function Inventory({kind = 'sites'}) {
  const sites = kind === 'sites';
  const title = sites ? '网站与证书' : '备份检查台';
  const states = sites ? SSL_STATES : BACKUP_STATES;
  const filterStates = {...states, stale: '旧缓存'};
  const canView = hasPermission('admin');
  const [options, setOptions] = useState({panels: [], groups: []});
  const [optionsError, setOptionsError] = useState('');
  const [optionsRevision, setOptionsRevision] = useState(0);
  const [filters, setFilters] = useState(() => {
    const query = new URLSearchParams(window.location.search), initial = {};
    if (Object.prototype.hasOwnProperty.call(filterStates, query.get('state'))) initial.state = query.get('state');
    if (/^[1-9][0-9]{0,14}$/.test(query.get('panel_id') || '')) initial.panel_id = Number(query.get('panel_id'));
    const keyword = query.get('q');
    if (keyword && keyword.length <= 200 && ![...keyword].some(char => char.charCodeAt(0) < 32)) initial.q = keyword;
    return initial;
  });
  const [maxAge, setMaxAge] = useState(48);
  const [pagination, setPagination] = useState({page: 1, page_size: 20});
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState([]);
  const [collecting, setCollecting] = useState(false);
  const [awaiting, setAwaiting] = useState(false);
  const [progress, setProgress] = useState('');
  const [collectionError, setCollectionError] = useState('');
  const mounted = useRef(false);
  const sequence = useRef(null);
  const requestId = useRef(0);
  const pendingPost = useRef(false);

  function stop(reason = '已停止后续采集；已发送的采集由服务端结束。') {
    if (sequence.current) sequence.current.cancelled = true;
    if (mounted.current) {setCollecting(false); setProgress(reason);}
  }
  useEffect(() => {
    mounted.current = true;
    function hidden() {if (document.visibilityState === 'hidden' && sequence.current && !sequence.current.cancelled) stop('页面已隐藏，已停止后续采集。');}
    document.addEventListener('visibilitychange', hidden);
    return () => {
      mounted.current = false;
      if (sequence.current) sequence.current.cancelled = true;
      document.removeEventListener('visibilitychange', hidden);
    };
  }, [kind]);
  useEffect(() => {
    if (!canView) return;
    let valid = true;
    setOptionsError('');
    http.get('/api/baota/inventory/options/').then(result => {
      if (valid) setOptions(result);
    }, () => {if (valid) setOptionsError('读取面板选项失败，请重试。');});
    return () => {valid = false;};
  }, [canView, optionsRevision]);
  useEffect(() => {
    if (!canView) return;
    const id = ++requestId.current;
    let valid = true;
    const params = {...pagination};
    Object.entries(filters).forEach(([key, value]) => {if (value !== undefined && value !== '') params[key] = value;});
    if (!sites) params.max_age_hours = maxAge;
    setLoading(true); setError(''); setData(null);
    http.get(`/api/baota/inventory/${kind}/`, {params}).then(result => {
      if (valid && id === requestId.current) setData(result);
    }, () => {if (valid && id === requestId.current) setError('读取缓存失败，请刷新重试。');}).finally(() => {
      if (valid && id === requestId.current) setLoading(false);
    });
    return () => {valid = false;};
  }, [canView, kind, sites, filters, pagination, maxAge, revision]);

  function filter(key, value) {
    setFilters(current => ({...current, [key]: value}));
    setPagination(current => ({...current, page: 1}));
  }
  const groupIds = filters.group_id ? descendantIds(options.groups, Number(filters.group_id)) : null;
  const range = options.panels.filter(panel => (!filters.panel_id || panel.id === Number(filters.panel_id)) &&
    (!groupIds || groupIds.has(panel.group_id)));
  const targets = selected.length ? options.panels.filter(panel => selected.includes(panel.id)) : range;
  const configuredTargets = targets.filter(panel => panel.configured);
  const allTargets = options.panels.filter(panel => panel.configured);
  async function collect(panels) {
    if (!hasPermission('admin') || collecting || pendingPost.current || !panels.length || document.visibilityState === 'hidden') return;
    const run = {cancelled: false};
    sequence.current = run;
    const valid = () => {
      if (!mounted.current || sequence.current !== run || run.cancelled || document.visibilityState === 'hidden') return false;
      if (!hasPermission('admin')) {stop('访问权限已变化，已停止后续采集。'); return false;}
      return true;
    };
    setCollecting(true); setCollectionError('');
    let completed = 0;
    for (const panel of panels) {
      if (!valid()) break;
      setProgress(`正在采集 ${panel.name}（${completed + 1}/${panels.length}）`);
      try {
        pendingPost.current = true; setAwaiting(true);
        const result = await http.post('/api/baota/inventory/collect/', {panel_id: panel.id, kind}, {timeout: 60000});
        if (!valid()) break;
        completed++;
        setRevision(value => value + 1);
        if (result.status === 'busy') {setCollectionError('已有采集进行中，本次后续采集已停止。'); break;}
        if (['error', 'invalidated', 'unconfigured', 'cooldown'].includes(result.status)) {
          setCollectionError(`${panel.name}：${shortMessage(result.message) || COLLECTION_STATES[result.status]}。其余面板继续。`);
        }
      } catch (_) {
        if (!valid()) break;
        completed++;
        setCollectionError(`${panel.name}：采集请求失败，保留上次成功缓存。`);
        setRevision(value => value + 1);
      } finally {
        pendingPost.current = false;
        if (mounted.current && sequence.current === run) setAwaiting(false);
      }
    }
    if (valid()) {run.cancelled = true; setCollecting(false); setProgress(`本轮已结束（${completed}/${panels.length}）；请核对各面板采集状态。`);}
  }

  const panelStates = new Map((data?.panels || []).map(panel => [panel.panel_id, panel]));
  const timestampColumn = {title: '采集时间', key: 'sampled', width: 220, render: (_, row) => <>
    <div>{row.stale && <Tag color="orange">旧缓存</Tag>}成功：{date(row.sampled_at)}</div>
    <Typography.Text type="secondary">尝试：{date(panelStates.get(row.panel_id)?.attempted_at)}</Typography.Text>
  </>};
  const commonColumns = [
    {title: '分组 / 服务器', key: 'server', width: 200, render: (_, row) => <><div>{row.group_name || '未分组'} / {row.panel_name}</div><Typography.Text type="secondary">{row.ip || 'IP 未登记'}</Typography.Text></>},
    {title: sites ? '网站' : '资源', dataIndex: 'name', width: 190},
  ];
  const siteColumns = [
    {title: '域名', dataIndex: 'domains', width: 250, render: value => (value || []).map(item => `${item.name}${item.port ? `:${item.port}` : ''}`).join('、') || '未知'},
    {title: 'PHP / 站点', key: 'runtime', width: 150, render: (_, row) => <><div>{row.php_version === '00' ? '静态网站' : row.php_version ? `PHP ${String(row.php_version).split('').join('.')}` : 'PHP 未知'}</div>
      <div>{{running: '运行中', stopped: '已停止', unknown: '状态未知'}[row.site_status] || '状态未知'}</div></>},
    {title: '证书登记摘要', key: 'ssl', width: 200, render: (_, row) => <><Tag color={color(row.ssl_state)}>{SSL_STATES[row.ssl_state] || '未知'}</Tag>
      <div>到期：{row.expires_at || '未知'}</div>{row.days_remaining != null && <div>剩余 {row.days_remaining} 天</div>}</>},
    timestampColumn,
    {title: '证书任务', key: 'action', width: 155, render: (_, row) => {
      if (row.ssl_state === 'unknown' || panelStates.get(row.panel_id)?.configured === false) return <Typography.Text type="secondary">请先核对证书状态</Typography.Text>;
      const operation = row.ssl_state === 'unconfigured' ? 'ssl_apply' : 'ssl_renew';
      return <a href={`/exec/panel?panel_id=${encodeURIComponent(row.panel_id)}&site_id=${encodeURIComponent(row.site_id)}&operation=${operation}`}>{operation === 'ssl_apply' ? '填写申请任务' : '填写续签任务'}</a>;
    }},
  ];
  const backupColumns = [
    {title: '资源类型', dataIndex: 'asset_type', width: 135, render: value => ({site: '网站', database: 'MySQL 数据库'}[value] || '未知')},
    {title: '最新登记备份', key: 'backup', width: 230, render: (_, row) => <><div>{row.backup_name || (row.state === 'no_record' ? '无记录' : '文件名未知')}</div><div>{date(row.backup_at)}</div><Typography.Text type="secondary">{size(row.size_bytes)}</Typography.Text></>},
    {title: '登记状态', key: 'state', width: 200, render: (_, row) => <><Tag color={color(row.state)}>{BACKUP_STATES[row.state] || '未知'}</Tag>
      <div>{row.overdue === true ? <Tag color="orange">超过 {maxAge} 小时</Tag> : row.overdue == null ? '备份年龄未知' : '未超过阈值'}</div>
      {row.age_hours != null && <div>距今 {Number(row.age_hours).toFixed(1)} 小时</div>}</>},
    {title: '面板本地文件证据', key: 'evidence', width: 225, render: (_, row) => <>
      <div>{{local: '本地记录', cloud_record: '云端记录', unknown: '位置未知'}[row.location] || '位置未知'}</div>
      <div>本地文件：{row.local_exists === true ? '面板报告存在' : row.local_exists === false ? '面板报告缺失' : '未知'}</div>
      <div>进行中：{row.in_progress === true ? '是' : row.in_progress === false ? '否' : '未知'}</div>
    </>}, timestampColumn,
  ];
  const collectionColumns = [
    {title: '面板', key: 'panel', width: 200, render: (_, row) => <>{row.name} <Typography.Text type="secondary">{row.ip}</Typography.Text></>},
    {title: '采集状态', key: 'status', width: 230, render: (_, row) => <><Tag color={color(row.status)}>{COLLECTION_STATES[row.status] || '未知'}</Tag>{row.stale && <Tag color="orange">旧缓存</Tag>}</>},
    {title: '说明', dataIndex: 'message', width: 300, render: shortMessage},
    {title: '上次成功 / 最近尝试', key: 'times', width: 240, render: (_, row) => <><div>{date(row.sampled_at)}</div><div>{date(row.attempted_at)}</div>{row.cooldown_until && <div>冷却至 {date(row.cooldown_until)}</div>}</>},
  ];
  return <AuthDiv auth="admin">
    <Breadcrumb><Breadcrumb.Item>首页</Breadcrumb.Item><Breadcrumb.Item>批量执行</Breadcrumb.Item><Breadcrumb.Item>{title}</Breadcrumb.Item></Breadcrumb>
    <Card title={title}>
      <Alert showIcon type="info" message="列表读取上次成功采集的缓存，点击采集才查询面板。"
        description={sites ? 'SSL 为面板登记摘要，未验证公网 TLS 握手；临期范围为 30 天。证书入口只预填任务选择，请在原表单核对后生成预检。' :
          '覆盖网站与面板登记的 MySQL 数据库，其他数据库类型未覆盖。只展示最近一条登记备份，云端可用性未核验；面板报告本地存在且非空，也不代表归档完整或可恢复。备份时间按平台时区解析，年龄为估算，远端时区未核实。'}/>
      <Space wrap style={{margin: '16px 0'}}>
        <TreeSelect aria-label="分组筛选" allowClear placeholder="全部分组（含子组）" style={{width: 200}}
          treeData={buildGroupTree(options.groups)} value={filters.group_id} onChange={value => filter('group_id', value ? Number(value) : undefined)}/>
        <Select aria-label="服务器筛选" allowClear showSearch optionFilterProp="label" placeholder="全部服务器" style={{width: 230}}
          value={filters.panel_id} onChange={value => filter('panel_id', value ? Number(value) : undefined)}
          options={options.panels.map(panel => ({value: panel.id, label: `${panel.name} · ${panel.ip || 'IP 未登记'}`}))}/>
        <Input.Search aria-label="关键字" defaultValue={filters.q} allowClear placeholder={sites ? '网站 / 域名 / 服务器 / 分组 / IP' : '资源 / 服务器 / 分组 / IP'} style={{width: 270}} onSearch={value => filter('q', value.trim())}/>
        <Select aria-label={sites ? 'SSL 状态' : '备份状态'} allowClear placeholder="全部状态" style={{width: 190}} value={filters.state}
          onChange={value => filter('state', value)} options={Object.entries(filterStates).map(([value, label]) => ({value, label}))}/>
        {!sites && <Select aria-label="逾期阈值" value={maxAge} style={{width: 160}}
          onChange={value => {setMaxAge(Number(value)); setPagination(current => ({...current, page: 1}));}}
          options={[24, 48, 72, 168].map(value => ({value, label: `逾期阈值 ${value} 小时`}))}/>}
        <Button onClick={() => {setRevision(value => value + 1); setOptionsRevision(value => value + 1);}}>刷新缓存</Button>
      </Space>
      {optionsError && <Alert type="error" message={optionsError}/>}
      <div style={{marginBottom: 16}}><Space wrap>
        <Select aria-label="采集面板" mode="multiple" showSearch optionFilterProp="label" placeholder="选择采集面板；留空使用筛选范围" value={selected}
          disabled={collecting || awaiting} style={{minWidth: 340, maxWidth: '100%'}} onChange={setSelected}
          options={options.panels.map(panel => ({value: panel.id, label: `${panel.name}${panel.configured ? '' : ' · API 未配置'}`, disabled: !panel.configured}))}/>
        <Button type="primary" disabled={collecting || awaiting || !configuredTargets.length} onClick={() => collect(configuredTargets)}>
          {selected.length ? '采集所选面板' : '采集当前范围'}（{configuredTargets.length}）</Button>
        <Button disabled={collecting || awaiting || !allTargets.length} onClick={() => collect(allTargets)}>采集全部面板（{allTargets.length}）</Button>
        <Button disabled={!collecting} onClick={() => stop()}>停止后续采集</Button>
      </Space><Typography.Paragraph type="secondary" style={{margin: '8px 0 0'}}>当前范围按分组及服务器选取面板；关键字和状态只筛选缓存资源。按面板顺序采集，切换页面或隐藏标签会停止后续面板。API 未配置的面板跳过。</Typography.Paragraph>
        {progress && <div role="status">{progress}</div>}
        {collectionError && <Alert type="warning" showIcon message={collectionError}/>}</div>
      {data?.summary && <Space wrap style={{marginBottom: 12}}>
        {Object.entries({'': '范围内总计', ...filterStates}).map(([state, label]) => <button key={state} type="button"
          aria-pressed={(filters.state || '') === state} title={`显示${state ? label : '全部'}${sites ? '网站' : '资源'}`}
          onClick={() => filter('state', state || undefined)}
          style={{border: 0, padding: 0, background: 'transparent', cursor: 'pointer'}}>
          <Tag color={color(state)} style={{marginRight: 0, boxShadow: (filters.state || '') === state ? '0 0 0 2px #1890ff' : undefined}}>
            {label} {data.summary[state || 'total'] || 0}
          </Tag>
        </button>)}
      </Space>}
      {error && <Alert type="error" message={error}/>}
      <ResizableTable resizeKey={`inventory-${kind}`} rowKey="id" size="small" loading={loading} dataSource={data?.items || []}
        columns={[...commonColumns, ...(sites ? siteColumns : backupColumns)]} scroll={{x: sites ? 1450 : 1600}}
        locale={{emptyText: '当前范围没有已缓存的资源；请核对下方采集状态。'}}
        pagination={{current: pagination.page, pageSize: pagination.page_size, total: data?.total || 0, showSizeChanger: true,
          pageSizeOptions: ['20', '50', '100'], showTotal: total => `共 ${total} 条`,
          onChange: (page, page_size) => setPagination({page: page_size !== pagination.page_size ? 1 : page, page_size})}}/>
    </Card>
    <Card title="各面板采集状态" style={{marginTop: 16}}>
      <Typography.Paragraph type="secondary">采集失败会保留上次成功缓存；配置变化会使缓存失效。以下状态也包含尚未采集和没有资源的面板。</Typography.Paragraph>
      <ResizableTable resizeKey={`inventory-${kind}-panels`} rowKey="panel_id" size="small" columns={collectionColumns} dataSource={data?.panels || []}
        scroll={{x: 1000}} pagination={{pageSize: 5, showSizeChanger: false, hideOnSinglePage: true}}/>
    </Card>
  </AuthDiv>;
}

export function SiteInventory() {return <Inventory kind="sites"/>;}
export function BackupInventory() {return <Inventory kind="backups"/>;}
