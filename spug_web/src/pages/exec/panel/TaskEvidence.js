import React from 'react';
import { Tag } from 'antd';
import { STATE_LABELS, stateColor } from './helpers';

export function Status({state}) {
  return <Tag color={stateColor(state)}>{STATE_LABELS[state] || state || '未知'}</Tag>;
}

const LABELS = {
  key: '项目标识', label: '项目名称', target: '目标', before: '当前状态', plan: '拟执行动作', state: '状态',
  site: '网站', site_id: '网站 ID', id: 'ID', name: '名称', path: '目录', domains: '域名', domain: '域名', port: '端口',
  php_version: 'PHP 版本', enabled: '启用', mode: '模式', rules: '规则', template: '模板', webserver: 'Web 服务',
  version: '版本', current_version: '当前版本', installed_version: '已装版本', m_version: '主版本', min_version: '子版本',
  ssl_scope: '证书同步范围', policy: '同步策略', ssl_sync_inventory: '同步影响清单（不完整）', complete: '清单完整',
  sites: '网站', registered_sites: '登记网站', panel_tls: '面板 TLS', unselected_sites: '未选网站',
  evidence: '核验依据', message: '说明', configuration_saved: '配置已保存', runtime_verified: '运行时已验证',
  phase: '阶段', at: '记录时间', returned_at: '返回时间', status: '状态', outcome: '判定',
  api_config_revision: 'API 配置修订', reason: '人工判定理由', actor_id: '操作者', created_at: '创建时间',
  action: '动作', event: '事件', data: '内容', details: '详情', certificate: '证书', index: '证书订单',
  extensions: '扩展', install_type: '安装方式', dependencies: '依赖', blockers: '阻挡原因',
  scope: '影响范围', may_update_unselected_sites: '可能更新未选网站', may_update_orphan_certificates: '可能更新孤立证书副本',
  may_update_panel_tls: '可能更新面板自身 TLS', web_service_reload: '可能重载 Web 服务',
  renewal_http_to_https_toggle: '续签时可能临时切换强制 HTTPS', candidate_directory_roots: '面板可能扫描的目录范围',
  eligibility: '证书同步条件', inventory_complete: '同步清单完整', coverage: '已读取范围',
  registered_site_count: '面板登记网站数', scanned_site_count: '本次读取网站数', scan_limit: '读取上限',
  truncated: '已达到读取上限', not_enumerated: '未枚举的影响位置', matching_sites: '已读取的同域名证书网站',
  site_name: '网站名称', expires_at: '证书到期时间', not_before: '证书生效时间',
  panel_sync_outcome_verified: '全面板同步结果已验证', ssl_scope_policy: '证书同步策略',
  config: '配置', identity: '网站身份', project_type: '项目类型', run_path: '运行目录', file: '规则文件',
  requirements: '安装先决条件', cpu_cores: 'CPU 核数', memory_mb: '内存（MiB）', os_family: '操作系统',
  installed: '已安装', extension: '扩展', configured: '已配置', system: '系统', panel_version: '面板版本',
  task_ids: '面板安装队列 ID', remote: '远端任务记录', required_acknowledgements: '必须确认的影响项',
  auth_type: '证书验证方式', auth_to: '证书验证目标', reuse_index: '可复用订单',
};

const VALUES = {
  baota_acme_panel_sync_v1: '宝塔申请 / 续签时自动同步同域名证书（面板范围）',
  panel_wide_same_san_eligible_certificates: '整个面板中符合条件且域名集合相同的证书',
  immediate_child_certificate_with_same_san_set_compatible_issuer_and_not_later_expiry: '相同域名集合、兼容签发者且到期时间不晚于新证书的直接子目录证书',
  registered_sites_GetSSL_only: '仅面板登记网站的 SSL 配置；不能作为完整白名单',
  orphan_certificate_directories: '孤立证书目录', unregistered_certificate_copies: '未登记证书副本', panel_tls: '面板自身 TLS',
  apply: '初次下发', deploy: '部署到所选网站', issue: '证书签发', issue_and_sync: '签发 / 续签并自动同步',
  http: 'HTTP 验证', dns: 'DNS 验证', custom: '自定义规则', template: '面板模板', clear: '清空规则',
  ssl_panel_sync: '确认面板范围内证书同步影响', official_source_reviewed: '官方协议已适配',
  success: '成功', failed: '失败', uncertain: '结果不确定', remote: '远端处理中',
};

function technical(key) {return /hash|digest|fingerprint|^md5$|^sha1$/.test(key);}

export default function Evidence({value}) {
  if (value === null || value === undefined) return <span>—</span>;
  if (typeof value === 'boolean') return <span>{value ? '是' : '否'}</span>;
  if (typeof value !== 'object') return <span style={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{VALUES[value] || String(value)}</span>;
  if (Array.isArray(value)) return value.length ? <ol style={{paddingLeft: 20}}>{value.map((item, index) => <li key={index}><Evidence value={item}/></li>)}</ol> : <span>无</span>;
  const entries = Object.entries(value);
  const hashes = Object.fromEntries(entries.filter(([key]) => technical(key)));
  return <><dl style={{margin: 0}}>{entries.filter(([key]) => !technical(key)).map(([key, item]) => <div key={key} style={{marginBottom: 6}}>
    <dt style={{fontWeight: 500}}>{LABELS[key] || key}</dt><dd style={{marginLeft: 16}}><Evidence value={item}/></dd>
  </div>)}</dl>{Object.keys(hashes).length > 0 && <details><summary>技术核对摘要（哈希 / 指纹）</summary>
    <pre style={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{JSON.stringify(hashes, null, 2)}</pre></details>}</>;
}
