import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Card, Col, Empty, Input, Modal, Row, Space, Tag, Typography, message } from 'antd';
import Table from 'components/ResizableTable';
import { ExportOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { AuthDiv, Breadcrumb } from 'components';
import ResourceMonitorSwitch, { ResourceMonitorProvider } from 'components/ResourceMonitorSwitch';
import ResourceMonitorButton from 'components/ResourceMonitorButton';
import { hasPermission, http } from 'libs';
import Group from './Group';
import PanelForm from './PanelForm';
import { filterPanels } from './helpers';
import styles from './index.module.less';

export default function BaotaIndex() {
  const [panels, setPanels] = useState([]);
  const [groups, setGroups] = useState([]);
  const [selected, setSelected] = useState(0);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [ready, setReady] = useState(false);
  const [record, setRecord] = useState(null);
  const [checkingIds, setCheckingIds] = useState({});
  const [openingIds, setOpeningIds] = useState({});
  const opening = useRef(new Set());
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [monitorRevision, setMonitorRevision] = useState(0);
  const canView = hasPermission('baota.panel.view');

  const refresh = useCallback(async () => {
    if (!canView) return;
    setMonitorRevision(value => value + 1);
    setLoading(true);
    try {
      const [newPanels, newGroups] = await Promise.all([http.get('/api/baota/'), http.get('/api/baota/group/')]);
      setPanels(newPanels);
      setGroups(newGroups);
      setReady(true);
      setSelected(value => value && !newGroups.some(group => group.id === value) ? 0 : value);
    } catch (_) {
      // HTTP client displays the error; keep the last successful list.
    } finally { setLoading(false); }
  }, [canView]);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => { setPage(1); }, [search, selected]);
  const rows = useMemo(() => filterPanels(panels, groups, selected, search), [panels, groups, selected, search]);
  useEffect(() => {
    setPage(value => Math.min(value, Math.max(1, Math.ceil(rows.length / pageSize))));
  }, [rows.length, pageSize]);

  function remove(panel) {
    Modal.confirm({
      title: `删除面板“${panel.name}”？`, content: '删除此处保存的面板记录及登录凭据。',
      okText: '删除', okButtonProps: {danger: true}, cancelText: '取消',
      onOk: () => http.delete('/api/baota/', {params: {id: panel.id}}).then(() => {
        message.success('面板已删除');
        return refresh();
      }),
    });
  }

  async function checkPanel(panel) {
    setCheckingIds(value => ({...value, [panel.id]: true}));
    try {
      const result = await http.post('/api/baota/connection/', {id: panel.id});
      setPanels(value => value.map(item => item.id === result.id ? result : item));
      message.success(`${panel.name} 连接成功，宝塔版本 ${result.api_version}`);
      await refresh();
    } catch (_) {
      // The shared HTTP client displays server errors.
    } finally { setCheckingIds(value => ({...value, [panel.id]: false})); }
  }

  async function openPanel(panel) {
    if (opening.current.has(panel.id)) return;
    if (!panel.api_base_url || !panel.has_api_key) {
      message.info('请先编辑此面板并配置 API 密钥，才能一键登录');
      return;
    }
    const popup = window.open('about:blank', '_blank');
    if (!popup) {
      message.info('请允许浏览器弹出窗口后重试');
      return;
    }
    opening.current.add(panel.id);
    setOpeningIds(value => ({...value, [panel.id]: true}));
    try {
      popup.opener = null;
      const policy = popup.document.createElement('meta');
      policy.name = 'referrer';
      policy.content = 'no-referrer';
      popup.document.head.appendChild(policy);
      popup.document.title = '正在登录宝塔…';
      const result = await http.post('/api/baota/login/', {id: panel.id});
      // Bind navigation to the saved API origin/prefix, even if a response is malformed.
      const expected = new URL(panel.api_base_url.replace(/\/$/, '') + '/login');
      let target;
      try {target = new URL(result.url);} catch (_) { /* handled below */ }
      if (!target || !['http:', 'https:'].includes(target.protocol) || target.username || target.password ||
          target.origin !== expected.origin || target.pathname !== expected.pathname || target.hash ||
          !/^\?tmp_token=[A-Za-z0-9_]{64}$/.test(target.search)) {
        message.error('登录地址校验失败，请刷新列表后重试');
        popup.close();
        return;
      }
      if (!popup.closed) {
        const link = popup.document.createElement('a');
        link.href = target.href;
        link.target = '_self';
        link.rel = 'noreferrer noopener';
        link.referrerPolicy = 'no-referrer';
        popup.document.body.appendChild(link);
        link.click();
      }
    } catch (_) {
      popup.close();
      // The shared HTTP client displays server errors without reflecting ticket data.
    } finally {
      opening.current.delete(panel.id);
      setOpeningIds(value => ({...value, [panel.id]: false}));
    }
  }

  const columns = [
    {title: '面板名称', dataIndex: 'name', width: 160, ellipsis: true},
    {title: 'IP 地址', dataIndex: 'url', width: 150, ellipsis: true,
      render: url => {try {return new URL(url).hostname.replace(/^\[|\]$/g, '');} catch (_) {return '—';}}},
    {title: '监控', key: 'monitor', width: 110, render: (_, panel) =>
      <Space size={8}><ResourceMonitorButton id={panel.id} name={panel.name}/>
        <ResourceMonitorSwitch id={panel.id} name={panel.name} canEdit={hasPermission('baota.panel.edit')}/></Space>},
    {title: '密码', dataIndex: 'has_password', width: 100,
      render: value => <Tag color={value ? 'green' : 'default'}>{value ? '已加密保存' : '未设置'}</Tag>},
    {title: 'API 连接', key: 'api', width: 190, render: (_, panel) => <div>
      <Tag color={panel.api_checked_at ? 'green' : panel.has_api_key && panel.api_base_url ? 'orange' : 'default'}>
        {panel.api_checked_at ? `宝塔 ${panel.api_version}` : panel.has_api_key && panel.api_base_url ? '待验证' : '未配置'}
      </Tag>
      {panel.api_checked_at && <div><Typography.Text type="secondary">上次检测：{panel.api_checked_at}</Typography.Text></div>}
    </div>},
    {title: '所属分组', dataIndex: 'group_id', width: 130,
      render: id => groups.find(group => group.id === id)?.name || '—'},
    {title: '备注', dataIndex: 'desc', width: 150, ellipsis: true},
    {title: '创建时间', dataIndex: 'created_at', width: 170},
    {title: '操作', key: 'actions', width: 280, fixed: 'right', render: (_, panel) => (
      <Space size={4} wrap>
        {hasPermission('baota.panel.login') && <Button type="link" size="small" icon={<ExportOutlined/>}
          loading={!!openingIds[panel.id]} onClick={() => openPanel(panel)}>打开</Button>}
        <a href={panel.url} target="_blank" rel="noopener noreferrer">登录页</a>
        {hasPermission('baota.panel.edit') && <Button type="link" size="small" onClick={() => setRecord(panel)}>编辑</Button>}
        {hasPermission('baota.panel.edit') && <Button type="link" size="small" loading={!!checkingIds[panel.id]}
          disabled={!panel.api_base_url || !panel.has_api_key} onClick={() => checkPanel(panel)}>测试连接</Button>}
        {hasPermission('baota.panel.del') && <Button type="link" danger size="small" onClick={() => remove(panel)}>删除</Button>}
      </Space>
    )},
  ];

  return (
    <AuthDiv auth="baota.panel.view">
      <Breadcrumb><Breadcrumb.Item>首页</Breadcrumb.Item><Breadcrumb.Item>宝塔面板管理</Breadcrumb.Item></Breadcrumb>
      <Row gutter={[12, 12]} className={styles.layout}>
        <Col xs={24} lg={6}>
          <Group groups={groups} selected={selected} onSelect={setSelected} onRefresh={refresh}/>
        </Col>
        <Col xs={24} lg={18}>
          <Card title="宝塔面板列表" className={styles.panels}>
            <div className={styles.toolbar}>
              <Input.Search allowClear placeholder="搜索名称、地址或账户" value={search}
                            onChange={event => setSearch(event.target.value)} className={styles.search}/>
              <Space>
                <Button icon={<ReloadOutlined/>} onClick={refresh} loading={loading}>刷新</Button>
                {hasPermission('baota.panel.add') && <Button type="primary" icon={<PlusOutlined/>}
                  disabled={!ready || !groups.length} onClick={() => setRecord({})}>添加面板</Button>}
              </Space>
            </div>
            {ready && !groups.length && <Typography.Paragraph type="secondary">
              {hasPermission('baota.group.manage') ? '先点击左侧“＋”新建分组，再添加宝塔面板。' : '请联系管理员创建分组后再添加面板。'}
            </Typography.Paragraph>}
            <ResourceMonitorProvider kind="panel" revision={monitorRevision} enabled={canView && ready}>
            <Table resizeKey="pages/baota/index:1" rowKey="id" columns={columns} dataSource={rows} loading={loading} scroll={{x: 1500}}
                   locale={{emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={search ? '没有匹配的面板' : '还没有宝塔面板'}/>}}
                   pagination={{current: page, pageSize, showSizeChanger: true, total: rows.length,
                     showTotal: total => `共 ${total} 个面板`, onChange: (value, size) => { setPage(value); setPageSize(size); }}}/>
            </ResourceMonitorProvider>
            <Typography.Text type="secondary">“打开”通过已保存的 API 一键登录宝塔主管理员；“登录页”用于手动登录。</Typography.Text>
          </Card>
        </Col>
      </Row>
      {record && <PanelForm record={record} groups={groups} selectedGroup={selected} onCancel={() => setRecord(null)}
                            onChecked={refresh} onSaved={() => { setRecord(null); refresh(); }}/ >}
    </AuthDiv>
  );
}
