import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Modal, Select, Space, Spin } from 'antd';
import { hasPermission, http } from 'libs';
import TaskForm from '../exec/panel/TaskForm';
import PanelPlanSummary from './PanelPlanSummary';

const PENDING = '预检尚未完成，请稍后刷新';
const MAX_POLLS = 240;

export function templateResponse(raw) {
  let result = raw;
  if (typeof raw === 'string') {
    try {result = JSON.parse(raw);} catch (_) {return raw;}
  }
  // The shared HTTP client reports all API errors. Pending preflight is an
  // expected polling state; keep genuine API errors on the normal error path.
  return result?.error === PENDING ? {data: {pending: true}} : result;
}

export default function PanelPlanSelector({value, onChange}) {
  const [items, setItems] = useState([]);
  const [selectedId, setSelectedId] = useState(value?.template_id);
  const [creating, setCreating] = useState(false);
  const [options, setOptions] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [waiting, setWaiting] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const generation = useRef(0);
  const timer = useRef();
  const latestChange = useRef(onChange);
  latestChange.current = onChange;
  const canView = hasPermission('exec.panel.view');
  const canCreate = canView && hasPermission('exec.panel.create');

  useEffect(() => {
    if (!canCreate) return undefined;
    let valid = true;
    http.get('/api/schedule/panel_templates/').then(result => {if (valid) {setItems(result.items || []); setError('');}},
      () => {if (valid) setError('读取宝塔预检模板失败，请重试');});
    return () => {valid = false;};
  }, [canCreate, refresh]);
  useEffect(() => () => {generation.current++; clearTimeout(timer.current);}, []);
  useEffect(() => {
    if (!canCreate) {generation.current++; clearTimeout(timer.current);}
  }, [canCreate]);

  async function loadTemplate(id) {
    if (!canCreate) return;
    const token = ++generation.current;
    clearTimeout(timer.current);
    setSelectedId(id); setCreating(false); setError(''); setBusy(true); setWaiting(false);
    latestChange.current(null);
    let attempts = 0;
    async function poll() {
      attempts++;
      try {
        const result = await http.get('/api/schedule/panel_templates/', {params: {id}, transformResponse: [templateResponse]});
        if (token !== generation.current) return;
        if (result.pending) throw PENDING;
        latestChange.current(result); setBusy(false); setWaiting(false);
        setItems(current => [result, ...current.filter(item => item.template_id !== result.template_id)]);
      } catch (failure) {
        if (token !== generation.current) return;
        const reason = String(failure?.message || failure || '预检模板读取失败');
        if (reason === PENDING && attempts < MAX_POLLS) {
          setWaiting(true); timer.current = setTimeout(poll, 2500);
        } else {
          setBusy(false); setWaiting(false);
          setError(reason === PENDING ? '预检仍未完成，已停止自动刷新。请稍后重新读取预检。' : reason);
        }
      }
    }
    await poll();
  }

  async function openCreate() {
    if (!canCreate) return;
    setError(''); setBusy(true);
    const token = generation.current;
    try {
      const result = await http.get('/api/baota/task-options/');
      if (token === generation.current) {setOptions(result); setCreating(true);}
    } catch (_) {if (token === generation.current) setError('读取宝塔任务选项失败，请重试');}
    finally {if (token === generation.current) setBusy(false);}
  }

  if (!canCreate) return <Alert type="warning" message="需要面板任务查看和创建权限才能配置宝塔计划"/>;
  return <div>
    <Alert showIcon type="info" message="选择已有预检，或生成新的宝塔预检" description="固定所选目标和参数；新增面板/网站不会自动加入。软件/PHP目标版本变化需编辑计划。每次到点重新预检，通过后自动执行。"/>
    <Space wrap style={{margin: '12px 0', width: '100%'}}>
      <Select style={{width: 320, maxWidth: '100%'}} showSearch optionFilterProp="label" placeholder="选择已有宝塔预检"
        loading={busy} disabled={busy} value={selectedId} onChange={loadTemplate}
        options={items.map(item => ({value: item.template_id, label: `#${item.template_id} · ${item.operation_label || item.operation} · ${(item.panels || []).map(panel => panel.name).join('、')}`}))}/>
      {canCreate && <Button disabled={busy} onClick={openCreate}>新建宝塔预检</Button>}
      <Button disabled={busy} onClick={() => setRefresh(current => current + 1)}>刷新列表</Button>
    </Space>
    {waiting && <Space><Spin/><span>等待预检完成，正在自动读取固定范围…</span></Space>}
    {error && <Alert showIcon type="error" message={error} action={selectedId ?
      <Button onClick={() => loadTemplate(selectedId)}>重新读取预检</Button> : <Button onClick={() => setRefresh(current => current + 1)}>重试读取</Button>}/>}
    <PanelPlanSummary plan={value} onChange={onChange}/>
    {creating && options && <Modal open width={1100} title="为任务计划生成宝塔预检" maskClosable={false} footer={null} onCancel={() => setCreating(false)}>
      <Alert showIcon type="info" message="这里仅生成预检，完成后返回本计划核对固定范围和影响项。保存后的计划默认未激活。"/>
      <TaskForm options={options} onPrepared={loadTemplate}/>
    </Modal>}
  </div>;
}
