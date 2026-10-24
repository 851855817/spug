import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, InputNumber, Modal, Select, Space, Typography } from 'antd';
import { hasPermission, http } from 'libs';
import { groupName, hostName, previewHosts } from './helpers';

export default function RunForm({catalog, onCreated}) {
  const jobs = catalog.jobs || [];
  const [job, setJob] = useState(jobs[0]?.code);
  const [hostIds, setHostIds] = useState([]);
  const [groupIds, setGroupIds] = useState([]);
  const [phpVersion, setPhpVersion] = useState('74');
  const [batchSize, setBatchSize] = useState(1);
  const [threshold, setThreshold] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mounted = useRef(true);
  const pending = useRef(false);
  const confirming = useRef(false);
  const dialog = useRef();
  useEffect(() => () => {mounted.current = false; if (dialog.current) dialog.current.destroy();}, []);
  const selectedJob = jobs.find(item => item.code === job);
  const targets = previewHosts(catalog, hostIds, groupIds);
  const valid = selectedJob && targets.length > 0 && targets.length <= 50 && Number.isInteger(batchSize) && batchSize >= 1 && batchSize <= 10 &&
    Number.isInteger(threshold) && threshold >= 1 && threshold <= batchSize;

  function confirm() {
    if (!valid || pending.current || confirming.current || !hasPermission('admin')) return;
    confirming.current = true;
    let consumed = false;
    const payload = {job, host_ids: [...hostIds], group_ids: [...groupIds], params: job === 'php_environment' ? {php_version: phpVersion} : {},
      batch_size: batchSize, failure_threshold: threshold, confirmed: true};
    dialog.current = Modal.confirm({title: `确认执行${selectedJob.name}？`,
      content: `将通过 SSH 对预估 ${targets.length} 台主机执行只读探测，探测会占用连接与系统资源。服务器会递归解析分组并去重、冻结最终目标；每批 ${batchSize} 台，失败或待核实达到 ${threshold} 台时熔断，每批完成后需人工继续。确认后创建排队作业。`,
      okText: '确认创建并排队', cancelText: '返回检查',
      onCancel: () => {confirming.current = false;},
      onOk: async () => {
        if (!mounted.current || pending.current || consumed || !hasPermission('admin')) return;
        consumed = true;
        pending.current = true; setBusy(true); setError('');
        try {
          const result = await http.post('/api/ops/runs/', payload);
          if (mounted.current && onCreated) onCreated(result.id);
        } catch (_) {
          if (mounted.current) setError('创建结果未确认，请先刷新执行记录核实，再决定是否重新创建。');
        } finally {pending.current = false; confirming.current = false; if (mounted.current) setBusy(false);}
      }});
  }
  return <Form layout="vertical">
    <Alert showIcon type="info" message="固定作业 · 只读探测" description="每批完成后暂停，核对结果后手动继续。分组包含所有子组，最终目标由服务器去重并冻结，最多 50 台。"/>
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
    <Space wrap align="start">
      <Form.Item label="每批台数（1–10）"><InputNumber aria-label="每批台数" min={1} max={10} precision={0} value={batchSize} disabled={busy}
        onChange={value => {setBatchSize(value); if (threshold > value) setThreshold(value);}}/></Form.Item>
      <Form.Item label="批内失败熔断阈值"><InputNumber aria-label="失败阈值" min={1} max={batchSize || 1} precision={0} value={threshold} disabled={busy} onChange={setThreshold}/></Form.Item>
    </Space>
    {error && <Alert showIcon type="warning" message={error}/>}
    <Button type="primary" disabled={!valid || busy} loading={busy} onClick={confirm}>确认执行</Button>
  </Form>;
}
