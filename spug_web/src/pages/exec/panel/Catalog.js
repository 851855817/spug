import React, { useEffect, useState } from 'react';
import { Alert, Input, Select, Space, Table, Tag } from 'antd';
import { http } from 'libs';

export default function Catalog() {
  const [query, setQuery] = useState({page: 1, page_size: 20});
  const [data, setData] = useState({items: [], categories: [], total: 0, catalog_total: 374});
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let valid = true;
    setLoading(true);
    http.get('/api/baota/catalog/', {params: query}).then(result => {if (valid) setData(result);}, () => {})
      .finally(() => {if (valid) setLoading(false);});
    return () => {valid = false;};
  }, [query]);
  return <div>
    <Alert type="info" showIcon message={`官方接口目录 · ${data.catalog_total} 条 · 只读参考`} description="目录收录不代表已接入执行。可执行范围由新建任务中的操作及各面板预检确定。"/>
    <Space wrap style={{margin: '16px 0'}}>
      <Input.Search allowClear placeholder="搜索接口、说明、路径、来源" maxLength={100} onSearch={q => setQuery({...query, q, page: 1})} style={{width: 320}}/>
      <Select allowClear placeholder="分类筛选" style={{width: 200}} value={query.category} options={data.categories.map(value => ({value, label: value}))}
        onChange={category => setQuery({...query, category, page: 1})}/>
    </Space>
    <Table size="small" loading={loading} dataSource={data.items} rowKey={row => `${row['请求路径']}:${row.action}:${row['文档版本']}`} scroll={{x: 1400}} columns={[
      {title: '分类', dataIndex: '分类', width: 140}, {title: '接口', dataIndex: 'action', width: 180},
      {title: '说明', dataIndex: '操作名称或说明', width: 320}, {title: '方法', dataIndex: '方法', width: 80},
      {title: '路径', dataIndex: '请求路径', width: 180}, {title: '接入状态', dataIndex: '接入状态', width: 240, render: value => <Tag>{value}</Tag>},
      {title: '文档版本', dataIndex: '文档版本', width: 160},
      {title: '官方来源', dataIndex: '官方来源', width: 100, render: url => /^https:\/\//.test(url) ? <a href={url} target="_blank" rel="noopener noreferrer">查看来源</a> : '—'},
    ]} pagination={{current: query.page, pageSize: query.page_size, total: data.total, showSizeChanger: true,
      onChange: (page, page_size) => setQuery({...query, page, page_size}), showTotal: total => `筛选后 ${total} 条`}}/>
  </div>;
}
