import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Input, Modal, Select, Space, Spin, Tag, Typography } from 'antd';
import Table from 'components/ResizableTable';
import { hasPermission, http } from 'libs';
import { ACTIVE_STATES, CATEGORY_LABELS, CHECK_LABELS, DIFF_LABELS, RUN_LABELS, TARGET_LABELS, displayValue, downloadReport, hostName } from './helpers';

const POLL_INTERVAL = 5000;
const MAX_POLLS = 60;
const stateColor = state => ['success', 'ok', 'same'].includes(state) ? 'green' : ['failed', 'tripped', 'changed', 'different'].includes(state) ? 'red' : ['warning', 'unknown', 'missing'].includes(state) ? 'orange' : 'default';
function State({value, labels}) {return <Tag color={stateColor(value)}>{labels[value] || '未知'}</Tag>;}
function compatibleBaseline(baseline, run) {
  const before = baseline.params || {}; const after = run.params || {};
  return baseline.job === run.job && String(baseline.version) === String(run.version) &&
    Object.keys(before).length === Object.keys(after).length && Object.keys(before).every(key => before[key] === after[key]);
}

function Checks({checks}) {
  return <Table resizeKey="operations-checks" rowKey="key" size="small" dataSource={checks || []} pagination={false} scroll={{x: 680}} columns={[
    {title: '检查项', dataIndex: 'label', width: 180},
    {title: '指标状态', dataIndex: 'status', width: 130, render: value => <State value={value} labels={CHECK_LABELS}/>},
    {title: '采集值', dataIndex: 'value', width: 180, render: displayValue},
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
  const [categoryFilter, setCategoryFilter] = useState();
  const [baselines, setBaselines] = useState([]);
  const [baselineError, setBaselineError] = useState(false);
  const [baselineLoading, setBaselineLoading] = useState(false);
  const [baselinePage, setBaselinePage] = useState(1);
  const [baselineTotal, setBaselineTotal] = useState(0);
  const [baselineSearch, setBaselineSearch] = useState('');
  const [baselineQuery, setBaselineQuery] = useState('');
  const [targetId, setTargetId] = useState();
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [baselineId, setBaselineId] = useState();
  const [comparing, setComparing] = useState(false);
  const [comparison, setComparison] = useState(null);
  const mounted = useRef(false);
  const revision = useRef(0);
  const readPending = useRef(false);
  const actionPending = useRef(false);
  const baselinePending = useRef(false);
  const baselineReadRevision = useRef(0);
  const comparePending = useRef(false);
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

  const baselineJob = run?.job;
  const baselineVersion = run?.version;
  const baselinePhpVersion = run?.params?.php_version;
  const runState = run?.state;
  const readBaselines = useCallback(async () => {
    if (!allowed || !baselineJob) return;
    const request = ++baselineReadRevision.current;
    setBaselineLoading(true); setBaselineId(undefined); setComparison(null);
    try {
      const result = await http.get('/api/ops/baselines/', {params: {
        page: baselinePage, page_size: 100, job: baselineJob, version: baselineVersion,
        ...(baselinePhpVersion ? {php_version: baselinePhpVersion} : {}), q: baselineQuery,
      }});
      if (mounted.current && request === baselineReadRevision.current) {
        setBaselines(result.items || []); setBaselineTotal(result.total ?? (result.items || []).length); setBaselineError(false);
      }
    } catch (_) {if (mounted.current && request === baselineReadRevision.current) setBaselineError(true);}
    finally {if (mounted.current && request === baselineReadRevision.current) setBaselineLoading(false);}
  }, [allowed, baselineJob, baselineVersion, baselinePhpVersion, baselinePage, baselineQuery]);

  useEffect(() => {
    mounted.current = true;
    if (allowed) refresh();
    return () => {mounted.current = false; revision.current += 1; baselineReadRevision.current += 1; readPending.current = false; if (dialog.current) dialog.current.destroy();};
  }, [allowed, refresh]);

  useEffect(() => {readBaselines();}, [readBaselines]);

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
    setLoading(false); setAction(kind); setNotice(''); setComparison(null);
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

  async function saveBaseline() {
    const current = latest.current;
    const target = current.run?.targets?.find(item => item.id === targetId && item.state === 'success');
    if (!hasPermission('admin') || current.error || !target || !name.trim() || baselinePending.current) return;
    baselinePending.current = true; setSaving(true); setNotice('');
    try {
      await http.post('/api/ops/baselines/', {run_id: runId, target_id: targetId, name: name.trim()});
      if (mounted.current) {
        setName(''); setNotice('不可变基线已保存。');
        if (baselinePage !== 1) setBaselinePage(1); else await readBaselines();
      }
    } catch (_) {if (mounted.current) setNotice('基线保存结果未确认，请刷新基线列表核实。');}
    finally {baselinePending.current = false; if (mounted.current) setSaving(false);}
  }

  async function compare() {
    const selected = baselines.find(item => item.id === baselineId && compatibleBaseline(item, run));
    if (!selected || baselineLoading || baselineError || comparePending.current || !hasPermission('admin') || error) return;
    const snapshot = run;
    comparePending.current = true; setComparing(true); setNotice(''); setComparison(null);
    try {
      const result = await http.get(`/api/ops/runs/${runId}/diff/`, {params: {baseline_id: baselineId}});
      if (mounted.current) setComparison({...result, compared_at: new Date().toISOString(), run_snapshot: snapshot});
    } catch (_) {if (mounted.current) setNotice('差异读取失败，当前无法确认差异。');}
    finally {comparePending.current = false; if (mounted.current) setComparing(false);}
  }

  if (!allowed) return <Alert type="warning" message="仅超级管理员可访问运维作业。"/>;
  if (!run) return <Card title={`作业 #${runId}`}>{error ? <Alert type="error" message="作业读取失败。"/> : <Spin/>}<Button onClick={refresh} loading={loading}>刷新详情</Button></Card>;
  const targets = run.targets || [];
  const counts = run.counts || {};
  const visibleTargets = targets.filter(target => (!stateFilter || target.state === stateFilter) && (!categoryFilter || target.category === categoryFilter));
  const successTargets = targets.filter(target => target.state === 'success');
  const compatibleBaselines = baselines.filter(item => compatibleBaseline(item, run));
  const busy = Boolean(action);
  const warnings = targets.flatMap(target => target.checks || []).filter(check => check.status === 'warning').length;
  const unknownChecks = targets.flatMap(target => target.checks || []).filter(check => check.status === 'unknown').length;
  return <Card title={`作业 #${run.id} · ${run.job_name || run.job} · v${run.version}`}>
    <Space wrap style={{marginBottom: 16}}>
      <State value={run.state} labels={RUN_LABELS}/>
      <Button loading={loading} disabled={busy} onClick={() => {pollCount.current = 0; setPollStopped(false); setPollRevision(value => value + 1); refresh();}}>刷新详情</Button>
      <Button disabled={loading || busy} onClick={() => downloadReport(run, comparison, error)}>下载 JSON 报告</Button>
      {['paused', 'tripped'].includes(run.state) && <Button type="primary" disabled={error || busy || loading} loading={action === 'continue'} onClick={() => confirmAction('continue')}>继续下一批</Button>}
      {['queued', 'running', 'paused', 'tripped'].includes(run.state) && <Button danger disabled={error || busy} loading={action === 'cancel'} onClick={() => confirmAction('cancel')}>取消未执行项</Button>}
    </Space>
    {error && <Alert showIcon type="error" message="最新读取失败，当前显示上次快照；请刷新后操作。"/>}
    {notice && <Alert showIcon type="info" message={notice}/>}
    {run.state === 'tripped' && <Alert showIcon type="error" message="本批失败或待核实达到阈值，已熔断。核对原因后明确确认才能继续。"/>}
    <Typography.Paragraph type="secondary">每批 {run.batch_size} 台 · 批内失败阈值 {run.failure_threshold} 台 · {ACTIVE_STATES.includes(run.state) ? pollStopped ? '自动刷新已结束，请手动刷新。' : '每 5 秒刷新，最多 5 分钟。' : '当前状态采用手动刷新。'}</Typography.Paragraph>
    <Space wrap style={{marginBottom: 16}}>
      <strong>共 {counts.total ?? targets.length} 台</strong>
      {Object.entries(TARGET_LABELS).map(([state, label]) => <span key={state}>{label} {counts[state] || 0}</span>)}
    </Space>
    <Typography.Paragraph>指标需关注 {warnings} 项 · 指标未知 {unknownChecks} 项。采集成功仅表示取得结果，请同时核对指标状态。</Typography.Paragraph>
    <Space wrap style={{marginBottom: 16}}>
      <Select allowClear placeholder="结果状态" style={{width: 190}} value={stateFilter} onChange={setStateFilter} options={Object.entries(TARGET_LABELS).map(([value, label]) => ({value, label}))}/>
      <Select allowClear placeholder="失败分类" style={{width: 190}} value={categoryFilter} onChange={setCategoryFilter} options={Object.entries(CATEGORY_LABELS).map(([value, label]) => ({value, label}))}/>
    </Space>
    <Table resizeKey="operations-targets" rowKey="id" dataSource={visibleTargets} pagination={false} scroll={{x: 850}} columns={[
      {title: '主机', dataIndex: 'host', width: 270, render: hostName},
      {title: '采集状态', dataIndex: 'state', width: 140, render: state => <State value={state} labels={TARGET_LABELS}/>},
      {title: '失败分类', dataIndex: 'category', width: 170, render: category => category ? CATEGORY_LABELS[category] || '检查异常' : '—'},
      {title: '结果说明', dataIndex: 'message', width: 270, render: (value, target) => value || (target.state === 'success' ? '展开查看结构化检查项' : TARGET_LABELS[target.state])},
    ]} expandable={{expandedRowRender: target => target.checks?.length ? <Checks checks={target.checks}/> : <Typography.Text type="secondary">尚无可用检查项，不能判定指标正常。</Typography.Text>}}/>
    <Card size="small" title="配置基线与差异" style={{marginTop: 20}}>
      <Typography.Paragraph type="secondary">从成功采集的主机保存不可变基线。仅比较相同作业、版本及参数（含 PHP 分支）；缺失、采集失败与未知字段均需单独核实。</Typography.Paragraph>
      <Space wrap style={{marginBottom: 16}}>
        <Select placeholder="基线来源主机" style={{width: 280}} value={targetId} onChange={setTargetId} disabled={error || saving || busy}
          options={successTargets.map(target => ({value: target.id, label: hostName(target.host)}))}/>
        <Input placeholder="基线名称" maxLength={100} value={name} onChange={event => setName(event.target.value)} disabled={saving || busy}/>
        <Button disabled={!successTargets.some(target => target.id === targetId) || !name.trim() || error || busy} loading={saving} onClick={saveBaseline}>保存不可变基线</Button>
      </Space>
      <div><Space wrap>
        <Input placeholder="搜索基线名称" maxLength={100} style={{width: 200}} value={baselineSearch} onChange={event => setBaselineSearch(event.target.value)}/>
        <Button disabled={baselineLoading} onClick={() => {setBaselinePage(1); setBaselineQuery(baselineSearch.trim()); if (baselinePage === 1 && baselineQuery === baselineSearch.trim()) readBaselines();}}>搜索基线</Button>
        <Select placeholder="选择同版本基线" style={{width: 280}} value={baselineId} disabled={comparing || busy || baselineError || baselineLoading} onChange={value => {setBaselineId(value); setComparison(null);}}
          options={compatibleBaselines.map(item => ({value: item.id, label: `${item.name} · ${item.created_at || `#${item.id}`}`}))}/>
        <Button disabled={!compatibleBaselines.some(item => item.id === baselineId) || error || busy || baselineError || baselineLoading} loading={comparing} onClick={compare}>比较差异</Button>
        <Button loading={baselineLoading} onClick={readBaselines}>刷新基线</Button>
        <Button disabled={baselinePage <= 1 || baselineLoading} onClick={() => setBaselinePage(value => value - 1)}>上一页基线</Button>
        <span>第 {baselinePage} 页 · 共 {baselineTotal} 条</span>
        <Button disabled={baselinePage * 100 >= baselineTotal || baselineLoading} onClick={() => setBaselinePage(value => value + 1)}>下一页基线</Button>
      </Space></div>
      {baselineError && <Alert type="warning" message="基线列表读取失败，请刷新基线后再比较。"/>}
      {comparison && <div style={{marginTop: 16}}><Typography.Paragraph>基线：{comparison.baseline?.name} · 比较快照 {comparison.compared_at}（重新比较可更新）</Typography.Paragraph>
        {(comparison.items || []).map((item, index) => <Card key={item.host?.id || index} size="small" title={hostName(item.host)} style={{marginBottom: 12}}>
          <State value={item.state} labels={TARGET_LABELS}/>
          <Table resizeKey="operations-diff" rowKey="key" size="small" dataSource={item.changes || []} pagination={false} scroll={{x: 600}} columns={[
            {title: '检查项', dataIndex: 'label', width: 160},
            {title: '差异', dataIndex: 'state', width: 100, render: state => <State value={state} labels={DIFF_LABELS}/>},
            {title: '基线值', dataIndex: 'before', width: 180, render: displayValue},
            {title: '本次值', dataIndex: 'after', width: 180, render: (value, change) => change.state === 'missing' ? '缺失' : change.state === 'unknown' ? '未知' : displayValue(value)},
          ]}/>
        </Card>)}</div>}
    </Card>
    {Array.isArray(run.audit) && run.audit.length > 0 && <div style={{marginTop: 16}}><strong>作业记录</strong>{run.audit.map((item, index) =>
      <Typography.Paragraph key={index}>{item.created_at || item.at} · {({created: '创建', continue: '继续', cancel: '取消', tripped: '熔断', paused: '批次暂停', baseline_created: '创建基线'})[item.event] || '状态更新'} · {item.actor_id ? `用户 #${item.actor_id}` : '系统'}</Typography.Paragraph>)}</div>}
  </Card>;
}
