import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, Select, Typography } from 'antd';
import { hasPermission, http } from 'libs';
import { groupName, hostName, previewHosts } from './helpers';

export default function RunForm({catalog, onCreated}) {
  const jobs = catalog.jobs || [];
  const [job, setJob] = useState(jobs[0]?.code);
  const [hostIds, setHostIds] = useState([]);
  const [groupIds, setGroupIds] = useState([]);
  const [phpVersion, setPhpVersion] = useState('74');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mounted = useRef(true);
  const pending = useRef(false);
  useEffect(() => () => {mounted.current = false;}, []);
  const selectedJob = jobs.find(item => item.code === job);
  const targets = previewHosts(catalog, hostIds, groupIds);
  const valid = selectedJob && targets.length > 0 && targets.length <= 50;

  async function confirm() {
    if (!mounted.current || !valid || pending.current || !hasPermission('admin')) return;
    const payload = {job, host_ids: [...hostIds], group_ids: [...groupIds], params: job === 'php_environment' ? {php_version: phpVersion} : {},
      execution_mode: 'continuous', confirmed: true};
    pending.current = true; setBusy(true); setError('');
    try {
      const result = await http.post('/api/ops/runs/', payload);
      if (mounted.current && onCreated) onCreated(result.id);
    } catch (_) {
      if (mounted.current) setError('创建结果未确认，请先刷新执行记录核实，再决定是否重新创建。');
    } finally {pending.current = false; if (mounted.current) setBusy(false);}
  }
  return <Form layout="vertical">
    <Alert showIcon type="info" message="逐台巡检 · 遇错继续" description="点击确认执行后，通过 SSH 自动检查所有所选主机；单台报错显示原因并继续下一台。最多 50 台，可取消未执行项。"/>
    <Form.Item label="标准作业"><Select placeholder="标准作业" style={{width: '100%'}} value={job} disabled={busy} onChange={setJob}
      options={jobs.map(item => ({value: item.code, label: `${item.name} · v${item.version}`}))}/></Form.Item>
    {selectedJob && <Typography.Paragraph type="secondary">{selectedJob.description}</Typography.Paragraph>}
    <Form.Item label="选择主机"><Select placeholder="选择主机" mode="multiple" showSearch optionFilterProp="label" style={{width: '100%'}} disabled={busy}
      value={hostIds} onChange={setHostIds} options={(catalog.hosts || []).map(host => ({value: host.id, label: hostName(host)}))}/></Form.Item>
    <Form.Item label="选择分组（包含子组）"><Select placeholder="选择分组" mode="multiple" showSearch optionFilterProp="label" style={{width: '100%'}} disabled={busy}
      value={groupIds} onChange={setGroupIds} options={(catalog.groups || []).map(group => ({value: group.id, label: groupName(group, catalog.groups)}))}/></Form.Item>
    <Typography.Paragraph>预估 {targets.length} 台 · 以服务器冻结结果为准</Typography.Paragraph>
    {targets.length > 50 && <Alert type="error" message="最多选择 50 台主机，请缩小范围。"/>}
    {job === 'php_environment' && <Form.Item label="宝塔 PHP 分支"><Select placeholder="PHP分支" value={phpVersion} disabled={busy} onChange={setPhpVersion} style={{width: 200}}
      options={['74', '80', '81', '82', '83', '84', '85'].map(version => ({value: version, label: `PHP ${version[0]}.${version.slice(1)}`}))}/></Form.Item>}
    {error && <Alert showIcon type="warning" message={error}/>}
    <Button type="primary" disabled={!valid || busy} loading={busy} onClick={confirm}>确认执行</Button>
  </Form>;
}
