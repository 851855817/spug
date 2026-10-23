import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Modal, Select, message } from 'antd';
import { http } from 'libs';

function addressHost(address) {
  try {return new URL(address.includes('://') ? address : `ssh://${address}`).hostname;} catch (_) {return address;}
}

export default function LinkSettings({card, onCancel, onSaved, hideIP}) {
  const [hosts, setHosts] = useState([]);
  const [selected, setSelected] = useState(card.linked_host_id ? String(card.linked_host_id) : '');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [loadFailed, setLoadFailed] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    http.get('/api/baota/monitor/sources/', {params: {kind: 'panel'}}).then(result => {
      if (alive.current) setHosts(result.hosts);
    }).catch(() => {if (alive.current) {setError('可关联主机加载失败，请关闭后重试'); setLoadFailed(true);}})
      .finally(() => {if (alive.current) setLoading(false);});
    return () => {alive.current = false;};
  }, []);

  const current = card.linked_host_id ? String(card.linked_host_id) : '';
  function confirm() {
    const host = hosts.find(item => String(item.id) === selected);
    Modal.confirm({
      title: selected ? '确认关联同一台服务器？' : '确认取消关联？',
      content: selected ? `将“${card.name}”关联到“${host?.name}”。请确认它们属于同一台服务器。` : '取消后，已开启的来源会分别展示为卡片。',
      okText: '确认保存', cancelText: '返回',
      onOk: async () => {
        if (!alive.current) return;
        setSaving(true);
        setError('');
        try {
          await http.post('/api/baota/monitor/link/', {panel_id: card.panel_id, host_id: selected ? Number(selected) : null});
          if (alive.current) {message.success('关联已保存'); onSaved();}
        } catch (_) {
          if (alive.current) setError('关联保存失败，请重试');
        } finally {if (alive.current) setSaving(false);}
      },
    });
  }
  const matching = hosts.filter(host => addressHost(host.address) === addressHost(card.address));
  return <Modal visible title={`关联设置 · ${card.name}`} onCancel={onCancel} footer={null} destroyOnClose>
    <p>选择有权限的主机合并显示，只有开启的来源会参与采集。相同地址仅作建议。</p>
    {!!matching.length && <Alert type="info" showIcon message={`同地址主机建议：${matching.map(host => host.name).join('、')}，请核实后选择。`}/>}
    {error && <Alert type="error" message={error}/>}
    <div style={{margin: '16px 0'}}><Select aria-label="关联主机" style={{width: '100%'}} value={selected} loading={loading}
      disabled={loading || saving || loadFailed} onChange={setSelected} options={[
        {value: '', label: '不关联 / 取消关联'},
        ...hosts.map(host => ({value: String(host.id), label: `${host.name}${hideIP ? '' : ` (${host.address})`}`})),
      ]}/></div>
    <Button type="primary" onClick={confirm} loading={saving} disabled={loading || loadFailed || current === selected}>保存关联</Button>
  </Modal>;
}
