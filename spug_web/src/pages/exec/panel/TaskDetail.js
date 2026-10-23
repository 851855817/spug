import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Collapse, Empty, Modal, Space, Spin, Typography } from 'antd';
import { hasPermission, http } from 'libs';
import Evidence, { Status } from './TaskEvidence';
import ResolveForm from './ResolveForm';
import TaskItem from './TaskItem';
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
  const generation = useRef(0);
  const actionRevision = useRef(0);
  const actionPending = useRef(false);
  const latestChange = useRef(onTaskChange);
  latestChange.current = onTaskChange;
  useEffect(() => {
    const token = ++generation.current;
    let timer;
    setTask(null); setAcknowledged([]); setError(''); setBusy(false);
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

  async function command(action) {
    if (actionPending.current) return;
    const token = generation.current;
    actionPending.current = true;
    actionRevision.current++;
    setBusy(true);
    try {
      const payload = action === 'start' ? startPayload(task, acknowledged) : {};
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
  return <Card title={`任务 #${task.id} · ${OPERATION_LABELS[task.operation] || task.operation}`}>
    {error && <Alert type="error" message={error}/>}
    <Space wrap><Status state={task.state}/><Typography.Text type="secondary">{(task.targets || []).length} 个面板</Typography.Text>
      {Object.entries(COUNT_LABELS).filter(([key]) => counts[key] > 0).map(([key, label]) =>
        <Typography.Text key={key} type={key === 'failed' ? 'danger' : key === 'uncertain' ? 'warning' : undefined}>{label} {counts[key]}</Typography.Text>)}
    </Space>
    {ready && <Typography.Paragraph type="secondary">按本次预检范围执行；修改参数需重新预检。</Typography.Paragraph>}
    {task.retry_parent_id && <Typography.Paragraph type="secondary">重试来源：任务 #{task.retry_parent_id}</Typography.Paragraph>}
    {task.operation.startsWith('ssl_') && <Alert showIcon type="warning" message="SSL 可能同步未选网站和面板证书，并重载 Web 服务。"/>}
    {task.operation === 'site_rewrite' && <Alert type="info" showIcon message="伪静态仅核实文件保存，运行状态仍需确认。"/>}
    <Space wrap style={{margin: '16px 0'}}>
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
            {hasPermission('exec.panel.manage') && ['uncertain', 'remote'].includes(child.state) && <Button style={{marginTop: 8}}
              onClick={() => setResolution({target, child})}>人工判定</Button>}
          </div>)}
      </Collapse.Panel>)}
    </Collapse>
    {ready && <div style={{marginTop: 20}}>
      {required.map(code => <div key={code} style={{marginBottom: 12}}><Checkbox checked={acknowledged.includes(code)} onChange={event => {
        setAcknowledged(value => event.target.checked ? [...value, code] : value.filter(item => item !== code));
      }}>{acknowledgements[code] || (code === 'ssl_panel_sync' ? SSL_ACK : `确认影响项：${code}`)}</Checkbox></div>)}
      {hasPermission('exec.panel.execute') && <Button type="primary" disabled={!confirmed} loading={busy} onClick={() => command('start')}>确认并开始执行</Button>}
    </div>}
    <Collapse style={{marginTop: 16}}><Collapse.Panel key="audit" header="任务审计记录"><Evidence value={task.audit}/></Collapse.Panel></Collapse>
    {resolution && <ResolveForm taskId={task.id} {...resolution} onClose={() => setResolution(null)} onSaved={result => {
      actionRevision.current++; setTask(result);
    }}/>}
  </Card>;
}
