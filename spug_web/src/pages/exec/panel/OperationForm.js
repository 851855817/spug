import React from 'react';
import { Alert, Checkbox, Form, Input, Select, Spin, Typography } from 'antd';
import { phpLabel } from './helpers';
import { useResources } from './resources';

function Field({label, children}) {return <Form.Item label={label}>{children}</Form.Item>;}
const SITE_OPERATIONS = ['site_basedir', 'site_rewrite', 'ssl_apply', 'ssl_renew'];
const TYPES = [{value: '0', label: '编译安装'}, {value: '1', label: '快速安装'}];

export default function OperationForm({panel, operation, value, onChange, active, onResource, blocked}) {
  const kinds = {panel_upgrade: ['upgrade_versions'], site_add: ['sites', 'php_versions'], site_basedir: ['sites', 'webserver'],
    site_rewrite: ['sites', 'webserver'], ssl_apply: ['sites'], ssl_renew: ['sites'],
    software_install: ['software'], software_uninstall: ['software'], php_extension_install: ['php_versions', 'webserver']};
  const base = useResources(panel.id, (kinds[operation] || []).map(kind => ({kind})), active && !blocked, onResource);
  const sites = base.data.sites || [];
  const selectedIds = value.all_sites ? sites.map(site => site.id) : value.sites || [];
  const needsSite = ['site_rewrite', 'ssl_apply', 'ssl_renew'].includes(operation);
  const detail = useResources(panel.id, needsSite ? selectedIds.map(site_id => ({kind: 'site_options', site_id})) : [], active && !blocked, onResource);
  const extensions = useResources(panel.id, value.php_version ? [{kind: 'php_extensions', php_version: value.php_version}] : [],
    active && !blocked && operation === 'php_extension_install', onResource);
  const errors = {...base.errors, ...detail.errors, ...extensions.errors};
  const change = patch => onChange({...value, ...patch});
  const software = (base.data.software || []).find(item => item.name === value.name);
  const options = selectedIds.map(id => detail.data[`site:${id}`]).filter(Boolean);
  const templates = options.length ? (options[0].rewrite_templates || []).filter(name => options.every(item => (item.rewrite_templates || []).includes(name))) : [];
  const loading = base.loading || detail.loading || extensions.loading;
  if (blocked) return <Alert showIcon type="error" message={blocked} description="该面板保留在预检目标中；服务器会记录失败原因，其它面板可独立完成预检。"/>;
  return <Form layout="vertical">
    {loading && <Spin size="small"/>}
    {Object.entries(errors).map(([key, error]) => <Alert key={key} type="error" showIcon message={`${key}：${error}`}/>)}
    {base.data.compatibility && <Typography.Paragraph type="secondary">实际版本 {base.data.compatibility.version} · {base.data.compatibility.system}</Typography.Paragraph>}
    {SITE_OPERATIONS.includes(operation) && <>
      <Field label="网站范围（只属于当前面板）"><Checkbox checked={Boolean(value.all_sites)} onChange={e => change({all_sites: e.target.checked, sites: [], domains: {}})}>本次全部网站（以预检时冻结列表为准）</Checkbox></Field>
      {!value.all_sites && <Field label="选择网站"><Select mode="multiple" style={{width: '100%'}} value={value.sites || []}
        onChange={sites => change({sites, domains: {}})} options={sites.map(site => ({value: site.id, label: `${site.name} · #${site.id} · ${site.path} · ${site.php_version ? phpLabel(site.php_version) : 'PHP 未识别'}`}))}/></Field>}
    </>}
    {operation === 'site_basedir' && <Field label="防跨站 open_basedir"><Select value={value.enabled !== false} onChange={enabled => change({enabled})}
      options={[{value: true, label: '启用'}, {value: false, label: '关闭'}]}/></Field>}
    {operation === 'site_rewrite' && <>
      <Field label="Web 服务（按本面板读取）"><Select value={value.webserver || base.data.webserver} onChange={webserver => change({webserver})}
        options={['nginx', 'apache'].filter(item => item === base.data.webserver).map(item => ({value: item, label: item}))}/></Field>
      <Field label="规则来源"><Select value={value.mode || 'custom'} onChange={mode => change({mode, template: undefined, rules: ''})}
        options={[{value: 'custom', label: '自定义规则'}, {value: 'template', label: '本面板模板'}, {value: 'clear', label: '清空规则'}]}/></Field>
      {(value.mode || 'custom') === 'custom' && <Field label="规则文本（最多 64 KiB）"><Input.TextArea rows={6} value={value.rules} onChange={e => change({rules: e.target.value})}/></Field>}
      {value.mode === 'template' && <Field label="选中网站均可用的模板"><Select value={value.template} onChange={template => change({template})} options={templates.map(name => ({value: name, label: name}))}/></Field>}
      {options.map(item => item.rewrite_available ? null : <Alert key={item.id} type="warning" message={`网站 #${item.id}：${item.rewrite_message || '伪静态不可用'}`}/>)}
    </>}
    {operation.startsWith('ssl_') && <>
      <Alert type="info" message="仅支持 HTTP 验证的单次免费申请 / 同订单续签。默认使用网站现有域名，禁止通配符及 DNS 验证。"/>
      {options.map(item => <div key={item.id} style={{marginTop: 12}}>
        <Typography.Text>网站 #{item.id} · 域名 {(item.domains || []).map(x => x.name).join(', ')} · SSL {item.ssl?.enabled ? '已启用' : '未启用'}</Typography.Text>
        {!value.all_sites && <Field label="本次域名（留空使用已存在域名；逗号分隔）"><Input value={value.domains?.[item.id] || ''}
          onChange={e => change({domains: {...value.domains, [item.id]: e.target.value}})}/></Field>}
      </div>)}
    </>}
    {operation === 'site_add' && <Typography.Paragraph>本面板已读取 {sites.length} 个网站；可用版本：{(base.data.php_versions || []).map(item => phpLabel(item.version)).join('、') || '暂无'}。在上方建站表格逐行填写。</Typography.Paragraph>}
    {operation === 'panel_upgrade' && <>
      <Typography.Paragraph>当前版本：{base.data.current_version || '未知'}。仅支持已适配的精确版本 11.7.0 / 13.1.0。</Typography.Paragraph>
      <Field label="本面板提供的升级版本"><Select value={value.version} onChange={version => change({version})} options={(base.data.versions || []).map(version => ({value: version, label: version}))}/></Field>
    </>}
    {['software_install', 'software_uninstall'].includes(operation) && <>
      <Field label={operation === 'software_install' ? '本面板软件目录' : '本面板已安装软件'}><Select showSearch value={value.name}
        onChange={name => change({name, m_version: undefined, min_version: undefined})} options={(base.data.software || []).filter(item => operation === 'software_install' || item.installed)
          .map(item => ({value: item.name, label: `${item.title} ${item.installed ? `· 已装 ${item.installed_version}` : '· 未安装'}`}))}/></Field>
      {operation === 'software_install' && <>
        <Alert type="info" message="软件安装不隐含升级，不切换网站 PHP 版本。已安装或依赖冲突由预检阻挡。"/>
        <Field label="本面板软件版本（主版本 / 子版本）"><Select value={value.m_version && value.min_version ? `${value.m_version}|${value.min_version}` : undefined}
          onChange={version => {const [m_version, min_version] = version.split('|'); change({m_version, min_version});}}
          options={(software?.versions || []).map(item => ({value: `${item.m_version}|${item.min_version}`, label: `${item.m_version} / ${item.min_version}${item.mem_limit ? ` · 内存要求 ${item.mem_limit}` : ''}${item.cpu_limit ? ` · CPU 要求 ${item.cpu_limit}` : ''}${item.os_limit ? ` · 系统约束 ${item.os_limit}` : ''}`}))}/></Field>
      </>}
    </>}
    {operation === 'php_extension_install' && <>
      <Alert type="info" message="本操作只安装 PHP 扩展，PHP 7.4 显示为 74；不会升级 PHP 软件或切换网站版本。"/>
      <Field label="本面板 PHP 分支"><Select value={value.php_version} onChange={php_version => change({php_version, extensions: []})}
        options={(base.data.php_versions || []).filter(item => item.version !== '00').map(item => ({value: item.version, label: phpLabel(item.version)}))}/></Field>
      <Field label="本分支可用扩展"><Select mode="multiple" value={value.extensions || []} onChange={extensions => change({extensions})}
        options={(extensions.data.php_extensions || []).map(item => ({value: item.name, label: `${item.name}${item.configured ? ' · 已配置' : ''}`}))}/></Field>
    </>}
    {['software_install', 'php_extension_install'].includes(operation) && <Field label="安装方式"><Select value={value.install_type || '0'} onChange={install_type => change({install_type})} options={TYPES}/></Field>}
  </Form>;
}
