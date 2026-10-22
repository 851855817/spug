import React, { useState } from 'react';
import { Alert, Button, Input, InputNumber, Select, Space, Table } from 'antd';
import { parseSiteRows, phpLabel } from './helpers';

export default function SiteGrid({panels, rows, onChange, resources}) {
  const [paste, setPaste] = useState('');
  function update(index, patch) {onChange(rows.map((row, i) => i === index ? {...row, ...patch} : row));}
  const columns = [
    {title: '面板（每行必选）', key: 'panel', width: 180, render: (_, row, i) => <Select style={{width: '100%'}} value={row.panel_id}
      onChange={panel_id => update(i, {panel_id, php_version: undefined})} options={panels.map(panel => ({value: panel.id, label: `${panel.name} (#${panel.id})`}))}/>},
    {title: '主域名、附加域名（逗号分隔）', key: 'domains', width: 260, render: (_, row, i) => <Input value={row.domains} onChange={e => update(i, {domains: e.target.value})}/>},
    {title: '根目录', key: 'path', width: 230, render: (_, row, i) => <Input placeholder="/www/wwwroot/example.com" value={row.path} onChange={e => update(i, {path: e.target.value})}/>},
    {title: 'PHP / 静态', key: 'php', width: 170, render: (_, row, i) => <Select style={{width: '100%'}} value={row.php_version}
      onChange={php_version => update(i, {php_version})} options={(resources[row.panel_id]?.php_versions || []).map(item => ({value: item.version, label: phpLabel(item.version)}))}/>},
    {title: '端口', key: 'port', width: 100, render: (_, row, i) => <InputNumber min={1} max={65535} value={row.port} onChange={port => update(i, {port})}/>},
    {title: '备注', key: 'remark', width: 160, render: (_, row, i) => <Input value={row.remark} onChange={e => update(i, {remark: e.target.value})}/>},
    {title: '', key: 'remove', width: 70, render: (_, row, i) => <Button danger type="link" onClick={() => onChange(rows.filter((_, index) => index !== i))}>删除</Button>},
  ];
  return <div>
    <Alert showIcon type="info" message="每行显式指定面板；不会把同一域名复制到所有面板。展开下方各面板读取本机 PHP 版本。"/>
    <Table style={{marginTop: 12}} size="small" rowKey={(_, index) => index} dataSource={rows} columns={columns} scroll={{x: 1300}} pagination={false}/>
    <Button style={{margin: '12px 0'}} onClick={() => onChange([...rows, {domains: '', path: '', port: 80, remark: ''}])}>增加建站行</Button>
    <Input.TextArea rows={3} value={paste} onChange={e => setPaste(e.target.value)} placeholder="从表格粘贴 TSV（不含标题）：面板 ID ⇥ 主域名,附加域名 ⇥ 根目录 ⇥ PHP 标识（74 / 00）⇥ 端口 ⇥ 备注"/>
    <Space style={{marginTop: 8}}><Button disabled={!paste.trim()} onClick={() => {onChange([...rows, ...parseSiteRows(paste)]); setPaste('');}}>导入粘贴行</Button></Space>
  </div>;
}
