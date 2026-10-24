import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Space, Spin, Tabs, Typography } from 'antd';
import { Breadcrumb } from 'components';
import { hasPermission, http } from 'libs';
import RunForm from './RunForm';
import RunDetail from './RunDetail';
import Records from './Records';

export default function Operations() {
  const allowed = hasPermission('admin');
  const [catalog, setCatalog] = useState(null);
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [runId, setRunId] = useState(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!allowed) return;
    let valid = true; setError(false);
    http.get('/api/ops/catalog/').then(result => {if (valid) setCatalog(result);}, () => {if (valid) setError(true);});
    return () => {valid = false;};
  }, [allowed, refresh]);
  function open(id) {setRunId(id); setRevision(value => value + 1);}
  if (!allowed) return <Alert showIcon type="warning" message="仅超级管理员可访问运维作业。"/>;
  return <div>
    <Breadcrumb><Breadcrumb.Item>首页</Breadcrumb.Item><Breadcrumb.Item>批量执行</Breadcrumb.Item><Breadcrumb.Item>运维作业</Breadcrumb.Item></Breadcrumb>
    <Card title="运维作业">
      <Tabs defaultActiveKey="create">
        <Tabs.TabPane key="create" tab="新建作业">
          {error ? <Space><Alert type="error" message="作业目录读取失败，请重试。"/><Button onClick={() => setRefresh(value => value + 1)}>重新读取</Button></Space> :
            catalog ? <RunForm catalog={catalog} onCreated={open}/> : <Spin/>}
        </Tabs.TabPane>
        <Tabs.TabPane key="records" tab="执行记录"><Records jobs={catalog?.jobs} revision={revision} onOpen={open}/></Tabs.TabPane>
        <Tabs.TabPane key="catalog" tab="标准作业库（只读）">
          {(catalog?.jobs || []).map(job => <Card key={job.code} size="small" title={`${job.name} · v${job.version}`} style={{marginBottom: 12}}>
            <Typography.Paragraph>{job.description}</Typography.Paragraph>
            <Typography.Paragraph><strong>依赖：</strong>{(job.dependencies || []).join('；') || '以主机探测结果为准'}</Typography.Paragraph>
            <Typography.Paragraph><strong>范围与限制：</strong>{(job.limitations || []).join('；') || '仅固定检查项'}</Typography.Paragraph>
          </Card>)}
        </Tabs.TabPane>
      </Tabs>
    </Card>
    <div style={{marginTop: 16}}>{runId ? <RunDetail key={`${runId}:${revision}`} runId={runId}/> : <Card><Empty description="创建作业，或从执行记录打开逐台结果。"/></Card>}</div>
  </div>;
}
