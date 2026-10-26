import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Modal, Select, Space, Spin, Tag, Typography } from 'antd';
import Table from 'components/ResizableTable';
import { hasPermission, http } from 'libs';
import { ACTIVE_STATES, CATEGORY_LABELS, RUN_LABELS, displayValue, downloadReport, hostName } from './helpers';

const POLL_INTERVAL = 5000;
const MAX_POLLS = 60;
const stateColor = state => ['success', 'ok', 'same'].includes(state) ? 'green' : ['failed', 'tripped', 'changed', 'different'].includes(state) ? 'red' : ['warning', 'unknown', 'missing'].includes(state) ? 'orange' : 'default';
function State({value, labels}) {return <Tag color={stateColor(value)}>{labels[value] || '未知'}</Tag>;}
const RESULT_LABELS = {normal: '正常', abnormal: '异常', unverified: '待核实', pending: '待执行', running: '采集中', cancelled: '已取消'};
const RESULT_COLORS = {normal: 'green', abnormal: 'red', unverified: 'orange'};
function resultState(target) {
  if (target.state === 'failed') return 'abnormal';
  if (['pending', 'running', 'cancelled'].includes(target.state)) return target.state;
  if (target.state !== 'success') return 'unverified';
  const checks = Array.isArray(target.checks) ? target.checks : [];
  if (checks.some(check => check.status === 'warning')) return 'abnormal';
  return checks.length && checks.every(check => check.status === 'ok' && check.value !== null && check.value !== undefined &&
    ['string', 'number', 'boolean'].includes(typeof check.value)) ? 'normal' : 'unverified';
}
function resultReason(target) {
  if (target.state === 'failed') return `采集失败：${target.message || CATEGORY_LABELS[target.category] || '原因未返回'}`;
  if (target.state === 'unknown') return target.message || '采集中断，结果待核实';
  if (target.state !== 'success') return '—';
  const checks = Array.isArray(target.checks) ? target.checks : [];
  const issues = checks.filter(check => check.status !== 'ok' || check.value === null || check.value === undefined ||
    !['string', 'number', 'boolean'].includes(typeof check.value));
  return issues.map(check => `${check.label || check.key}：${check.summary || (check.status === 'warning' ? '指标异常' : '数据未核实')}`).join('；') ||
    (checks.length ? '—' : '未取得检查数据');
}
function Checks({checks}) {
  return <Table resizeKey="operations-checks" rowKey="key" size="small" dataSource={checks || []} pagination={false} scroll={{x: 680}} columns={[
    {title: '检查项', dataIndex: 'label', width: 180},
    {title: '状态', dataIndex: 'status', width: 100, render: (status, check) => {
      const state = resultState({state: 'success', checks: [check]});
      return <Tag color={RESULT_COLORS[state]}>{RESULT_LABELS[state]}</Tag>;
    }},
    {title: '具体数据', dataIndex: 'value', width: 180, render: displayValue},
    {title: '说明', dataIndex: 'summary', width: 250},
  ]}/>;
}

