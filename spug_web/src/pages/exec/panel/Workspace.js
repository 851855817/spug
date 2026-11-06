import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Space, Spin, Tabs } from 'antd';

import { hasPermission, http } from 'libs';
import Records from './Records';
import TaskDetail from './TaskDetail';
import TaskForm from './TaskForm';

export default function PanelWorkspace({location}) {
  const [options, setOptions] = useState(null);
  const [error, setError] = useState('');
  const [taskId, setTaskId] = useState(null);
  const [revision, setRevision] = useState(0);
  const [tab, setTab] = useState(hasPermission('exec.panel.create') ? 'create' : 'records');
  const [refresh, setRefresh] = useState(0);
  const canView = hasPermission('exec.panel.view');
  useEffect(() => {
    if (!canView) return;
    let valid = true;
    setError('');
    http.get('/api/baota/task-options/').then(result => {if (valid) setOptions(result);}, () => {if (valid) setError('读取面板任务选项失败');});
    return () => {valid = false;};
  }, [canView, refresh]);
  function open(id) {setTaskId(id); setRevision(value => value + 1);}
  if (!canView) return null;
  return <>
    <Card>
      <Tabs activeKey={tab} onChange={setTab}>
        {hasPermission('exec.panel.create') && <Tabs.TabPane key="create" tab="新建任务">
          {error ? <Space><Alert type="error" message={error}/><Button onClick={() => setRefresh(value => value + 1)}>重试读取</Button></Space> :
            options ? <TaskForm key={location?.search || ''} options={options} initialSearch={location?.search || ''} onPrepared={open}/> : <Spin/>}
        </Tabs.TabPane>}
        <Tabs.TabPane key="records" tab="执行记录"><Records revision={revision} onOpen={open}/></Tabs.TabPane>
      </Tabs>
    </Card>
    <div style={{marginTop: 16}}>{taskId ? <TaskDetail key={taskId} taskId={taskId} acknowledgements={options?.acknowledgements} onRetry={open}/> :
      <Card><Empty description="生成预检或从执行记录打开任务，核对冻结快照及逐项结果"/></Card>}</div>
  </>;
}
