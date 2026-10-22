import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Collapse, Descriptions, Empty, Modal, Space, Spin, Tag, Typography } from 'antd';
import { hasPermission, http } from 'libs';
import Evidence, { Status } from './TaskEvidence';
import ResolveForm from './ResolveForm';
import { OPERATION_LABELS, SSL_ACK, startPayload, summarizeTask } from './helpers';

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
    <Space wrap><Status state={task.state}/><Typography.Text>
      成功 {counts.success} / 已满足 {counts.already} / 失败 {counts.failed} / 不确定 {counts.uncertain} / 远端处理中 {counts.remote} / 待执行 {counts.ready} / 跳过 {counts.skipped} / 已取消 {counts.cancelled}
    </Typography.Text></Space>
    <Typography.Paragraph type="secondary">预检快照固定于本任务。修改新建表单需重新预检；当前网站列表不会扩展本次执行范围。任务“已结束”不代表全部成功。</Typography.Paragraph>
    {task.retry_parent_id && <Typography.Paragraph>关联重试来源：任务 #{task.retry_parent_id}（本次仍需独立预检和确认）</Typography.Paragraph>}
    {task.operation.startsWith('ssl_') && <Alert showIcon type="warning" message="SSL 可能影响整个面板" description={SSL_ACK}/>}
    {task.operation === 'site_rewrite' && <Alert type="info" showIcon message="伪静态文件保存不代表运行时生效；仅文件回读的结果保持不确定，不自动重载。"/>}
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
        {target.message && <Alert type={target.state === 'failed' ? 'error' : 'info'} message={target.message}/>}
        <Descriptions bordered size="small" column={1}><Descriptions.Item label="预检汇总"><Evidence value={target.summary}/></Descriptions.Item></Descriptions>
        <Collapse style={{marginTop: 12}} defaultActiveKey={(target.children || []).map(child => child.snapshot.key)}>
          {(target.children || []).map(child => <Collapse.Panel key={child.snapshot.key} header={<Space>{child.snapshot.label}
            {task.operation === 'site_rewrite' && child.state === 'already' ? <Tag>配置文件一致，运行时未核实</Tag> : <Status state={child.state}/>}</Space>}>
            {child.snapshot.message && <Alert type={child.snapshot.state === 'failed' ? 'error' : 'info'} message={child.snapshot.message}/>}
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="实际目标 / 网站"><Evidence value={child.snapshot.target}/></Descriptions.Item>
              <Descriptions.Item label="冻结的当前状态"><Evidence value={child.snapshot.before}/></Descriptions.Item>
              <Descriptions.Item label="冻结的拟执行动作 / 影响范围"><Evidence value={child.snapshot.plan}/></Descriptions.Item>
              <Descriptions.Item label="执行结果与核验证据"><Evidence value={child.result}/></Descriptions.Item>
              <Descriptions.Item label="持久化下发记录"><Evidence value={child.intents}/></Descriptions.Item>
              <Descriptions.Item label="人工判定历史"><Evidence value={child.resolutions}/></Descriptions.Item>
            </Descriptions>
            {hasPermission('exec.panel.manage') && ['uncertain', 'remote'].includes(child.state) && <Button style={{marginTop: 8}}
              onClick={() => setResolution({target, child})}>人工判定</Button>}
          </Collapse.Panel>)}
        </Collapse>
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
