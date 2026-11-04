import React from 'react';
import { Redirect, useHistory } from 'react-router-dom';
import { Alert, Card, Select } from 'antd';
import { Breadcrumb } from 'components';
import { hasPermission } from 'libs';
import PanelWorkspace from './Workspace';
import Operations from '../operations';

export function LegacyOperations({location}) {
  if (!hasPermission('admin')) return null;
  const query = new URLSearchParams(location.search);
  query.set('type', 'inspection');
  return <Redirect to={{...location, pathname: '/exec/panel', search: `?${query.toString()}`}}/>;
}

export default function PanelTasks({location}) {
  const navigation = useHistory();
  const canInspect = hasPermission('admin'), canPanel = hasPermission('exec.panel.view');
  const query = new URLSearchParams(location.search);
  const type = canInspect && (query.get('type') === 'inspection' || !canPanel) ? 'inspection' : 'panel';
  const options = [canPanel && {value: 'panel', label: '宝塔操作'}, canInspect && {value: 'inspection', label: '主机巡检'}].filter(Boolean);
  function change(value) {
    if (!options.some(option => option.value === value)) return;
    const search = new URLSearchParams(location.search);
    if (value === 'inspection') search.set('type', value);
    else search.delete('type');
    navigation.push({...location, search: search.toString() ? `?${search.toString()}` : ''});
  }
  if (!canInspect && !canPanel) return <Alert showIcon type="warning" message="没有面板任务访问权限。"/>;
  return <>
    <Breadcrumb><Breadcrumb.Item>首页</Breadcrumb.Item><Breadcrumb.Item>批量执行</Breadcrumb.Item><Breadcrumb.Item>面板任务</Breadcrumb.Item></Breadcrumb>
    <Card size="small" style={{marginBottom: 16}}>
      <label htmlFor="panel-task-type" style={{marginRight: 12}}>任务类型</label>
      <Select id="panel-task-type" aria-label="任务类型" value={type} options={options} onChange={change} style={{width: 180}}/>
    </Card>
    {type === 'inspection' ? <Operations embedded location={location} navigation={navigation}/> : <PanelWorkspace location={location}/>}
  </>;
}
