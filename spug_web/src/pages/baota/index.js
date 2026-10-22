import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Card, Col, Empty, Input, Modal, Row, Space, Table, Tag, Typography, message } from 'antd';
import { ExportOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { AuthDiv, Breadcrumb } from 'components';
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
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const canView = hasPermission('baota.panel.view');

  const refresh = useCallback(async () => {
    if (!canView) return;
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

  const columns = [
    {title: '面板名称', dataIndex: 'name', width: 160, ellipsis: true},
    {title: '面板地址', dataIndex: 'url', width: 260, ellipsis: true,
      render: url => <a href={url} target="_blank" rel="noopener noreferrer">{url}</a>},
    {title: '登录账户', dataIndex: 'username', width: 140, ellipsis: true},
    {title: '密码', dataIndex: 'has_password', width: 100,
      render: value => <Tag color={value ? 'green' : 'default'}>{value ? '已加密保存' : '未设置'}</Tag>},
    {title: '所属分组', dataIndex: 'group_id', width: 130,
      render: id => groups.find(group => group.id === id)?.name || '—'},
    {title: '备注', dataIndex: 'desc', width: 150, ellipsis: true},
    {title: '创建时间', dataIndex: 'created_at', width: 170},
    {title: '操作', key: 'actions', width: 210, fixed: 'right', render: (_, panel) => (
      <Space size={12}>
        <a href={panel.url} target="_blank" rel="noopener noreferrer"><ExportOutlined/> 打开</a>
        {hasPermission('baota.panel.edit') && <Button type="link" size="small" onClick={() => setRecord(panel)}>编辑</Button>}
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
            <Table rowKey="id" columns={columns} dataSource={rows} loading={loading} scroll={{x: 1420}}
                   locale={{emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={search ? '没有匹配的面板' : '还没有宝塔面板'}/>}}
                   pagination={{current: page, pageSize, showSizeChanger: true, total: rows.length,
                     showTotal: total => `共 ${total} 个面板`, onChange: (value, size) => { setPage(value); setPageSize(size); }}}/>
            <Typography.Text type="secondary">“打开”将进入宝塔登录页面。</Typography.Text>
          </Card>
        </Col>
      </Row>
      {record && <PanelForm record={record} groups={groups} selectedGroup={selected} onCancel={() => setRecord(null)}
                            onSaved={() => { setRecord(null); refresh(); }}/ >}
    </AuthDiv>
  );
}
