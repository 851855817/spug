import React, { useState } from 'react';
import { Button, Card, Empty, Form, Input, Modal, Space, Tree, TreeSelect, message } from 'antd';
import { DeleteOutlined, EditOutlined, FolderOutlined, PlusOutlined } from '@ant-design/icons';
import { hasPermission, http } from 'libs';
import { buildGroupTree, descendantIds } from './helpers';
import {useResourceAccess} from 'resources/ResourceAccess';

export default function Group({groups, selected, onSelect, onRefresh}) {
  const access = useResourceAccess();
  const canGroup = group => access.is_supper || group?.can_manage === true;
  const scopedTree = nodes => nodes.map(node => ({...node, disabled: !canGroup(groups.find(group => group.id === node.key)), children: scopedTree(node.children || [])}));
  const [record, setRecord] = useState(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();
  const canManage = hasPermission('baota.group.manage');
  const current = groups.find(group => group.id === selected);

  function openForm(value) {
    form.resetFields();
    form.setFieldsValue(value);
    setRecord(value);
  }

  async function save() {
    let values;
    try { values = await form.validateFields(); } catch (_) { return; }
    setSaving(true);
    try {
      await http.post('/api/baota/group/', {...values, parent_id: values.parent_id || null, id: record.id});
      message.success('分组已保存');
      setRecord(null);
      onRefresh();
    } catch (_) {
      // Errors are displayed by the shared HTTP client.
    } finally { setSaving(false); }
  }

  function remove() {
    Modal.confirm({
      title: `删除分组“${current.name}”？`,
      content: '只有不包含面板和子分组的分组才可以删除。',
      okText: '删除', okButtonProps: {danger: true}, cancelText: '取消',
      onOk: () => http.delete('/api/baota/group/', {params: {id: current.id}}).then(() => {
        message.success('分组已删除');
        onSelect(0);
        onRefresh();
      }),
    });
  }

  const excluded = record?.id ? descendantIds(groups, record.id) : new Set();
  return (
    <Card title="分组列表" size="small" extra={canManage && (
      <Button disabled={!!current && !canGroup(current)} type="text" icon={<PlusOutlined/>} title="新建分组" onClick={() => openForm({parent_id: selected || null})}/>
    )}>
      <Tree.DirectoryTree blockNode showIcon={false} expandAction="doubleClick" selectedKeys={[selected]}
                          treeData={[{key: 0, title: '全部面板', children: buildGroupTree(groups)}]}
                          defaultExpandedKeys={[0]} onSelect={keys => onSelect(keys[0] || 0)}/>
      {!groups.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有分组"/>}
      {canManage && current && <Space style={{marginTop: 20}}>
        <Button disabled={!canGroup(current)} size="small" icon={<EditOutlined/>} onClick={() => openForm(current)}>编辑</Button>
        <Button disabled={!canGroup(current)} size="small" danger icon={<DeleteOutlined/>} onClick={remove}>删除</Button>
      </Space>}
      {record && <Modal open title={record.id ? '编辑分组' : '新建分组'} onCancel={() => setRecord(null)}
                        onOk={save} okText="保存" confirmLoading={saving} maskClosable={false}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="分组名称" rules={[{required: true, whitespace: true, message: '请输入分组名称'}]}>
            <Input maxLength={50} autoFocus placeholder="例如：生产环境" prefix={<FolderOutlined/>}/>
          </Form.Item>
          <Form.Item name="parent_id" label="上级分组">
            <TreeSelect allowClear treeDefaultExpandAll placeholder="不选择则创建根分组"
                        treeData={scopedTree(buildGroupTree(groups.filter(group => !excluded.has(group.id))))}/>
          </Form.Item>
        </Form>
      </Modal>}
    </Card>
  );
}
