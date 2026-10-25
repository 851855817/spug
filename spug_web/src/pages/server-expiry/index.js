import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Input, Select, Space, Tabs, Tag, Typography } from 'antd';
import { AuthDiv, Breadcrumb } from 'components';
import ResizableTable from '../../components/ResizableTable';
import { hasPermission, http } from 'libs';
import Editor from './Editor';
import History from './History';
import Reminders from './Reminders';
import { API, expiryLabel, ownerName, safeError, STATES, time } from './common';

export default function ServerExpiry() {
  const allowed = hasPermission('admin');
  const [options, setOptions] = useState({owners: [], groups: [], timezone: 'Asia/Shanghai', schedule: '09:00'});
  const [data, setData] = useState(null), [filters, setFilters] = useState({}), [page, setPage] = useState({page: 1, page_size: 20});
  const [error, setError] = useState(''), [optionsError, setOptionsError] = useState(''), [revision, setRevision] = useState(0);
  const [tab, setTab] = useState('assets'), [editor, setEditor] = useState(null), [history, setHistory] = useState(null);
  const [unread, setUnread] = useState(0);
  useEffect(() => {
    if (!allowed) return;
    let valid = true; setOptionsError('');
    http.get(`${API}options/`).then(result => {if (valid) setOptions(result);},
      failure => {if (valid) setOptionsError(safeError(failure, '读取负责人及分组选项失败，请刷新重试。'));});
    return () => {valid = false;};
  }, [allowed, revision]);
  useEffect(() => {
    if (!allowed) return;
    let valid = true; setError(''); setData(null);
    http.get(`${API}assets/`, {params: {...page, ...filters}}).then(result => {
      if (!valid) return;
      const last = Math.max(1, Math.ceil(result.total / page.page_size));
      if (page.page > last) setPage(current => ({...current, page: last}));
      else {setData(result); setUnread(result.unread_count || 0);}
    },
      failure => {if (valid) setError(safeError(failure, '读取到期信息失败，请刷新重试。'));});
    return () => {valid = false;};
  }, [allowed, page, filters, revision]);
  function filter(key, input) {
    setFilters(current => {const next = {...current}; if (input !== undefined && input !== '') next[key] = input; else delete next[key]; return next;});
    setPage(current => ({...current, page: 1}));
  }
  const refresh = () => setRevision(current => current + 1);
  if (!allowed) return <Alert type="warning" message="仅超级管理员可访问到期管理。"/>;
  const summary = data?.summary || {}, check = data?.check || options.check || {};
  const today = data?.today || options.today;
  const columns = [
    {title: '服务器 / 来源', key: 'name', width: 230, render: (_, row) => <><div><b>{row.name}</b></div><div>{row.address || '地址未登记'}</div>
      <Typography.Text type="secondary">{row.sources.map(source => `${source.kind === 'host' ? '主机' : '宝塔'}：${source.name}`).join('、')}</Typography.Text></>},
    {title: '分组', key: 'groups', width: 150, render: (_, row) => row.groups.map(group => group.name).join('、') || '未分组'},
    {title: '到期信息', key: 'expiry', width: 235, render: (_, row) => <><Tag color={row.state === 'expired' ? 'red' : row.days_remaining != null && row.days_remaining <= 30 ? 'orange' : 'default'}>{expiryLabel(row)}</Tag>
      <div>{row.expires_on || '—'} · {row.date_source === 'manual' ? '人工登记' : row.date_source === 'host_record' ? '主机已有记录' : '未登记'}</div>
      {row.date_source === 'manual' && <Typography.Text type="secondary">原记录：{row.original_expiry || '未登记'}</Typography.Text>}</>},
    {title: '负责人 / 跟进', key: 'owner', width: 180, render: (_, row) => <><div>{ownerName(row.owner)}</div><Tag>{row.followup === 'contacted' ? '已联系' : '待跟进'}</Tag></>},
    {title: '站内提醒', key: 'reminder', width: 115, render: (_, row) => row.exempt ? '无需管理' : row.reminder_enabled ? <Tag color="blue">已开启</Tag> : '未开启'},
    {title: '备注', dataIndex: 'note', width: 200, render: text => text || '—'},
    {title: '操作', key: 'actions', width: 250, render: (_, row) => <Space>
      <Button type="link" disabled={Boolean(optionsError)} onClick={() => setEditor({asset: row, mode: 'edit'})}>编辑</Button>
      <Button type="link" disabled={Boolean(optionsError) || row.exempt} onClick={() => setEditor({asset: row, mode: 'renew'})}>登记续费</Button>
      <Button type="link" onClick={() => setHistory(row)}>记录</Button>
    </Space>},
  ];
  return <AuthDiv auth="admin">
    <Breadcrumb extra={<Space><Button href="/host">主机列表</Button><Button onClick={refresh}>刷新</Button></Space>}>
      <Breadcrumb.Item>主机管理</Breadcrumb.Item><Breadcrumb.Item>到期管理</Breadcrumb.Item>
    </Breadcrumb>
    <Card>
      <Space wrap style={{marginBottom: 16}}>
        {[['total', '资产'], ['expired', '已过期'], ['within_7', '7 天内'], ['within_30', '30 天内'], ['unset', '未登记'], ['invalid', '日期无效']]
          .map(([key, label]) => <Tag key={key} color={key === 'expired' ? 'red' : 'default'}>{label}：{summary[key] ?? '—'}</Tag>)}
      </Space>
      <Typography.Paragraph type="secondary">7／30 天为包含今天的累计数量，不含已过期。仅使用平台已有数据，查看和刷新不会连接客户服务器。</Typography.Paragraph>
      <Alert type={['error', 'stale'].includes(check.status) ? 'warning' : 'info'} showIcon
        message={`站内提醒默认关闭 · 每日 ${options.schedule || '09:00'}（${options.timezone}）检查`}
        description={<>{check.message || '启用后仅在本页生成管理员站内提醒，不发送外部消息。'} 最近成功：{time(check.last_success_at)}。
          调度器停止期间不会生成提醒；可在平台健康页检查服务。</>} style={{marginBottom: 16}}/>
      <Tabs activeKey={tab} onChange={setTab}>
        <Tabs.TabPane tab="到期资产" key="assets">
          <Space wrap style={{marginBottom: 16}}>
            <Select aria-label="资产来源" style={{width: 125}} allowClear placeholder="全部来源" value={filters.source} onChange={input => filter('source', input)}
              options={[{value: 'host', label: 'SSH 主机'}, {value: 'panel', label: '含宝塔面板'}]}/>
            <Select aria-label="资产分组" style={{width: 170}} allowClear showSearch optionFilterProp="label" placeholder="全部分组" value={filters.group}
              onChange={input => filter('group', input)} options={options.groups.map(group => ({value: group.key, label: `${group.key.startsWith('host:') ? '主机' : '宝塔'} / ${group.name}`}))}/>
            <Select aria-label="到期状态" style={{width: 150}} allowClear placeholder="全部到期状态" value={filters.state} onChange={input => filter('state', input)}
              options={Object.entries(STATES).map(([value, label]) => ({value, label}))}/>
            <Select aria-label="资产负责人" style={{width: 150}} allowClear placeholder="全部负责人" value={filters.owner_id} onChange={input => filter('owner_id', input)}
              options={options.owners.map(owner => ({value: owner.id, label: owner.name}))}/>
            <Select aria-label="资产跟进状态" style={{width: 130}} allowClear placeholder="全部跟进状态" value={filters.followup} onChange={input => filter('followup', input)}
              options={[{value: 'pending', label: '待跟进'}, {value: 'contacted', label: '已联系'}]}/>
            <Input.Search aria-label="搜索到期资产" placeholder="名称、IP 或主机名" allowClear maxLength={100} style={{width: 220}} onSearch={input => filter('q', input.trim())}/>
          </Space>
          {(error || optionsError) && <Alert type="error" message={error || optionsError} style={{marginBottom: 16}}/>}
          <ResizableTable resizeKey="server-expiry" rowKey="key" size="middle" columns={columns} dataSource={data?.items || []}
            loading={!data && !error} scroll={{x: 1360}} locale={{emptyText: '没有符合条件的到期资产。未登记日期的服务器可在“编辑”中补充。'}}
            pagination={{current: page.page, pageSize: page.page_size, total: data?.total || 0, showSizeChanger: true, pageSizeOptions: ['20', '50', '100'],
              showTotal: total => `共 ${total} 项`, onChange: (next, pageSize) => setPage({page: pageSize === page.page_size ? next : 1, page_size: pageSize})}}/>
        </Tabs.TabPane>
        <Tabs.TabPane tab={`站内提醒 (${unread})`} key="reminders">
          {tab === 'reminders' && <Reminders revision={revision} onCount={setUnread}/>}
        </Tabs.TabPane>
      </Tabs>
    </Card>
    {editor && <Editor key={`${editor.asset.key}:${editor.mode}`} {...editor} options={{...options, today}}
      onClose={() => setEditor(null)} onSaved={() => {setEditor(null); refresh();}}/>}
    {history && <History asset={history} onClose={() => setHistory(null)}/>}
  </AuthDiv>;
}
