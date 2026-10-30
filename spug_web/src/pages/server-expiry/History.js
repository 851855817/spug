import React, { useEffect, useState } from 'react';
import { Alert, Button, Modal } from 'antd';
import ResizableTable from '../../components/ResizableTable';
import { http } from 'libs';
import { API, safeError, time } from './common';

const LABELS = {manual_expiry: '人工到期日', expires_on: '生效到期日', owner_id: '负责人编号', followup: '跟进状态',
  reminder_enabled: '站内提醒', exempt: '无需管理', note: '备注'};
const value = input => input == null || input === '' ? '未设置' : typeof input === 'boolean' ? (input ? '开启' : '关闭') :
  ({pending: '待跟进', contacted: '已联系'}[input] || String(input));
function Changes({detail = {}}) {
  const before = detail.before || {}, after = detail.after || {};
  const keys = Object.keys(LABELS).filter(key => Object.prototype.hasOwnProperty.call(after, key) && before[key] !== after[key]);
  return keys.length ? keys.map(key => <div key={key}>{LABELS[key]}：{value(before[key])} → {value(after[key])}</div>) : '到期信息已登记';
}
export default function History({asset, onClose}) {
  const [page, setPage] = useState({page: 1, page_size: 20}), [data, setData] = useState(null);
  const [error, setError] = useState(''), [revision, setRevision] = useState(0);
  useEffect(() => {
    let valid = true; setData(null); setError('');
    http.get(`${API}assets/${asset.key}/history/`, {params: page}).then(result => {if (valid) setData(result);},
      failure => {if (valid) setError(safeError(failure, '读取记录失败，请重试。'));});
    return () => {valid = false;};
  }, [asset.key, page, revision]);
  return <Modal open title={`到期与续费记录 · ${asset.name}`} onCancel={onClose} width={850} footer={<Button onClick={onClose}>关闭</Button>}>
    {error && <Alert type="error" message={error} action={<Button onClick={() => setRevision(current => current + 1)}>重试</Button>}/>}
    <ResizableTable resizeKey="expiry-history" rowKey="id" size="small" dataSource={data?.items || []} loading={!data && !error}
      locale={{emptyText: '暂无变更记录'}} scroll={{x: 720}} columns={[
        {title: '时间', dataIndex: 'created_at', width: 180, render: time},
        {title: '操作人', dataIndex: 'actor_name', width: 120},
        {title: '操作', dataIndex: 'event', width: 110, render: event => event === 'renew' ? '登记续费' : '更新信息'},
        {title: '变更内容', dataIndex: 'detail', width: 310, render: detail => <Changes detail={detail}/>},
      ]} pagination={{current: page.page, pageSize: page.page_size, total: data?.total || 0, showSizeChanger: false,
        onChange: next => setPage(current => ({...current, page: next}))}}/>
  </Modal>;
}
