import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Space, Spin, Tabs } from 'antd';
import { AuthDiv, Breadcrumb } from 'components';
import { hasPermission, http } from 'libs';
import Catalog from './Catalog';
import Records from './Records';
import TaskDetail from './TaskDetail';
import TaskForm from './TaskForm';

export default function PanelTasks() {
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
  return <AuthDiv auth="exec.panel.view">
    <Breadcrumb><Breadcrumb.Item>首页</Breadcrumb.Item><Breadcrumb.Item>批量执行</Breadcrumb.Item><Breadcrumb.Item>面板任务</Breadcrumb.Item></Breadcrumb>
    <Card>
      <Tabs activeKey={tab} onChange={setTab}>
        {hasPermission('exec.panel.create') && <Tabs.TabPane key="create" tab="新建面板任务">
          {error ? <Space><Alert type="error" message={error}/><Button onClick={() => setRefresh(value => value + 1)}>重试读取</Button></Space> :
            options ? <TaskForm options={options} onPrepared={open}/> : <Spin/>}
        </Tabs.TabPane>}
        <Tabs.TabPane key="records" tab="执行记录"><Records revision={revision} onOpen={open}/></Tabs.TabPane>
        <Tabs.TabPane key="catalog" tab="官方接口目录（只读）"><Catalog/></Tabs.TabPane>
      </Tabs>
    </Card>
    <div style={{marginTop: 16}}>{taskId ? <TaskDetail key={taskId} taskId={taskId} acknowledgements={options?.acknowledgements} onRetry={open}/> :
      <Card><Empty description="生成预检或从执行记录打开任务，核对冻结快照及逐项结果"/></Card>}</div>
  </AuthDiv>;
}
