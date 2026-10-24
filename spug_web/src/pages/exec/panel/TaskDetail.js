import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Collapse, Empty, InputNumber, Modal, Space, Spin, Typography } from 'antd';
import { hasPermission, http } from 'libs';
import Evidence, { Status } from './TaskEvidence';
import ResolveForm from './ResolveForm';
import TaskItem from './TaskItem';
import UploadResults from './UploadResults';
import { OPERATION_LABELS, SSL_ACK, startPayload, summarizeTask } from './helpers';
import styles from './TaskDetail.module.css';

const COUNT_LABELS = {success: '成功', already: '已满足', failed: '失败', uncertain: '待核实', remote: '处理中',
  ready: '待执行', skipped: '已跳过', cancelled: '已取消', pending: '等待中'};

export default function TaskDetail({taskId, acknowledgements = {}, onTaskChange, onRetry}) {
  const [task, setTask] = useState(null);
  const [acknowledged, setAcknowledged] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [resolution, setResolution] = useState(null);
  const [batchEnabled, setBatchEnabled] = useState(true);
  const [batchSize, setBatchSize] = useState(1);
  const [failureThreshold, setFailureThreshold] = useState(1);
  const generation = useRef(0);
  const actionRevision = useRef(0);
  const actionPending = useRef(false);
  const latestChange = useRef(onTaskChange);
  latestChange.current = onTaskChange;
  useEffect(() => {
    const token = ++generation.current;
    let timer;
    setTask(null); setAcknowledged([]); setError(''); setBusy(false);
    setBatchEnabled(true); setBatchSize(1); setFailureThreshold(1);
    async function poll() {
      const revision = actionRevision.current;
      try {
        const result = await http.get(`/api/baota/tasks/${taskId}/`);
        if (token !== generation.current) return;
        if (revision === actionRevision.current && !actionPending.current) {
          setTask(result); setError('');
          if (latestChange.current) latestChange.current(result);
        }
        if (!['completed', 'cancelled'].includes(result.state)) timer = setTimeout(poll, 2500);
      } catch (_) {
        if (token === generation.current) {setError('任务读取失败，可刷新重试'); timer = setTimeout(poll, 6000);}
      }
    }
    poll();
    return () => {generation.current = token + 1; clearTimeout(timer);};
  }, [taskId]);

  async function command(action, values = {}) {
    if (actionPending.current) return;
    const token = generation.current;
    actionPending.current = true;
    actionRevision.current++;
    setBusy(true);
    try {
      const payload = action === 'start' ? startPayload(task, acknowledged,
        batchEnabled ? {batch_size: batchSize, failure_threshold: failureThreshold} : undefined) : values;
      const result = await http.post(`/api/baota/tasks/${task.id}/${action}/`, payload);
      if (token !== generation.current) return;
      if (action === 'retry') {if (onRetry) onRetry(result.id);}
      else setTask(result);
      if (onTaskChange) onTaskChange(result);
    } catch (_) { /* HTTP reports errors; retain the frozen preview. */ }
    finally {actionPending.current = false; if (token === generation.current) setBusy(false);}
  }
  if (!task) return error ? <Alert type="error" message={error}/> : <Spin/>;
  const counts = summarizeTask(task);
  const required = task.required_acknowledgements || [];
  const ready = task.state === 'prepared' && counts.ready > 0;
  const confirmed = required.every(code => acknowledged.includes(code));
  const batch = task.batch || {};
  const canContinue = batch.enabled && ['paused', 'tripped'].includes(batch.state) &&
    !batch.active && !batch.unresolved && (batch.pending > 0 || batch.remaining_in_batch > 0);
  const validPolicy = !batchEnabled || (Number.isInteger(batchSize) && batchSize >= 1 && batchSize <= 10 &&
    Number.isInteger(failureThreshold) && failureThreshold >= 1 && failureThreshold <= batchSize);
  return <Card title={`任务 #${task.id} · ${OPERATION_LABELS[task.operation] || task.operation}`}>
    {error && <Alert type="error" message={error}/>}
    <Space wrap><Status state={task.state}/><Typography.Text type="secondary">{(task.targets || []).length} 个面板</Typography.Text>
      {task.operation === 'file_upload' && <Typography.Text type="secondary">按文件统计</Typography.Text>}
      {Object.entries(COUNT_LABELS).filter(([key]) => counts[key] > 0).map(([key, label]) =>
        <Typography.Text key={key} type={key === 'failed' ? 'danger' : key === 'uncertain' ? 'warning' : undefined}>{label} {counts[key]}</Typography.Text>)}
    </Space>
    {ready && <Typography.Paragraph type="secondary">按本次预检范围执行；修改参数需重新预检。</Typography.Paragraph>}
    {task.retry_parent_id && <Typography.Paragraph type="secondary">重试来源：任务 #{task.retry_parent_id}</Typography.Paragraph>}
    {batch.enabled && <Alert showIcon type={batch.state === 'tripped' ? 'warning' : 'info'} style={{marginTop: 12}}
      message={`第 ${batch.index} / ${batch.total} 批 · 每批 ${batch.batch_size || 1} 个面板 · 后续 ${batch.pending || 0} 个面板`}
      description={batch.pause_reason || (batch.state === 'completed' ? '所有批次已结束，请查看逐项结果。' : '每批结束后暂停，核对结果后手动继续。')}/>}
    {task.operation.startsWith('ssl_') && <Alert showIcon type="warning" message="SSL 可能同步未选网站和面板证书，并重载 Web 服务。"/>}
    {task.operation === 'site_rewrite' && <Alert type="info" showIcon message="伪静态仅核实文件保存，运行状态仍需确认。"/>}
    <Space wrap style={{margin: '16px 0'}}>
      {batch.enabled && !batch.active && !batch.deployment_pending && hasPermission('exec.panel.execute') &&
        (batch.deployable || []).map(item => <Button key={`${item.target_id}:${item.child_key}`} loading={busy}
          onClick={() => Modal.confirm({title: '确认继续当前证书部署阶段？',
            content: `${item.panel_name || '当前面板'} · ${item.label || item.child_key}：只读核验已确认可部署。确认将保存证书并可能重载 Web 服务；其它面板和后续批次仍暂停。`,
            okText: '确认部署此证书', onOk: () => command('continue', {acknowledge: true,
              deploy: {target_id: item.target_id, child_key: item.child_key}})})}>确认继续当前证书部署阶段</Button>)}
      {canContinue && hasPermission('exec.panel.execute') && <Button type="primary" loading={busy}
        onClick={() => Modal.confirm({title: batch.requires_acknowledgement ? '确认已核对本批异常并继续？' : '核对本批结果并继续？',
          content: batch.requires_acknowledgement ? '本批已熔断。继续只放行尚未下发的项目，已下发项目不会重发。' : '继续放行下一批尚未下发的面板。',
          okText: '确认继续', onOk: () => command('continue', batch.requires_acknowledgement ? {acknowledge: true} : {})})}>核对结果并继续</Button>}
      {hasPermission('exec.panel.view') && <Button loading={busy} onClick={() => command('reconcile')}>只读核验</Button>}
      {hasPermission('exec.panel.cancel') && !['completed', 'cancelled'].includes(task.state) && <Button danger loading={busy}
        onClick={() => Modal.confirm({title: '取消尚未下发的项目？', content: '已发送的远端操作可能继续运行，请查看结果并只读核验。',
          okText: '取消未下发项', onOk: () => command('cancel')})}>取消未下发项</Button>}
      {hasPermission('exec.panel.create') && ['completed', 'uncertain', 'remote', 'cancelled'].includes(task.state) && <Button loading={busy}
        onClick={() => command('retry')}>关联新预检重试</Button>}
    </Space>
    {!(task.targets || []).length && <Empty description="暂无目标，等待预检"/>}
    <Collapse key={task.state === 'preflight_queued' ? 'preflight' : 'frozen'} defaultActiveKey={(task.targets || []).map(target => String(target.id))}>
      {(task.targets || []).map(target => <Collapse.Panel key={String(target.id)} header={<Space>{target.panel?.name || `面板 ${target.id}`}<Status state={target.state}/></Space>}>
        {target.message && (['failed', 'uncertain', 'remote'].includes(target.state) || !(target.children || []).length) &&
          <Alert type={target.state === 'failed' ? 'error' : 'warning'} message={target.message}/>}
        {(target.children || []).map(child => <div key={child.snapshot.key} className={styles.entry}>
            <TaskItem operation={task.operation} child={child}/>
            {task.operation !== 'file_upload' && hasPermission('exec.panel.manage') && ['uncertain', 'remote'].includes(child.state) && <Button style={{marginTop: 8}}
              onClick={() => setResolution({target, child})}>人工判定</Button>}
          </div>)}
        {task.operation === 'file_upload' && <UploadResults taskId={task.id} targetId={target.id} summary={target.summary?.upload}
          active={!['prepared', 'completed', 'cancelled'].includes(task.state)} refreshKey={`${task.state}:${target.state}`}
          onResolved={result => {actionRevision.current++; setTask(result); if (onTaskChange) onTaskChange(result);}}/>}
      </Collapse.Panel>)}
    </Collapse>
    {ready && <div style={{marginTop: 20}}>
      {required.map(code => <div key={code} style={{marginBottom: 12}}><Checkbox checked={acknowledged.includes(code)} onChange={event => {
        setAcknowledged(value => event.target.checked ? [...value, code] : value.filter(item => item !== code));
      }}>{acknowledgements[code] || (code === 'ssl_panel_sync' ? SSL_ACK : code === 'upload_overwrite' ? '确认按预检目录上传并覆盖同名文件；已上传的内容不会因取消而撤回。' : `确认影响项：${code}`)}</Checkbox></div>)}
      <div style={{marginBottom: 12}}><Checkbox checked={batchEnabled} onChange={event => setBatchEnabled(event.target.checked)}>分批执行，每批结束后核对结果</Checkbox></div>
      {batchEnabled && <Space wrap style={{marginBottom: 12}}>
        <Typography.Text>每批面板数</Typography.Text><InputNumber aria-label="每批面板数" min={1} max={10} precision={0} value={batchSize}
          onChange={value => {setBatchSize(value); if (value && failureThreshold > value) setFailureThreshold(value);}}/>
        <Typography.Text>失败熔断阈值</Typography.Text><InputNumber aria-label="失败熔断阈值" min={1} max={batchSize || 1} precision={0}
          value={failureThreshold} onChange={setFailureThreshold}/>
        <Typography.Text type="secondary">默认先验证一台；远端处理中或待核实会暂停后续下发。</Typography.Text>
      </Space>}
      <div>{hasPermission('exec.panel.execute') && <Button type="primary" disabled={!confirmed || !validPolicy} loading={busy} onClick={() => command('start')}>确认并开始执行</Button>}</div>
    </div>}
    <Collapse style={{marginTop: 16}}><Collapse.Panel key="audit" header="任务审计记录"><Evidence value={task.audit}/></Collapse.Panel></Collapse>
    {resolution && <ResolveForm taskId={task.id} {...resolution} onClose={() => setResolution(null)} onSaved={result => {
      actionRevision.current++; setTask(result);
    }}/>}
  </Card>;
}
