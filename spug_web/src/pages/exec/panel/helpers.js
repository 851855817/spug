import { descendantIds } from '../../baota/helpers';

export const OPERATION_LABELS = {
  panel_upgrade: '升级面板', site_add: '批量建站', site_basedir: '防跨站设置', site_rewrite: '伪静态设置',
  ssl_apply: '申请免费 SSL', ssl_renew: '续签免费 SSL', software_install: '安装软件',
  php_extension_install: '安装 PHP 扩展', software_uninstall: '卸载软件',
  php_update: '更新 PHP（同分支）',
  file_upload: '上传文件 / 目录',
};
export const STATE_LABELS = {
  preflight_queued: '等待预检', preflighting: '预检中', prepared: '预检完成', ready: '待执行',
  already: '已满足', skipped: '已跳过', queued: '等待执行', running: '执行中', dispatching: '正在下发',
  reconciling: '只读核验中', remote: '远端处理中', uncertain: '结果不确定', failed: '失败',
  cancelled: '已取消未下发项', completed: '已结束（请查看结果）', success: '成功',
};
export const SSL_ACK = '初次申请或续签可能自动同步同一组域名证书，影响本面板内未选网站、孤立证书副本及面板自身 TLS，并重载 Web 服务；续签可能暂时关闭强制 HTTPS。所列同步清单不完整，所选网站配置核验不代表所有受影响的线上 TLS 已生效。';

export function selectPanels(panels, groups, panelIds, groupIds) {
  const selected = new Set(panelIds);
  const selectedGroups = new Set();
  groupIds.forEach(id => descendantIds(groups, id).forEach(value => selectedGroups.add(value)));
  return panels.filter(panel => selected.has(panel.id) || selectedGroups.has(panel.group_id));
}

export function parseSiteRows(text) {
  return text.trim().split(/\r?\n/).filter(line => line.trim()).map(line => {
    const [panel_id, domains = '', path = '', php_version = '', port = '80', remark = ''] = line.split('\t');
    return {panel_id: Number(panel_id), domains, path, php_version, port: Number(port || 80), remark};
  });
}

export function panelBlocker(panel) {
  if (panel.api_configured === false) return 'API 尚未配置，请先在面板管理中配置 API 并测试连接';
  if (panel.api_checked === false) return 'API 尚未通过连接测试';
  if (panel.api_checked === true && !['9.2.0', '11.7.0', '13.1.0'].includes(panel.api_version)) return '面板版本未知或未适配；已适配 9.2.0 / 11.7.0 / 13.1.0，请重新测试连接';
  return '';
}

export function dnsNames(value) {
  const values = Array.isArray(value) ? value : String(value || '').split(/[,，;；\s]+/).filter(Boolean);
  if (!values.length) throw new Error('请填写主域名及附加域名');
  return [...new Set(values.map(raw => {
    const name = raw.trim().toLowerCase();
    if (name.length > 253 || !name.includes('.') || /^\d+$/.test(name.split('.').pop()) ||
      !name.split('.').every(label => label.length <= 63 && /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label))) {
      throw new Error(`域名“${raw}”无效：仅支持普通 DNS 域名，不支持通配符、URL、IP 或端口`);
    }
    return name;
  }))];
}

function required(value, label) {
  if (value === undefined || value === null || String(value).trim() === '') throw new Error(`请填写${label}`);
  return String(value).trim();
}

function siteSelector(value, resources, panelId) {
  if (value.all_sites) return {all_sites: true};
  const sites = [...new Set(value.sites || [])];
  if (!sites.length) throw new Error(`面板 ${panelId} 请选择网站或本次全部网站`);
  if (resources?.sites && sites.some(id => !resources.sites.some(site => site.id === id))) {
    throw new Error(`网站不属于面板 ${panelId}，请重新选择`);
  }
  return {sites};
}

