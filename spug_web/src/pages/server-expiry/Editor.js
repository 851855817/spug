import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, Input, Modal, Select, Space, Switch, Typography, message } from 'antd';
import { hasPermission, http } from 'libs';
import { API, ownerName, platformToday, safeError, validDate } from './common';

export default function Editor({asset, mode, options, onClose, onSaved}) {
  const renew = mode === 'renew';
  const [dateMode, setDateMode] = useState(asset.date_source === 'manual' || asset.manual_expiry ? 'manual' : 'original');
  const [expiry, setExpiry] = useState(renew ? '' : asset.manual_expiry || asset.expires_on || '');
  const [owner, setOwner] = useState(asset.owner?.id ?? '');
  const [followup, setFollowup] = useState(asset.followup);
  const [reminder, setReminder] = useState(asset.reminder_enabled);
  const [exempt, setExempt] = useState(asset.exempt);
  const [note, setNote] = useState(renew ? '' : asset.note);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const pending = useRef(false), mounted = useRef(true), confirmation = useRef(null);
  useEffect(() => () => {mounted.current = false; if (confirmation.current) confirmation.current.destroy();}, []);

  async function submit(payload) {
    if (pending.current || !mounted.current || !hasPermission('admin')) return;
    pending.current = true; setSaving(true); setError('');
    try {
      await http.post(`${API}assets/${asset.key}/${renew ? 'renew/' : ''}`, payload);
      if (mounted.current) {message.success(renew ? '已登记新的到期日' : '到期信息已保存'); onSaved();}
    } catch (failure) {
      if (mounted.current) setError(safeError(failure, '保存失败，请重试；如信息已变化，请关闭后刷新列表。'));
    } finally {
      pending.current = false; if (mounted.current) setSaving(false);
    }
  }
  function save() {
    if (pending.current || confirmation.current || !hasPermission('admin')) return;
    setError('');
    if ((renew || dateMode === 'manual') && !validDate(expiry)) {setError('请填写有效的到期日期。'); return;}
    if (renew && (expiry <= (options.today || platformToday(options.timezone)) ||
      (validDate(asset.expires_on) && expiry <= asset.expires_on))) {
      setError('续费后到期日必须晚于今天及当前有效到期日。'); return;
    }
    const payload = renew ? {version: asset.version, expires_on: expiry, note: note || ''} :
      {version: asset.version, manual_expiry: dateMode === 'manual' ? expiry : null,
        owner_id: owner === '' || owner == null ? null : Number(owner), followup,
        reminder_enabled: Boolean(reminder), exempt: Boolean(exempt), note: note || ''};
    if (!renew && reminder && !asset.reminder_enabled) {
      confirmation.current = Modal.confirm({title: '开启管理员站内提醒？',
        content: `每天 09:00（${options.timezone || 'Asia/Shanghai'}）检查本地记录，仅在到期管理页生成提醒；不会发送邮件、短信或机器人消息。`,
        okText: '开启并保存', cancelText: '取消',
        onCancel: () => {confirmation.current = null;},
        onOk: () => {confirmation.current = null; return submit(payload);}});
    } else submit(payload);
  }
  const owners = options.owners.map(item => ({value: item.id, label: item.name}));
  if (asset.owner?.available === false && !owners.some(item => item.value === asset.owner.id)) {
    owners.push({value: asset.owner.id, label: ownerName(asset.owner), disabled: true});
  }
  return <Modal open title={`${renew ? '登记续费' : '编辑到期信息'} · ${asset.name}`} onCancel={() => {if (!pending.current) onClose();}}
    maskClosable={false} width={560} footer={<Space><Button disabled={saving} onClick={onClose}>取消</Button>
      <Button type="primary" loading={saving} onClick={save}>{renew ? '确认登记' : '保存'}</Button></Space>}>
    {error && <Alert type="error" showIcon message={error} style={{marginBottom: 16}}/>}
    {renew ? <>
      <Alert type="info" showIcon message="仅登记您已核对的新到期日，不代表云厂商已完成续费。" style={{marginBottom: 16}}/>
      <Typography.Paragraph>当前到期日：{asset.expires_on || '未登记'}</Typography.Paragraph>
      <Form.Item label="续费后到期日"><Input type="date" aria-label="续费后到期日" value={expiry}
        onChange={event => setExpiry(event.target.value)} disabled={saving}/></Form.Item>
    </> : <>
      <Form.Item label="日期来源"><Select aria-label="日期来源" style={{width: '100%'}} value={dateMode} disabled={saving}
        onChange={setDateMode} options={[{value: 'original', label: '使用主机已有记录（独立宝塔为未登记）'}, {value: 'manual', label: '人工登记，不被云同步覆盖'}]}/></Form.Item>
      <Typography.Paragraph type="secondary">主机已有记录：{asset.original_expiry || '未登记'}。关联宝塔共享本主机的到期信息。</Typography.Paragraph>
      {dateMode === 'manual' && <Form.Item label="人工到期日"><Input type="date" aria-label="人工到期日" value={expiry}
        onChange={event => setExpiry(event.target.value)} disabled={saving}/></Form.Item>}
      <Form.Item label="负责人"><Select aria-label="负责人" allowClear style={{width: '100%'}} value={owner} disabled={saving}
        onChange={setOwner} options={owners} placeholder="未指派"/></Form.Item>
      <Form.Item label="跟进状态"><Select aria-label="跟进状态" style={{width: '100%'}} value={followup} disabled={saving}
        onChange={setFollowup} options={[{value: 'pending', label: '待跟进'}, {value: 'contacted', label: '已联系'}]}/></Form.Item>
      <Form.Item label="无需到期管理"><Switch aria-label="无需到期管理" checked={exempt} onChange={setExempt} disabled={saving}/></Form.Item>
      <Form.Item label="开启站内提醒"><Switch aria-label="开启站内提醒" checked={reminder} onChange={setReminder} disabled={saving || exempt}/>
        <div style={{color: '#888', marginTop: 6}}>默认关闭，开启后每日 09:00 检查；仅管理员可见。</div></Form.Item>
    </>}
    <Form.Item label={renew ? '续费备注' : '备注'}><Input.TextArea aria-label={renew ? '续费备注' : '备注'} value={note}
      onChange={event => setNote(event.target.value)} maxLength={500} rows={3} disabled={saving}/></Form.Item>
  </Modal>;
}