export default function RunDetail({runId}) {
  const allowed = hasPermission('admin');
  const [run, setRun] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [action, setAction] = useState('');
  const [notice, setNotice] = useState('');
  const [pollRevision, setPollRevision] = useState(0);
  const [pollStopped, setPollStopped] = useState(false);
  const [stateFilter, setStateFilter] = useState();
  const [expandedIds, setExpandedIds] = useState([]);
  const mounted = useRef(false);
  const revision = useRef(0);
  const readPending = useRef(false);
  const actionPending = useRef(false);
  const dialog = useRef();
  const pollCount = useRef(0);
  const latest = useRef();
  latest.current = {run, error, allowed};

  const refresh = useCallback(async () => {
    if (!allowed || !mounted.current || readPending.current) return null;
    const request = ++revision.current;
    readPending.current = true; setLoading(true);
    try {
      const result = await http.get(`/api/ops/runs/${runId}/`);
      if (mounted.current && request === revision.current) {setRun(result); setError(false); return result;}
    } catch (_) {
      if (mounted.current && request === revision.current) setError(true);
    } finally {
      if (request === revision.current) {readPending.current = false; if (mounted.current) setLoading(false);}
    }
    return null;
  }, [allowed, runId]);

  const runState = run?.state;
  useEffect(() => {
    mounted.current = true;
    if (allowed) refresh();
    return () => {mounted.current = false; revision.current += 1; readPending.current = false; if (dialog.current) dialog.current.destroy();};
  }, [allowed, refresh]);

  useEffect(() => {
    if (!allowed || error || !ACTIVE_STATES.includes(runState)) return;
    let valid = true; let timer;
    if (pollCount.current >= MAX_POLLS) {setPollStopped(true); return;}
    const schedule = () => {timer = setTimeout(async () => {
      if (!valid) return;
      if (actionPending.current || readPending.current) {schedule(); return;}
      pollCount.current += 1;
      const result = await refresh();
      if (!valid) return;
      if (!result || pollCount.current >= MAX_POLLS) {setPollStopped(true); return;}
      if (ACTIVE_STATES.includes(result.state)) schedule();
    }, POLL_INTERVAL);};
    schedule();
    return () => {valid = false; clearTimeout(timer);};
  }, [allowed, error, runState, refresh, pollRevision]);

  async function performAction(kind, expectedState) {
    const current = latest.current;
    if (!mounted.current || !hasPermission('admin') || actionPending.current || !current.allowed) return;
    if (current.error || current.run?.state !== expectedState) {setNotice('作业状态已变化，请刷新后重新确认。'); return;}
    actionPending.current = true; revision.current += 1; readPending.current = false;
    setLoading(false); setAction(kind); setNotice('');
    try {
      const payload = kind === 'continue' && expectedState === 'tripped' ? {acknowledge: true} : {};
      const result = await http.post(`/api/ops/runs/${runId}/${kind}/`, payload);
      if (mounted.current) {
        if (result?.state && result?.id === runId) {setRun(result); setError(false);}
        else await refresh();
      }
    } catch (_) {
      if (mounted.current) {setNotice('操作结果待核实，请刷新状态后再决定下一步。'); await refresh();}
    } finally {actionPending.current = false; if (mounted.current) {pollCount.current = 0; setAction(''); setPollRevision(value => value + 1); setPollStopped(false);}}
  }

  function confirmAction(kind) {
    if (!run || error || actionPending.current) return;
    const tripped = run.state === 'tripped';
    dialog.current = Modal.confirm({
      title: kind === 'cancel' ? '取消尚未执行的主机？' : tripped ? '确认处理熔断并继续？' : '确认继续下一批？',
      content: kind === 'cancel' ? '取消只阻止未下发的检查；已下发的远程检查可能继续完成，请以逐台结果为准。' :
        tripped ? '本批已达到失败或待核实阈值并熔断。请先核对失败原因与中断项；确认后仅继续后续未执行主机，中断待核实项不会自动重发。' :
          '请核对本批检查结果。确认后按冻结目标执行下一批，只读 SSH 探测会占用连接与系统资源。',
      okText: kind === 'cancel' ? '确认取消未执行项' : '确认继续', cancelText: '返回检查',
      onOk: () => performAction(kind, run.state),
    });
  }

  if (!allowed) return <Alert type="warning" message="仅超级管理员可访问运维作业。"/>;
  if (!run) return <Card title={`作业 #${runId}`}>{error ? <Alert type="error" message="作业读取失败。"/> : <Spin/>}<Button onClick={refresh} loading={loading}>刷新详情</Button></Card>;
  const targets = run.targets || [];
  const counts = targets.reduce((result, target) => {
    const state = resultState(target); result[state] = (result[state] || 0) + 1; return result;
  }, {});
  const visibleTargets = targets.filter(target => !stateFilter || resultState(target) === stateFilter);
  const busy = Boolean(action);
  return <Card title={`作业 #${run.id} · ${run.job_name || run.job}`}>
    <Space wrap style={{marginBottom: 16}}>
      <State value={run.state} labels={RUN_LABELS}/>
      <Button loading={loading} disabled={busy} onClick={() => {pollCount.current = 0; setPollStopped(false); setPollRevision(value => value + 1); refresh();}}>刷新详情</Button>
      <Button disabled={loading || busy} onClick={() => downloadReport(run, null, error)}>下载 JSON 报告</Button>
      {['paused', 'tripped'].includes(run.state) && <Button type="primary" disabled={error || busy || loading} loading={action === 'continue'} onClick={() => confirmAction('continue')}>继续下一批</Button>}
      {['queued', 'running', 'paused', 'tripped'].includes(run.state) && <Button danger disabled={error || busy} loading={action === 'cancel'} onClick={() => confirmAction('cancel')}>取消未执行项</Button>}
    </Space>
    {error && <Alert showIcon type="error" message="最新读取失败，当前显示上次快照；请刷新后操作。"/>}
    {notice && <Alert showIcon type="info" message={notice}/>}
    {run.state === 'tripped' && <Alert showIcon type="error" message="本批失败或待核实达到阈值，已熔断。核对原因后明确确认才能继续。"/>}
    {pollStopped && ACTIVE_STATES.includes(run.state) && <Typography.Paragraph type="secondary">自动刷新已结束，请手动刷新。</Typography.Paragraph>}
    <Space wrap style={{marginBottom: 16}}>
      <strong>共 {targets.length} 台</strong>
      {Object.entries(RESULT_LABELS).filter(([state]) => counts[state]).map(([state, label]) => <span key={state}>{label} {counts[state]}</span>)}
      <Select allowClear placeholder="结果状态" style={{width: 150}} value={stateFilter} onChange={setStateFilter}
        options={Object.entries(RESULT_LABELS).map(([value, label]) => ({value, label}))}/>
    </Space>
    <Table resizeKey="operations-results-v2" rowKey="id" dataSource={visibleTargets} pagination={false} scroll={{x: 900}} columns={[
      {title: '主机', dataIndex: 'host', width: 260, render: hostName},
      {title: '结果状态', key: 'state', width: 110, render: (_, target) => <Tag color={RESULT_COLORS[resultState(target)]}>{RESULT_LABELS[resultState(target)]}</Tag>},
      {title: '异常原因', key: 'reason', width: 400, render: (_, target) => <span style={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{resultReason(target)}</span>},
      {title: '检查数据', key: 'data', width: 150, render: (_, target) => target.checks?.length ?
        <Button type="link" size="small" onClick={() => setExpandedIds(ids => ids.includes(target.id) ? ids.filter(id => id !== target.id) : [...ids, target.id])}>
          {expandedIds.includes(target.id) ? '收起数据' : `查看数据（${target.checks.length} 项）`}</Button> : '暂无数据'},
    ]} expandable={{expandedRowKeys: expandedIds, onExpandedRowsChange: setExpandedIds,
      rowExpandable: target => Boolean(target.checks?.length), expandedRowRender: target => <Checks checks={target.checks}/>}}/>

  </Card>;
}
