import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, Input, Modal, Select, Space, Typography } from 'antd';
import Table from 'components/ResizableTable';
import { hasPermission, http } from 'libs';
import { Status } from './TaskEvidence';
import { formatBytes } from './uploadHelpers';

export default function UploadResults({taskId, targetId, summary, active = false, refreshKey, onResolved}) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState({page: 1, page_size: 20});
  const [data, setData] = useState({items: [], total: 0});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [resolution, setResolution] = useState(null);
  const [outcome, setOutcome] = useState('success');
  const [reason, setReason] = useState('');
  const [evidence, setEvidence] = useState('');
  const [saveError, setSaveError] = useState('');
  const [saving, setSaving] = useState(false);
  const pending = useRef(false);

  useEffect(() => {
    if (!expanded) return undefined;
    let valid = true, timer;
    async function load() {
      setLoading(true);
      try {
        const result = await http.get(`/api/baota/targets/${targetId}/uploads/`, {params: query});
        if (valid) {setData({items: Array.isArray(result?.items) ? result.items : [], total: Math.max(0, Number(result?.total) || 0)}); setError('');}
      } catch (_) {if (valid) setError('文件结果读取失败，请收起后重新展开');}
      finally {
        if (valid) {setLoading(false); if (active) timer = setTimeout(load, 5000);}
      }
    }
    load();
    return () => {valid = false; clearTimeout(timer);};
  }, [expanded, targetId, query, active, refreshKey, revision]);

  function openResolution(file) {
    setResolution(file); setOutcome('success'); setReason(''); setEvidence(''); setSaveError('');
  }

  async function saveResolution() {
    if (pending.current) return;
    if (!reason.trim() || !evidence.trim() || reason.length > 2000 || evidence.length > 2000) {
      setSaveError('必须填写可追溯的理由和证据，每项最多 2000 字'); return;
    }
    pending.current = true; setSaving(true); setSaveError('');
    try {
      const result = await http.post(`/api/baota/tasks/${taskId}/resolve/`, {target_id: targetId,
        child_key: `file:${resolution.source_id}`, outcome, reason: reason.trim(), evidence: evidence.trim()});
      if (onResolved) onResolved(result);
      setResolution(null); setRevision(value => value + 1);
    } catch (_) {setSaveError('人工判定保存失败，请核对后重试');}
    finally {pending.current = false; setSaving(false);}
  }

  return <div style={{marginTop: 16}}>
    {summary && <Space wrap style={{marginBottom: 8}}>
      <Typography.Text>文件 {Number(summary.files) || 0} 个 · {formatBytes(summary.total_bytes)}</Typography.Text>
      {[['success', '成功'], ['failed', '失败'], ['uncertain', '待核实'], ['pending', '待处理'], ['cancelled', '已取消']].map(([key, label]) =>
        <Typography.Text key={key}>{label} {Number(summary[key]) || 0}</Typography.Text>)}
    </Space>}
    <div><Button onClick={() => setExpanded(value => !value)}>{expanded ? '收起文件结果' : '查看文件结果'}</Button></div>
    {expanded && <div style={{marginTop: 12}}>
      {error && <Alert type="error" message={error}/>}
      <Table resizeKey="pages/exec/panel/UploadResults:1" rowKey="id" size="small" loading={loading} dataSource={data.items} scroll={{x: 600}} columns={[
        {title: '相对路径', dataIndex: 'relative_path', render: (path, row) => <span title={`${row.target_path || path} · ${formatBytes(row.size)}`} style={{overflowWrap: 'anywhere'}}>{String(path || '')}</span>},
        {title: '状态', dataIndex: 'state', width: 130, render: state => <Status state={state}/>},
        {title: '结果说明', dataIndex: 'message', render: (message, row) => <div>{typeof message === 'string' ? message : ''}
          {hasPermission('exec.panel.manage') && row.state === 'uncertain' && row.source_id && <div><Button size="small" onClick={() => openResolution(row)}>人工判定此文件</Button></div>}
        </div>},
      ]} pagination={{current: query.page, pageSize: query.page_size, total: data.total, showSizeChanger: true,
        onChange: (page, page_size) => setQuery({page, page_size}), showTotal: total => `共 ${total} 个文件`}}/>
    </div>}
    {resolution && <Modal visible title={`人工判定：${resolution.relative_path}`} onCancel={() => {if (!pending.current) setResolution(null);}}
      onOk={saveResolution} confirmLoading={saving} okText="保存人工判定" destroyOnClose>
      <Alert type="warning" showIcon message="请先独立核验此文件。人工判定会保留原有记录，不会重新上传。"/>
      <Form layout="vertical" style={{marginTop: 16}}>
        <Form.Item label="判定结果" required><Select value={outcome} disabled={saving} onChange={setOutcome}
          options={[{value: 'success', label: '确认成功'}, {value: 'failed', label: '确认失败'}]}/></Form.Item>
        <Form.Item label="判定理由" required><Input.TextArea aria-label="判定理由" rows={3} maxLength={2000} disabled={saving} value={reason} onChange={event => setReason(event.target.value)}/></Form.Item>
        <Form.Item label="核验证据（来源、时间、观察结果）" required><Input.TextArea aria-label="核验证据" rows={4} maxLength={2000} disabled={saving} value={evidence} onChange={event => setEvidence(event.target.value)}/></Form.Item>
      </Form>
      {saveError && <Alert type="error" message={saveError}/>}
    </Modal>}
  </div>;
}
