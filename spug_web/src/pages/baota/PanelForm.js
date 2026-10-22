import React, { useState } from 'react';
import { Form, Input, Modal, TreeSelect, message } from 'antd';
import { http } from 'libs';
import { buildGroupTree } from './helpers';

export default function PanelForm({record, groups, selectedGroup, onCancel, onSaved}) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  async function handleSubmit() {
    let values;
    try { values = await form.validateFields(); } catch (_) { return; }
    setSaving(true);
    try {
      await http.post('/api/baota/', {...values, id: record.id});
      message.success('面板已保存');
      onSaved();
    } catch (_) {
      // The shared HTTP client displays server errors.
    } finally { setSaving(false); }
  }

  function validateURL(_, value) {
    if (!value) return Promise.resolve();
    try {
      const url = new URL(value);
      if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password ||
          url.hash || /[\s\\]/.test(value)) throw new Error();
      return Promise.resolve();
    } catch (_) { return Promise.reject('请输入有效的 HTTP/HTTPS 面板地址'); }
  }

  return (
    <Modal open title={record.id ? '编辑宝塔面板' : '添加宝塔面板'} width={680} maskClosable={false}
           confirmLoading={saving} onCancel={onCancel} onOk={handleSubmit} okText="保存" destroyOnClose>
      <Form form={form} layout="vertical" initialValues={{group_id: selectedGroup || undefined, ...record}}>
        <Form.Item name="name" label="面板名称" rules={[{required: true, whitespace: true, message: '请输入面板名称'}]}>
          <Input maxLength={100} placeholder="例如：客户 A · 生产服务器" autoFocus/>
        </Form.Item>
        <Form.Item name="group_id" label="所属分组" rules={[{required: true, message: '请选择分组'}]}>
          <TreeSelect treeData={buildGroupTree(groups)} treeDefaultExpandAll placeholder="请选择分组"/>
        </Form.Item>
        <Form.Item name="url" label="面板地址" extra="填写完整地址，包含端口和安全入口路径。"
                   rules={[{required: true, message: '请输入面板地址'}, {validator: validateURL}]}>
          <Input maxLength={2048} placeholder="https://example.com:8888/security-entry" autoComplete="off"/>
        </Form.Item>
        <Form.Item name="username" label="登录账户" rules={[{required: true, whitespace: true, message: '请输入登录账户'}]}>
          <Input maxLength={100} placeholder="宝塔面板账户" autoComplete="off"/>
        </Form.Item>
        <Form.Item name="password" label="登录密码" extra={record.id ? '留空保留已保存的密码，填写新密码则替换。' : '密码加密保存，不在列表中显示。'}
                   rules={[{required: !record.id, message: '请输入登录密码'}]}>
          <Input.Password maxLength={4096} autoComplete="new-password" placeholder={record.id ? '已设置，留空不修改' : '请输入宝塔面板密码'}/>
        </Form.Item>
        <Form.Item name="desc" label="备注">
          <Input.TextArea maxLength={500} rows={2} showCount placeholder="可填写用途、客户等信息"/>
        </Form.Item>
      </Form>
    </Modal>
  );
}
