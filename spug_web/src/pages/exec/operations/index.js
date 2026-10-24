import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Empty, Space, Spin, Tabs, Typography } from 'antd';
import { Breadcrumb } from 'components';
import { hasPermission, history, http } from 'libs';
import RunForm from './RunForm';
import RunDetail from './RunDetail';
import Records from './Records';
import { parseRunId } from './helpers';

export default function Operations() {
  const allowed = hasPermission('admin');
  const [catalog, setCatalog] = useState(null);
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [runId, setRunId] = useState(() => {
    const ids = new URLSearchParams(history.location.search).getAll('run_id');
    return ids.length === 1 ? parseRunId(ids[0]) : null;
  });
  const [activeTab, setActiveTab] = useState(runId ? 'records' : 'create');
  const [revision, setRevision] = useState(0);
  const resultHeading = useRef();
  useEffect(() => {
    if (!allowed) return;
    let valid = true; setError(false);
    http.get('/api/ops/catalog/').then(result => {if (valid) setCatalog(result);}, () => {if (valid) setError(true);});
    return () => {valid = false;};
  }, [allowed, refresh]);
  useEffect(() => {
    if (!allowed || !runId || !resultHeading.current) return;
    resultHeading.current.focus({preventScroll: true});
    if (resultHeading.current.scrollIntoView) resultHeading.current.scrollIntoView({block: 'start'});
  }, [allowed, runId, revision]);
  function open(value) {
    const id = parseRunId(value);
    if (!id) return;
    const query = new URLSearchParams(history.location.search);
    query.set('run_id', String(id));
    history.replace({...history.location, search: `?${query.toString()}`});
    setActiveTab('records'); setRunId(id); setRevision(current => current + 1);
  }
  if (!allowed) return <Alert showIcon type="warning" message="仅超级管理员可访问运维作业。"/>;
  return <div>
    <Breadcrumb><Breadcrumb.Item>首页</Breadcrumb.Item><Breadcrumb.Item>批量执行</Breadcrumb.Item><Breadcrumb.Item>运维作业</Breadcrumb.Item></Breadcrumb>
    <Card title="运维作业">
      <Tabs activeKey={activeTab} onChange={setActiveTab}>
        <Tabs.TabPane key="create" tab="新建作业">
          {error ? <Space><Alert type="error" message="作业目录读取失败，请重试。"/><Button onClick={() => setRefresh(value => value + 1)}>重新读取</Button></Space> :
            catalog ? <RunForm catalog={catalog} onCreated={open}/> : <Spin/>}
        </Tabs.TabPane>
        <Tabs.TabPane key="records" tab="执行记录"><Records jobs={catalog?.jobs} revision={revision} onOpen={open}/></Tabs.TabPane>
        <Tabs.TabPane key="catalog" tab="标准作业库（只读）">
          {(catalog?.jobs || []).map(job => <Card key={job.code} size="small" title={`${job.name} · v${job.version}`} style={{marginBottom: 12}}>
            <Typography.Paragraph>{job.description}</Typography.Paragraph>
            <Typography.Paragraph><strong>依赖：</strong>{(job.dependencies || []).map(value => value.replace(/[。；;]+$/, '')).join('；') || '以主机探测结果为准'}</Typography.Paragraph>
            <Typography.Paragraph><strong>范围与限制：</strong>{(job.limitations || []).map(value => value.replace(/[。；;]+$/, '')).join('；') || '仅固定检查项'}</Typography.Paragraph>
          </Card>)}
        </Tabs.TabPane>
      </Tabs>
    </Card>
    <div style={{marginTop: 16}}>{runId ? <section aria-labelledby="operations-results-title">
      <h2 id="operations-results-title" ref={resultHeading} tabIndex={-1} style={{scrollMarginTop: 16}}>巡检结果 · 作业 #{runId}</h2>
      <RunDetail key={`${runId}:${revision}`} runId={runId}/>
    </section> : <Card><Empty description="创建作业，或从执行记录点击“查看结果”打开逐台结果。"/></Card>}</div>
  </div>;
}
