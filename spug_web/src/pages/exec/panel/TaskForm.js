import React, { useState } from 'react';
import { Alert, Button, Collapse, Form, Select, Space, TreeSelect, Typography } from 'antd';
import { http } from 'libs';
import { buildGroupTree } from '../../baota/helpers';
import { buildTaskRequest, panelBlocker, selectPanels } from './helpers';
import OperationForm from './OperationForm';
import SiteGrid from './SiteGrid';
import UploadForm from './UploadForm';

function certificateContext(options, search) {
  const params = new URLSearchParams(search);
  const panelId = Number(params.get('panel_id'));
  const siteId = Number(params.get('site_id'));
  const operation = params.get('operation');
  const panel = (options.panels || []).find(item => item.id === panelId);
  if (!Number.isSafeInteger(panelId) || panelId <= 0 || !Number.isSafeInteger(siteId) || siteId <= 0 ||
    !['ssl_apply', 'ssl_renew'].includes(operation) || !panel || panelBlocker(panel) ||
    !(options.operations || []).some(item => item.code === operation)) return null;
  return {panelId, siteId, operation};
}

export default function TaskForm({options, onPrepared, initialSearch = ''}) {
  const [context] = useState(() => certificateContext(options, initialSearch));
  const [panelIds, setPanelIds] = useState(context ? [context.panelId] : []);
  const [groupIds, setGroupIds] = useState([]);
  const [operation, setOperation] = useState(context?.operation);
  const [category, setCategory] = useState(context ? 'ssl' : undefined);
  const [values, setValues] = useState(context ? {[context.panelId]: {sites: [context.siteId]}} : {});
  const [resources, setResources] = useState({});
  const [rows, setRows] = useState([]);
  const [activeKeys, setActiveKeys] = useState([]);
  const [busy, setBusy] = useState(false);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [error, setError] = useState('');
  const panels = selectPanels(options.panels || [], options.groups || [], panelIds, groupIds);
  const categories = (options.operations || []).filter(item => item.code !== 'ssl_renew').map(item => ({
    value: item.code === 'ssl_apply' ? 'ssl' : item.code,
    label: item.code === 'ssl_apply' ? '免费 SSL 证书（申请 / 续签）' : item.label,
  }));
  function changeOperation(code) {
    setOperation(code); setValues({}); setResources({}); setRows([]); setError('');
  }
  async function prepare() {
    setError('');
    try {
      const adjusted = {};
      panels.forEach(panel => {adjusted[panel.id] = {...values[panel.id], webserver: values[panel.id]?.webserver || resources[panel.id]?.webserver};});
      const request = buildTaskRequest(operation, panels, adjusted, rows, resources);
      setBusy(true);
      const result = await http.post('/api/baota/tasks/', request);
      onPrepared(result.id);
    } catch (error) {setError(String(error?.message || error || '预检创建失败'));}
    finally {setBusy(false);}
  }
  return <div>
    <Alert showIcon type="info" message="选面板 → 填写各面板参数 → 生成预检 → 核对目标和影响范围 → 确认开始"
      description={operation === 'file_upload' ? '支持宝塔 Linux 9.2.0 / 11.7.0 / 13.1.0，最多 50 个面板。先暂存本地文件并生成预检，核对各面板目录及覆盖范围后确认开始。' :
        '支持宝塔 Linux 9.2.0 / 11.7.0 / 13.1.0，最多 50 个面板、200 个子项目。安装软件、更新 PHP、升级面板分别选择。查询可能触发宝塔内部缓存或初始化；只有确认开始后才下发所选变更。'}/>
    {context && <Alert showIcon type="info" style={{marginTop: 12}} message={`已从盘点选择面板 #${context.panelId} / 网站 #${context.siteId}，尚未读取或执行任务。`}
      description="展开面板核对当前网站和证书信息，再按原流程生成预检。盘点记录可能已过期，站点身份和同订单续签条件由预检重新核对。"/>}
    <Form layout="vertical" style={{marginTop: 16}}>
      <Form.Item label="选择面板"><Select mode="multiple" showSearch optionFilterProp="label" placeholder="选择面板" style={{width: '100%'}} value={panelIds} disabled={uploadBusy}
        onChange={setPanelIds} options={(options.panels || []).map(panel => ({value: panel.id, label: `${panel.name} · ${panel.group_name || '未分组'} · ${panel.api_version || '版本未知'}`}))}/></Form.Item>
      <Form.Item label="或选择分组（递归包括所有子组，与面板选择去重）"><TreeSelect multiple treeCheckable treeData={buildGroupTree(options.groups || [])}
        value={groupIds} onChange={setGroupIds} style={{width: '100%'}} placeholder="选择分组" disabled={uploadBusy}/></Form.Item>
      <Typography.Paragraph>本次固定目标：{panels.length} 个面板 {panels.map(panel => panel.name).join('、')}</Typography.Paragraph>
      <Form.Item label="选择操作"><Select placeholder="选择操作" value={category} disabled={uploadBusy} onChange={code => {
        setCategory(code); changeOperation(code === 'ssl' ? 'ssl_apply' : code);
      }} options={categories}/></Form.Item>
      {category === 'ssl' && <Form.Item label="证书动作"><Select value={operation} onChange={changeOperation}
        options={[{value: 'ssl_apply', label: '单次申请免费证书（HTTP 验证）'}, {value: 'ssl_renew', label: '同订单续签免费证书（HTTP 验证）'}]}/></Form.Item>}
    </Form>
    {operation === 'file_upload' ? <UploadForm panels={panels} options={options} onPrepared={onPrepared} onBusyChange={setUploadBusy}/> : <>
    {operation === 'site_add' && <SiteGrid panels={panels} rows={rows} onChange={setRows} resources={resources}/>}
    <Collapse activeKey={activeKeys} onChange={setActiveKeys} destroyInactivePanel style={{margin: '16px 0'}}>
      {panels.map(panel => <Collapse.Panel key={String(panel.id)} header={`${panel.name} (#${panel.id}) · ${resources[panel.id]?.compatibility?.version || panel.api_version || '版本未知'} · ${panel.api_configured ? 'API 已配置' : 'API 未配置'}`}>
        {operation && <OperationForm key={`${operation}:${panel.id}`} panel={panel} blocked={panelBlocker(panel)} operation={operation} value={values[panel.id] || {}}
          active={activeKeys.includes(String(panel.id))} onChange={value => setValues(current => ({...current, [panel.id]: value}))}
          onResource={data => setResources(current => ({...current, [panel.id]: {...current[panel.id], ...data}}))}/ >}
      </Collapse.Panel>)}
    </Collapse>
    {error && <Alert type="error" message={error} style={{marginBottom: 12}}/>}
    <Space><Button type="primary" disabled={!operation || !panels.length || panels.length > 50} loading={busy} onClick={prepare}>生成冻结预检</Button>
      <Typography.Text type="secondary">每次生成新的预检任务，已打开的预览保持原样。</Typography.Text></Space>
    </>}
  </div>;
}
