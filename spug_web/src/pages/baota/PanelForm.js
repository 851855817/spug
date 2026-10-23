import React, { useState } from 'react';
import { Alert, Button, Checkbox, Divider, Form, Input, Modal, Radio, Space, TreeSelect, Typography, message } from 'antd';
import { http } from 'libs';
import { buildGroupTree, inferAPIBaseURL } from './helpers';

export default function PanelForm({record, groups, selectedGroup, onCancel, onSaved, onChecked}) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [inspecting, setInspecting] = useState(false);
  const [connection, setConnection] = useState(record);
  const [checkFailed, setCheckFailed] = useState(false);
  const [manualAPIAddress, setManualAPIAddress] = useState(Boolean(record.api_base_url));
  const [trust, setTrust] = useState(record.has_tls_ca ? 'ca' : record.tls_fingerprint ? 'fingerprint' : 'system');
  const apiAddress = Form.useWatch('api_base_url', form) || '';
  const clearingKey = Form.useWatch('clear_api_key', form);

  async function handleSubmit() {
    let values;
    try { values = await form.validateFields(); } catch (_) { return; }
    setSaving(true);
    try {
      const payload = {...values, id: record.id};
      if (!record.api_base_url && !record.has_api_key && !values.api_key && !manualAPIAddress) {
        payload.api_base_url = '';
      }
      if (!/^https:/i.test(values.api_base_url || '') || trust === 'system') {
        payload.tls_ca = '';
        payload.tls_fingerprint = '';
      } else if (trust === 'fingerprint') {
        payload.tls_ca = '';
      } else {
        payload.tls_fingerprint = '';
        if (!payload.tls_ca && record.has_tls_ca) delete payload.tls_ca;
      }
      await http.post('/api/baota/', payload);
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

  function validateAPIURL(_, value) {
    if (value && /[?#]/.test(value)) return Promise.reject('API 地址不能包含查询参数或片段');
    return validateURL(_, value);
  }

  function handleValuesChange(changed, values) {
    if ('api_base_url' in changed) setManualAPIAddress(true);
    else if ('url' in changed && !manualAPIAddress) {
      form.setFieldsValue({api_base_url: inferAPIBaseURL(values.url)});
    }
  }

  async function checkConnection() {
    setChecking(true);
    try {
      const result = await http.post('/api/baota/connection/', {id: record.id});
      setConnection(result);
      setCheckFailed(false);
      message.success(`连接成功，宝塔版本 ${result.api_version}`);
      if (onChecked) onChecked();
    } catch (_) {
      setCheckFailed(true);
    } finally { setChecking(false); }
  }

  async function inspectCertificate() {
    if ((form.getFieldValue('api_base_url') || '').replace(/\/$/, '') !== record.api_base_url) {
      message.info('请先保存 API 地址，再检查该地址的证书');
      return;
    }
    setInspecting(true);
    try {
      const result = await http.post('/api/baota/certificate/', {id: record.id});
      Modal.confirm({
        title: '确认此面板的 HTTPS 证书', width: 620, okText: '使用此指纹', cancelText: '取消',
        content: <div>
          <Typography.Paragraph>请通过可信渠道核对证书。选择后还需点击“保存”才能启用固定指纹信任。</Typography.Paragraph>
          <Typography.Paragraph>地址：{result.api_base_url}</Typography.Paragraph>
          <Typography.Paragraph>主体：{result.subject}</Typography.Paragraph>
          <Typography.Paragraph>到期时间：{result.expires_at}</Typography.Paragraph>
          <Typography.Paragraph style={{wordBreak: 'break-all'}}>SHA-256：{result.fingerprint}</Typography.Paragraph>
        </div>,
        onOk: () => {
          if ((form.getFieldValue('api_base_url') || '').replace(/\/$/, '') !== result.api_base_url) {
            message.info('API 地址已修改，请先保存并重新检查证书');
            return;
          }
          setTrust('fingerprint');
          form.setFieldsValue({tls_fingerprint: result.fingerprint, tls_ca: ''});
        },
      });
    } catch (_) {
      // The shared HTTP client displays server errors.
    } finally { setInspecting(false); }
  }

  return (
    <Modal open title={record.id ? '编辑宝塔面板' : '添加宝塔面板'} width={680} maskClosable={false}
           bodyStyle={{maxHeight: '65vh', overflowY: 'auto'}}
           confirmLoading={saving} onCancel={onCancel} onOk={handleSubmit} okText="保存" destroyOnClose>
      <Form form={form} layout="vertical" onValuesChange={handleValuesChange}
            initialValues={{group_id: selectedGroup || undefined, ...record,
              api_base_url: record.api_base_url || inferAPIBaseURL(record.url), api_key: '', clear_api_key: false}}>
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
        <Divider orientation="left">API 连接（选填）</Divider>
        <Typography.Paragraph type="secondary">
          API 密钥与登录密码不同。请先在宝塔设置中开启 API，并将 Spug 实际出口 IP 加入 API 白名单。
        </Typography.Paragraph>
        <Form.Item name="api_base_url" label="API 基础地址" extra="默认从面板地址自动提取，去掉登录安全入口；已有或手动填写的地址保留，反向代理可包含路径前缀。"
                   rules={[{validator: validateAPIURL}]}>
          <Input maxLength={2048} placeholder="自动读取上方地址，特殊情况可手动修改" autoComplete="off"/>
        </Form.Item>
        {/^http:/i.test(apiAddress) && <Alert type="warning" showIcon style={{marginBottom: 16}}
          message="此 API 地址使用明文 HTTP，密钥签名和请求内容将通过未加密连接发送。"/>}
        <Form.Item name="api_key" label="API 密钥"
                   extra={record.has_api_key ? '已加密保存。留空保留原密钥，输入新密钥则替换。' : '密钥加密保存，保存后无法查看。'}>
          <Input.Password maxLength={4096} autoComplete="new-password" visibilityToggle={false} disabled={clearingKey}
                          placeholder={record.has_api_key ? '已设置，留空不修改' : '请输入宝塔 API 密钥'}/>
        </Form.Item>
        {record.has_api_key && <Form.Item name="clear_api_key" valuePropName="checked">
          <Checkbox onChange={event => { if (event.target.checked) form.setFieldsValue({api_key: ''}); }}>清除已保存的 API 密钥</Checkbox>
        </Form.Item>}
        {/^https:/i.test(apiAddress) && <>
          <Form.Item label="HTTPS 证书信任">
            <Radio.Group value={trust} onChange={event => setTrust(event.target.value)}>
              <Radio value="system">系统信任</Radio><Radio value="ca">导入 CA</Radio><Radio value="fingerprint">固定 SHA-256 指纹</Radio>
            </Radio.Group>
          </Form.Item>
          {trust === 'ca' && <Form.Item name="tls_ca" label="公开 CA 证书（PEM）"
            extra={record.has_tls_ca ? '已保存 CA，留空保留；输入公开 CA 证书可替换。不要填写私钥。' : '粘贴可信 CA 的公开证书，不要填写私钥。'}
            rules={[{required: !record.has_tls_ca, message: '请填写公开 CA 证书'}]}>
            <Input.TextArea rows={4} maxLength={65536} placeholder="-----BEGIN CERTIFICATE-----"/>
          </Form.Item>}
          {trust === 'fingerprint' && <Form.Item name="tls_fingerprint" label="证书 SHA-256 指纹"
            extra="仅信任这个面板的指定证书。证书更换后需重新核对并保存指纹。"
            rules={[{required: true, message: '请填写证书指纹'},
              {pattern: /^(?:[a-fA-F0-9]{64}|(?:[a-fA-F0-9]{2}:){31}[a-fA-F0-9]{2})$/, message: '请输入完整的 SHA-256 指纹'}]}>
            <Input maxLength={95} placeholder="64 位十六进制 SHA-256 指纹"/>
          </Form.Item>}
        </>}
        {record.id ? <>
          <Typography.Paragraph type="secondary">以下检查使用已保存的 API 配置。修改后请先保存，再重新打开检查。</Typography.Paragraph>
          <Space style={{marginBottom: 12}}>
            <Button onClick={checkConnection} loading={checking} disabled={!record.api_base_url || !record.has_api_key}>测试已保存配置</Button>
            <Button onClick={inspectCertificate} loading={inspecting} disabled={!/^https:/i.test(record.api_base_url || '')}>查看证书</Button>
          </Space>
          <Typography.Paragraph type={checkFailed ? 'danger' : 'secondary'}>
            {checkFailed ? '本次连接失败。' : ''}
            {connection.api_checked_at ? `上次连接成功：${connection.api_checked_at}，宝塔 ${connection.api_version}` : '尚未验证 API 连接。'}
          </Typography.Paragraph>
        </> : <Typography.Paragraph type="secondary">保存面板后可测试连接和查看 HTTPS 证书。</Typography.Paragraph>}
      </Form>
    </Modal>
  );
}
