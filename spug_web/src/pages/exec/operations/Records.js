import React, { useEffect, useState } from 'react';
import { Alert, Button, Select, Space } from 'antd';
import Table from 'components/ResizableTable';
import { http } from 'libs';
import { RUN_LABELS } from './helpers';

export default function Records({jobs, revision, onOpen}) {
  const [query, setQuery] = useState({page: 1, page_size: 20});
  const [data, setData] = useState({items: [], total: 0});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let valid = true; setLoading(true);
    http.get('/api/ops/runs/', {params: query}).then(result => {if (valid) {setData(result); setError(false);}}, () => {if (valid) setError(true);})
      .finally(() => {if (valid) setLoading(false);});
    return () => {valid = false;};
  }, [query, revision, refresh]);
  return <div>
    <Space wrap style={{marginBottom: 16}}>
      <Select allowClear placeholder="作业筛选" style={{width: 210}} value={query.job} onChange={job => setQuery({...query, job, page: 1})}
        options={(jobs || []).map(job => ({value: job.code, label: job.name}))}/>
      <Select allowClear placeholder="运行状态" style={{width: 210}} value={query.state} onChange={state => setQuery({...query, state, page: 1})}
        options={Object.entries(RUN_LABELS).map(([value, label]) => ({value, label}))}/>
      <Button loading={loading} onClick={() => setRefresh(value => value + 1)}>刷新记录</Button>
    </Space>
    {error && <Alert type="error" showIcon message="记录读取失败，当前列表可能已过期，请刷新重试。"/>}
    <Table resizeKey="operations-records" rowKey="id" loading={loading} dataSource={data.items} scroll={{x: 900}} columns={[
      {title: '作业编号', dataIndex: 'id', width: 100, render: id => <Button type="link" onClick={() => onOpen(id)}>#{id}</Button>},
      {title: '标准作业', dataIndex: 'job_name', width: 200, render: (value, row) => `${value || (jobs || []).find(item => item.code === row.job)?.name || row.job} · v${row.version}`},
      {title: '状态', dataIndex: 'state', width: 180, render: state => RUN_LABELS[state] || '未知'},
      {title: '主机汇总', dataIndex: 'counts', width: 260, render: counts => counts ? `成功 ${counts.success || 0} / 失败 ${counts.failed || 0} / 待核实 ${counts.unknown || 0} / 共 ${counts.total || 0}` : '待核实'},
      {title: '创建时间', dataIndex: 'created_at', width: 180},
    ]} pagination={{current: query.page, pageSize: query.page_size, total: data.total, showSizeChanger: true,
      onChange: (page, page_size) => setQuery({...query, page, page_size}), showTotal: total => `共 ${total} 个作业`}}/>
  </div>;
}
