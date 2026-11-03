import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Collapse, Empty, Space, Spin, Typography } from 'antd';
import { hasPermission, http } from 'libs';
import TaskItem from '../exec/panel/TaskItem';
import { Status } from '../exec/panel/TaskEvidence';
import { OPERATION_LABELS } from '../exec/panel/helpers';

export default function PanelPreview({taskId}) {
  const [task, setTask] = useState(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const canView = hasPermission('exec.panel.view');
  useEffect(() => {
    if (!canView) return undefined;
    let valid = true, timer, attempts = 0;
    setTask(null); setError('');
    async function poll() {
      attempts++;
      try {
        const result = await http.get(`/api/baota/tasks/${taskId}/`);
        if (!valid) return;
        setTask(result);
        if (['preflight_queued', 'preflighting', 'running', 'queued', 'dispatching', 'reconciling', 'remote', 'paused'].includes(result.state)) {
          if (attempts < 240) timer = setTimeout(poll, 2500);
          else setError('任务仍在处理中，已停止自动刷新。请稍后刷新查看结果。');
        }
      } catch (_) {if (valid) setError('面板任务读取失败，请刷新重试');}
    }
    poll();
    return () => {valid = false; clearTimeout(timer);};
  }, [taskId, canView, revision]);
  if (!canView) return <Alert type="warning" message="需要面板任务查看权限才能查看预检结果"/>;
  return <div>
    <Alert showIcon type="info" message="只读预检与结果预览" description="此处只读取冻结目标及逐项结果。重新预检不会开始执行，也不会更改计划的调度规则。"/>
    <Button style={{margin: '12px 0'}} onClick={() => setRevision(value => value + 1)}>刷新结果</Button>
    {error && <Alert showIcon type="error" message={error}/>}
    {!task ? !error && <Spin/> : <Card style={{marginTop: 12}} title={`任务 #${task.id} · ${OPERATION_LABELS[task.operation] || task.operation}`}>
      <Space wrap><Status state={task.state}/><Typography.Text>{(task.targets || []).length} 个面板</Typography.Text></Space>
      {task.message && <Typography.Paragraph>{task.message}</Typography.Paragraph>}
      {!(task.targets || []).length && <Empty description="等待预检目标"/>}
      <Collapse defaultActiveKey={(task.targets || []).map(target => String(target.id))} style={{marginTop: 12}}>
        {(task.targets || []).map(target => <Collapse.Panel key={String(target.id)} header={<Space>{target.panel?.name || `面板 #${target.id}`}<Status state={target.state}/></Space>}>
          {target.message && <Typography.Paragraph>{target.message}</Typography.Paragraph>}
          {(target.children || []).map((child, index) => <div key={child.snapshot?.key || index} style={{marginBottom: 16}}><TaskItem operation={task.operation} child={child}/></div>)}
        </Collapse.Panel>)}
      </Collapse>
    </Card>}
  </div>;
}
