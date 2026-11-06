import React, {createContext, useCallback, useContext, useEffect, useRef, useState} from 'react';
import {Alert, Button, Spin} from 'antd';
import http from 'libs/http';
import {updatePermissions} from 'libs/functools';
import {createResourceAccess} from './access';
export const ResourceAccessContext = createContext(createResourceAccess());
export const useResourceAccess = () => useContext(ResourceAccessContext);
export function ResourceAccessProvider({children}) {
  const [data, setData] = useState(null), [error, setError] = useState(false);
  const identity = useRef(`${localStorage.getItem('id')}:${localStorage.getItem('token')}`);
  const generation = useRef(0);
  const refresh = useCallback(async () => {
    const request = ++generation.current;
    try {
      const result = await http.get('/api/account/resources/');
      if (request !== generation.current || identity.current !== `${localStorage.getItem('id')}:${localStorage.getItem('token')}`) return;
      localStorage.setItem('permissions', JSON.stringify(result.permissions || []));
      localStorage.setItem('is_supper', String(result.is_supper === true));
      updatePermissions(); setData(result); setError(false);
    } catch (_) {if (request === generation.current) {setData(null); setError(true);}}
  }, []);
  useEffect(() => {
    const requests = generation;
    refresh(); window.addEventListener('focus', refresh);
    window.addEventListener('resource-access-changed', refresh);
    return () => {requests.current++; window.removeEventListener('focus', refresh); window.removeEventListener('resource-access-changed', refresh);};
  }, [refresh]);
  if (error) return <Alert type="error" message="读取当前权限与资源额度失败" action={<Button onClick={refresh}>重试</Button>}/>;
  if (!data) return <Spin tip="读取权限与资源额度"/>;
  const access = {...createResourceAccess(data), refresh};
  return <ResourceAccessContext.Provider value={access}>
    <React.Fragment key={`${data.is_supper}:${(data.permissions || []).join(',')}`}>{children}</React.Fragment>
  </ResourceAccessContext.Provider>;
}
