import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Select, Space, Tag } from 'antd';
import ResizableTable from '../../components/ResizableTable';
import { hasPermission, http } from 'libs';
import { API, safeError, STAGES, time } from './common';
import ReminderDismiss, { ReminderControls } from '../../components/ReminderDismiss';
import { serverReminder, usePersonalReminders } from '../../components/personalReminders';

export default function Reminders({revision, onCount}) {
  const prefs = usePersonalReminders();
  const [page, setPage] = useState({page: 1, page_size: 20}), [filters, setFilters] = useState({});
  const [data, setData] = useState(null), [error, setError] = useState(''), [refresh, setRefresh] = useState(0);
  const [reading, setReading] = useState(null), pending = useRef(false), mounted = useRef(true);
  useEffect(() => () => {mounted.current = false;}, []);
  useEffect(() => {
    let valid = true; setError(''); setData(null);
    http.get(`${API}reminders/`, {params: {...page, ...filters}}).then(result => {
      if (!valid) return;
      onCount(result.unread_count || 0);
      const last = Math.max(1, Math.ceil(result.total / page.page_size));
      if (page.page > last) setPage(current => ({...current, page: last}));
      else setData(result);
    },
      failure => {if (valid) setError(safeError(failure, '读取站内提醒失败，请重试。'));});
    return () => {valid = false;};
  }, [page, filters, revision, refresh, onCount]);
  function filter(key, input) {setFilters(current => {const next = {...current}; if (input) next[key] = input; else delete next[key]; return next;}); setPage(current => ({...current, page: 1}));}
  async function read(id) {
    if (pending.current || !hasPermission('admin')) return;
    pending.current = true; setReading(id); setError('');
    try {
      await http.post(`${API}reminders/read/`, {ids: [id]});
      if (mounted.current) setRefresh(current => current + 1);
    } catch (failure) {if (mounted.current) setError(safeError(failure, '标记已读失败，请重试。'));}
    finally {pending.current = false; if (mounted.current) setReading(null);}
  }
  return <>
    <Alert showIcon type="info" message="仅管理员站内提醒，已读状态按个人保存。" description="历史提醒不会随续费删除；“已过时”表示日期、阶段、关联或提醒配置已变化。" style={{marginBottom: 16}}/>
    <Space wrap style={{marginBottom: 16}}>
      <Select aria-label="提醒已读状态" style={{width: 140}} allowClear placeholder="全部提醒" value={filters.unread}
        onChange={input => filter('unread', input)} options={[{value: '1', label: '仅未读'}]}/>
      <Select aria-label="提醒有效状态" style={{width: 160}} allowClear placeholder="含历史提醒" value={filters.current}
        onChange={input => filter('current', input)} options={[{value: '1', label: '仅当前有效提醒'}]}/>
      <Button onClick={() => setRefresh(current => current + 1)}>刷新提醒</Button>
      <ReminderControls/>
    </Space>
    {error && <Alert type="error" message={error} style={{marginBottom: 16}}/>}
    {(data?.items || []).some(row => prefs.isMuted(serverReminder(row))) && <p>本页已隐藏个人忽略项；总数和未读数仍为原始记录，可在“已忽略”中恢复。</p>}
    <ResizableTable resizeKey="expiry-reminders" rowKey="id" dataSource={(data?.items || []).filter(row => !prefs.isMuted(serverReminder(row)))} loading={!data && !error}
      locale={{emptyText: '暂无站内提醒；仅已开启提醒的资产会在每日检查后生成。'}} scroll={{x: 1000}} columns={[
        {title: '服务器', key: 'server', width: 200, render: (_, row) => <><div>{row.name}</div><div>{row.address}</div></>},
        {title: '提醒', dataIndex: 'stage', width: 180, render: (stage, row) => <><Tag color={row.current ? 'orange' : 'default'}>{STAGES[stage] || '到期提醒'}</Tag>
          {!row.current && <Tag>已过时</Tag>}</>},
        {title: '到期日', dataIndex: 'expires_on', width: 140},
        {title: '生成时间', dataIndex: 'created_at', width: 190, render: time},
        {title: '我的阅读状态', key: 'read', width: 140, render: (_, row) => row.unread ?
          <Button type="link" loading={reading === row.id} disabled={reading !== null} onClick={() => read(row.id)}>标记已读</Button> : '已读'},
        {title: '提醒操作', key: 'dismiss', width: 120, render: (_, row) => <ReminderDismiss item={serverReminder(row)}/>},
      ]} pagination={{current: page.page, pageSize: page.page_size, total: data?.total || 0, showSizeChanger: false,
        onChange: next => setPage(current => ({...current, page: next}))}}/>
  </>;
}
