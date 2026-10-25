import React, { useState } from 'react';
import { Alert, Descriptions, Space, Typography } from 'antd';
import Evidence, { Status } from './TaskEvidence';
import UpgradeReview from './UpgradeReview';
import { phpLabel } from './helpers';
import styles from './TaskDetail.module.css';

export default function TaskItem({operation, child}) {
  const [expanded, setExpanded] = useState(false);
  const snapshot = child.snapshot || {};
  const before = snapshot.before || {};
  const plan = snapshot.plan || {};
  const result = child.result || {};
  const observed = result.evidence || {};
  const upgrade = operation === 'panel_upgrade';
  const legacy = plan.mode === 'legacy_stable' || plan.channel === 'stable';
  const fields = [];
  let title = snapshot.label || '任务项目';
  if (upgrade) {
    const destination = observed.version || plan.checked_version || plan.version;
    const qualifier = observed.version ? '（已读回）' : legacy ? '候选 ' : '目标 ';
    title = `${before.panel_version || '未知版本'} → ${observed.version ? destination + qualifier : qualifier + (destination || '待查询')}`;
    if (legacy) fields.push(['通道', '官方稳定通道']);
  } else if (operation === 'file_upload') {
    const target = snapshot.target || {};
    fields.push(['最终目录', target.target_dir || '目录未确定']);
    fields.push(['目录来源', target.directory_source === 'individual' ? '单独设置' : '统一设置']);
    fields.push(['预计新增', `${Number(before.new_files) || 0} 个`]);
    fields.push(['预计覆盖', `${Number(before.overwrite_files) || 0} 个`]);
  } else {
    if (plan.path) fields.push(['目录', plan.path]);
    if (Array.isArray(plan.domains) && plan.domains.length) fields.push(['域名',
      plan.domains.length <= 3 ? plan.domains.join('、') : `${plan.domains.slice(0, 2).join('、')} 等 ${plan.domains.length} 个`]);
    if (plan.php_version && operation !== 'php_update') fields.push(['运行环境', phpLabel(plan.php_version)]);
    if (plan.port) fields.push(['端口', plan.port]);
    if (typeof plan.enabled === 'boolean') fields.push(['防跨站', plan.enabled ? '启用' : '关闭']);
    if (operation === 'site_rewrite' && typeof plan.rules === 'string') fields.push(['伪静态', plan.rules.trim() ?
      `更新规则（${plan.rules.split(/\r?\n/).filter(line => line.trim()).length} 行）` : '清空规则']);
    if (operation === 'software_install' && plan.m_version && plan.min_version) fields.push(['版本', `${plan.m_version} / ${plan.min_version}`]);
  }
  let message = result.message || snapshot.message;
  if (upgrade && child.state === 'ready') message = legacy ?
    '升级时面板会短暂断连，实际版本由官方稳定通道决定。' : '升级时面板会短暂断连。';
  if (upgrade && child.state === 'success' && observed.version) message = '升级完成，已核验实际版本。';
  if (message === '预检通过') message = '';
  const important = ['failed', 'uncertain', 'remote'].includes(child.state);
  return <div>
    <Space wrap className={styles.heading}>
      <Typography.Text strong>{title}</Typography.Text>
      {operation === 'site_rewrite' && child.state === 'already' ? <Typography.Text>配置一致，运行状态未核实</Typography.Text> : <Status state={child.state}/>}
    </Space>
    {fields.length > 0 && <div className={styles.fields}>{fields.map(([label, value]) => <span key={label}>
      <Typography.Text type="secondary">{label}：</Typography.Text>{value}
    </span>)}</div>}
    {message && (important ? <Alert showIcon type={child.state === 'failed' ? 'error' : 'warning'} message={message}/> :
      <Typography.Paragraph className={styles.message} type="secondary">{message}</Typography.Paragraph>)}
    <UpgradeReview operation={operation} snapshot={snapshot} evidence={observed}/>
    <details className={styles.details} onToggle={event => setExpanded(event.currentTarget.open)}>
      <summary>详细记录</summary>
      {expanded && <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="预检说明"><Evidence value={snapshot.message}/></Descriptions.Item>
        <Descriptions.Item label="目标"><Evidence value={snapshot.target}/></Descriptions.Item>
        <Descriptions.Item label="预检时状态"><Evidence value={before}/></Descriptions.Item>
        <Descriptions.Item label="执行计划与影响"><Evidence value={plan}/></Descriptions.Item>
        <Descriptions.Item label="结果与核验"><Evidence value={result}/></Descriptions.Item>
        <Descriptions.Item label="下发记录"><Evidence value={child.intents}/></Descriptions.Item>
        <Descriptions.Item label="人工判定"><Evidence value={child.resolutions}/></Descriptions.Item>
      </Descriptions>}
    </details>
  </div>;
}
