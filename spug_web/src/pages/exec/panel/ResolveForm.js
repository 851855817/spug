import React, { useState } from 'react';
import { Alert, Form, Input, Modal, Select } from 'antd';
import { http } from 'libs';

export default function ResolveForm({taskId, target, child, onClose, onSaved}) {
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  async function save() {
    try {
      const values = await form.validateFields();
      setBusy(true);
      const result = await http.post(`/api/baota/tasks/${taskId}/resolve/`, {
        target_id: target.id, child_key: child.snapshot.key,
        outcome: values.outcome, reason: values.reason.trim(), evidence: values.evidence.trim(),
      });
      onSaved(result);
      onClose();
    } catch (_) { /* Shared HTTP client and Form display errors. */ }
    finally { setBusy(false); }
  }
  const rules = [{required: true, whitespace: true, message: '必须填写可追溯的理由和证据'}, {max: 2000}];
  return <Modal visible title={`人工判定：${target.panel.name} / ${child.snapshot.label}`} onCancel={onClose}
    onOk={save} confirmLoading={busy} okText="保存人工判定" destroyOnClose>
    <Alert type="warning" showIcon message="人工判定会保留原有下发记录及历史，请先完成独立核验。不会重新执行远端操作。"/>
    <Form form={form} layout="vertical" style={{marginTop: 16}}>
      <Form.Item name="outcome" label="判定结果" rules={[{required: true}]}>
        <Select options={[{value: 'success', label: '确认成功'}, {value: 'failed', label: '确认失败'}]}/>
      </Form.Item>
      <Form.Item name="reason" label="判定理由" rules={rules}><Input.TextArea rows={3} maxLength={2000}/></Form.Item>
      <Form.Item name="evidence" label="核验证据（来源、时间、观察结果）" rules={rules}><Input.TextArea rows={4} maxLength={2000}/></Form.Item>
    </Form>
  </Modal>;
}
