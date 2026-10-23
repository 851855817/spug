import React, { createContext, useContext } from 'react';
import { Switch } from 'antd';
import useResourceMonitorSources from './useResourceMonitorSources';

const MonitorContext = createContext(null);

export function ResourceMonitorProvider({kind, revision, enabled = true, children}) {
  const state = useResourceMonitorSources(kind, revision, enabled);
  return <MonitorContext.Provider value={state}>{children}</MonitorContext.Provider>;
}

export default function ResourceMonitorSwitch({id, canEdit, name}) {
  const monitor = useContext(MonitorContext);
  const source = monitor?.sources[id];
  const error = monitor?.errors[id];
  return <span>
    <Switch size="small" aria-label={`${name || ''}监控`} checked={source?.enabled === true}
      disabled={!canEdit || !monitor?.enabled || !source} loading={!!monitor?.saving[id]}
      onChange={enabled => monitor.save(id, enabled)}/>
    {error && <small role="alert" style={{display: 'block', color: '#cf1322'}}>保存失败，请重试</small>}
    {monitor?.loadError && <small role="alert" style={{display: 'block', color: '#cf1322'}}>状态加载失败，请刷新</small>}
  </span>;
}
