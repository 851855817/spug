import React, { useState } from 'react';
import { Button, Tooltip, message } from 'antd';
import { MonitorOutlined } from '@ant-design/icons';
import { hasPermission } from 'libs';
import { useResourceMonitorContext } from './ResourceMonitorSwitch';
import ResourceMonitorDetail from './ResourceMonitorDetail';

export default function ResourceMonitorButton({id, name}) {
  const monitor = useResourceMonitorContext();
  const source = monitor?.sources[id];
  const [opened, setOpened] = useState(false);
  function open() {
    if (!source || monitor.loadError) {message.info('监控状态加载失败，请刷新列表'); return;}
    if (!source.enabled) {message.info('未开启监控'); return;}
    if (!hasPermission('monitor.monitor.view')) {message.info('没有查看监控的权限'); return;}
    setOpened(true);
  }
  return <>
    <Tooltip title={source?.enabled ? '查看监控' : '未开启监控'}>
      <Button type="text" size="small" aria-label={`查看${name || ''}监控`} icon={<MonitorOutlined/>}
        style={{color: source?.enabled ? '#2563fc' : '#98a2b3'}} disabled={!monitor?.enabled || !!monitor?.saving[id]}
        onClick={open}/>
    </Tooltip>
    {opened && <ResourceMonitorDetail kind={monitor.kind} id={id} name={name} onCancel={() => setOpened(false)}/>}
  </>;
}