export function buildTaskRequest(operation, panels, values, rows = [], resources = {}) {
  if (!OPERATION_LABELS[operation]) throw new Error('请选择操作');
  if (!panels.length || panels.length > 50) throw new Error('请选择 1 至 50 个面板');
  if (operation === 'site_add' && rows.some(row => !panels.some(panel => panel.id === Number(row.panel_id)))) {
    throw new Error('建站行的面板不在本次选择中');
  }
  const params_by_panel = {};
  panels.forEach(panel => {
    if (panelBlocker(panel)) {params_by_panel[String(panel.id)] = {}; return;}
    const value = values[panel.id] || {};
    let params;
    if (operation === 'site_add') {
      const entries = rows.filter(row => Number(row.panel_id) === panel.id).map(row => {
        const port = Number(row.port || 80);
        if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('网站端口必须为 1 至 65535');
        const path = required(row.path, '网站根目录');
        if (!path.startsWith('/')) throw new Error('网站根目录必须为绝对路径');
        return {domains: dnsNames(row.domains), path, php_version: required(row.php_version, 'PHP 版本'), port, remark: row.remark || ''};
      });
      if (!entries.length) throw new Error(`面板 ${panel.name} 至少需要一行建站数据`);
      params = {entries};
    } else if (['site_basedir', 'site_rewrite', 'ssl_apply', 'ssl_renew'].includes(operation)) {
      params = siteSelector(value, resources[panel.id], panel.id);
      if (operation === 'site_basedir') params.enabled = value.enabled !== false;
      if (operation === 'site_rewrite') {
        params.mode = value.mode || 'custom';
        params.webserver = required(value.webserver, 'Web 服务类型');
        if (params.mode === 'custom') params.rules = required(value.rules, '伪静态规则');
        if (params.mode === 'template') params.template = required(value.template, '伪静态模板');
      }
      if (operation.startsWith('ssl_') && value.domains && !params.all_sites) {
        const domains = {};
        Object.entries(value.domains).forEach(([id, hosts]) => {
          if (!params.sites.some(site => String(site) === id)) return;
          if ((Array.isArray(hosts) ? hosts.length : String(hosts).trim())) domains[id] = dnsNames(hosts);
        });
        if (Object.keys(domains).length) params.domains = domains;
      }
    } else if (operation === 'panel_upgrade') {
      if (resources[panel.id]?.upgrade_loading) throw new Error(`面板 ${panel.name} 升级信息读取中，请稍候`);
      if (resources[panel.id]?.upgrade_available === false) throw new Error(`面板 ${panel.name} 升级信息暂不可用，请重新读取`);
      const mode = resources[panel.id]?.upgrade_mode;
      if (!['legacy_stable', 'exact'].includes(mode)) throw new Error(`请先展开面板 ${panel.name} 并读取升级信息`);
      params = mode === 'legacy_stable' ? {channel: required(value.channel, '官方升级通道')} : {version: required(value.version, '升级目标版本')};
    } else if (operation === 'php_update') {
      params = {php_version: required(value.php_version, '待更新的 PHP 分支'), m_version: required(value.m_version, 'PHP 主版本'),
        min_version: required(value.min_version, 'PHP 预计补丁版本'), install_type: '0'};
    } else if (operation === 'software_install') {
      params = {name: required(value.name, '软件'), m_version: required(value.m_version, '主版本'),
        min_version: required(value.min_version, '子版本'), install_type: value.install_type || '0'};
    } else if (operation === 'software_uninstall') {
      params = {name: required(value.name, '待卸载软件')};
    } else {
      if (!value.extensions?.length) throw new Error('请选择要安装的 PHP 扩展');
      params = {php_version: required(value.php_version, 'PHP 版本'), extensions: [...new Set(value.extensions)], install_type: value.install_type || '0'};
    }
    params_by_panel[String(panel.id)] = params;
  });
  const request = {operation, panel_ids: panels.map(panel => panel.id), params_by_panel};
  if (new Blob([JSON.stringify(request)]).size > 1024 * 1024) throw new Error('任务参数超过 1 MiB，请缩小批次');
  return request;
}

export function summarizeTask(task) {
  const counts = {success: 0, failed: 0, uncertain: 0, remote: 0, ready: 0, already: 0, skipped: 0, cancelled: 0, pending: 0};
  (task.targets || []).forEach(target => {
    if (task.operation === 'file_upload' && target.summary?.upload) {
      const summary = target.summary.upload;
      ['success', 'failed', 'uncertain', 'cancelled'].forEach(key => {counts[key] += Math.max(0, Number(summary[key]) || 0);});
      counts[task.state === 'prepared' ? 'ready' : 'pending'] += Math.max(0, Number(summary.pending) || 0);
      return;
    }
    if (!(target.children || []).length && target.state === 'failed') counts.failed++;
    (target.children || []).forEach(child => {
      if (Object.prototype.hasOwnProperty.call(counts, child.state)) counts[child.state]++;
      else counts.pending++;
    });
  });
  return counts;
}

export function stateColor(state) {
  if (['success', 'already'].includes(state)) return 'green';
  if (state === 'failed') return 'red';
  if (['uncertain', 'remote'].includes(state)) return 'orange';
  if (['ready', 'prepared', 'running', 'dispatching', 'preflight_queued', 'preflighting', 'reconciling'].includes(state)) return 'blue';
  return 'default';
}

export function startPayload(task, acknowledged) {
  const required = task.required_acknowledgements || [];
  if (task.state !== 'prepared' || !summarizeTask(task).ready) throw new Error('仅可开始有待执行项目的已完成预检');
  if (required.length !== acknowledged.length || required.some(code => !acknowledged.includes(code))) throw new Error('请逐项确认预检影响范围');
  return {acknowledgements: [...required]};
}

export function phpLabel(version) {
  if (version === '00') return '静态网站';
  return `PHP ${String(version).split('').join('.')}`;
}
