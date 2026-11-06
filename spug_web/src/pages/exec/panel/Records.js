import React, { useEffect, useState } from 'react';
import { Button, Select, Space } from 'antd';
import Table from 'components/ResizableTable';
import { http } from 'libs';
import { OPERATION_LABELS, STATE_LABELS } from './helpers';
import { Status } from './TaskEvidence';

export default function Records({revision, onOpen}) {
  const [query, setQuery] = useState({page: 1, page_size: 20});
  const [data, setData] = useState({items: [], total: 0});
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let valid = true;
    setLoading(true);
    http.get('/api/baota/tasks/', {params: query}).then(result => {if (valid) setData(result);}, () => {})
      .finally(() => {if (valid) setLoading(false);});
    return () => {valid = false;};
  }, [query, revision, refresh]);
  const states = ['preflight_queued', 'prepared', 'running', 'paused', 'remote', 'uncertain', 'cancelled', 'completed'];
  return <div>
    <Space wrap style={{marginBottom: 16}}>
      <Select allowClear placeholder="操作筛选" style={{width: 180}} value={query.operation} onChange={operation => setQuery({...query, operation, page: 1})}
        options={Object.entries(OPERATION_LABELS).map(([value, label]) => ({value, label}))}/>
      <Select allowClear placeholder="状态筛选" style={{width: 210}} value={query.state} onChange={state => setQuery({...query, state, page: 1})}
        options={states.map(value => ({value, label: STATE_LABELS[value]}))}/>
      <Button onClick={() => setRefresh(value => value + 1)}>刷新记录</Button>
    </Space>
    <Table resizeKey="pages/exec/panel/Records:1" rowKey="id" dataSource={data.items} loading={loading} scroll={{x: 850}} columns={[
      {title: '任务', dataIndex: 'id', render: id => <Button type="link" onClick={() => onOpen(id)}>#{id}</Button>},
      {title: '操作', dataIndex: 'operation', render: operation => OPERATION_LABELS[operation] || operation},
      {title: '状态', dataIndex: 'state', render: state => <Status state={state}/>},
      {title: '来源任务', dataIndex: 'retry_parent_id', render: id => id ? `#${id}` : '—'},
      {title: '创建时间', dataIndex: 'created_at'},
      {title: '更新时间', dataIndex: 'updated_at'},
    ]} pagination={{current: query.page, pageSize: query.page_size, total: data.total, showSizeChanger: true,
      onChange: (page, page_size) => setQuery({...query, page, page_size}), showTotal: total => `共 ${total} 个任务`}}/>
  </div>;
}
