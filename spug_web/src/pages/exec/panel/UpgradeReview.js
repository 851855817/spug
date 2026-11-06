import React, { useState } from 'react';
import { Alert, Input, Pagination, Typography } from 'antd';
import styles from './TaskDetail.module.css';

function ModulesReview({baseline, evidence}) {
  const [open, setOpen] = useState(false);
  const check = evidence.cli_module_check || {};
  const comparable = ['unchanged', 'changed'].includes(check.state) && Array.isArray(check.added) && Array.isArray(check.removed);
  const changed = comparable && (check.added.length > 0 || check.removed.length > 0);
  let summary = '缺少对照记录';
  if (comparable) summary = `${check.removed.length ? `缺失 ${check.removed.length} 个` : '未发现缺失'}${check.added.length ? ` · 新增 ${check.added.length} 个` : ''}`;
  else if (check.reason === 'readback_unavailable') summary = '读回清单未核实';
  else if (!evidence.version && Array.isArray(baseline) && baseline.length) summary = `已记录 ${baseline.length} 个，等待更新后对照`;
  return <>
    <Typography.Paragraph className={styles.message} type={comparable && check.removed.length ? 'danger' : 'secondary'}>
      CLI 扩展：{summary} · FPM、默认 CLI 与网站请求未核实
    </Typography.Paragraph>
    {changed && <details className={styles.details} aria-label="CLI 扩展变化" onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>查看扩展变化</summary>
      {open && <div>
        {check.removed.length > 0 && <Typography.Paragraph>缺失：{check.removed.join('、')}</Typography.Paragraph>}
        {check.added.length > 0 && <Typography.Paragraph>新增：{check.added.join('、')}</Typography.Paragraph>}
      </div>}
    </details>}
  </>;
}

function WebsiteImpact({refs}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  if (!Array.isArray(refs) || refs.some(site => !site || typeof site !== 'object')) {
    return <Typography.Paragraph type="secondary">影响网站：未生成清单</Typography.Paragraph>;
  }
  if (!refs.length) return <Typography.Paragraph type="secondary">本次预检未发现使用该分支的网站</Typography.Paragraph>;
  const needle = query.trim().toLowerCase();
  const matches = refs.filter(site => `${site.name || ''}\n${site.path || ''}`.toLowerCase().includes(needle));
  const current = Math.min(page, Math.max(1, Math.ceil(matches.length / 10)));
  return <details className={styles.details} aria-label="影响网站清单" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>影响网站（{refs.length}）</summary>
    {open && <div>
      <Input aria-label="检索影响网站" placeholder="按网站名称或目录检索" value={query} onChange={event => {
        setQuery(event.target.value); setPage(1);
      }}/>
      <Typography.Text type="secondary">匹配 {matches.length} / {refs.length} 个 · 本次预检快照</Typography.Text>
      {matches.length ? <ul className={styles.impactSites}>{matches.slice((current - 1) * 10, current * 10).map((site, index) =>
        <li key={`${site.id}:${index}`} data-upgrade-site>
          <span>{site.name || `网站 #${site.id}`}</span>
          <Typography.Text type="secondary">{site.path || '目录未记录'}</Typography.Text>
        </li>)}</ul> : <Typography.Paragraph>没有匹配的网站</Typography.Paragraph>}
      {matches.length > 10 && <Pagination size="small" current={current} pageSize={10} total={matches.length}
        showSizeChanger={false} onChange={setPage}/>}
    </div>}
  </details>;
}

export default function UpgradeReview({operation, snapshot = {}, evidence = {}}) {
  if (!['php_update', 'panel_upgrade'].includes(operation)) return null;
  const php = operation === 'php_update';
  const before = snapshot.before || {};
  const plan = snapshot.plan || {};
  const legacy = plan.mode === 'legacy_stable' || plan.channel === 'stable';
  const fields = [
    ['预检版本', (php ? before.runtime?.version : before.panel_version) || '未记录'],
    [legacy ? '检查候选' : '预计目标', (php ? plan.target_version : plan.checked_version || plan.version) || '未记录'],
    ['实际读回', evidence.version || '尚未读回'],
  ];
  return <section className={styles.upgradeReview} aria-label="升级核对">
    <Typography.Text strong>升级核对</Typography.Text>
    <div className={styles.fields}>{fields.map(([label, value]) => <span key={label}>{label}：{value}</span>)}</div>
    <Typography.Paragraph type="secondary" className={styles.message}>
      {php ? '补丁由官方脚本决定；核验范围：目标分支 PHP CLI 二进制。' : legacy ?
        '官方后台决定实际版本，检查候选未锁定；升级期间面板会短暂断连。' : '核验范围：面板重新连接及版本读回。'}
    </Typography.Paragraph>
    {evidence.version_supported === false && <Alert type="warning" showIcon message="读回版本尚未适配，请人工核实。"/>}
    {php && <>
      <ModulesReview baseline={before.runtime?.cli_modules} evidence={evidence}/>
      <WebsiteImpact key={snapshot.key} refs={plan.impact?.website_refs}/>
    </>}
    {Array.isArray(plan.blockers) && plan.blockers.length > 0 && <Alert type="error" showIcon
      message={`预检阻挡：${plan.blockers.join('；')}`}/>}
  </section>;
}
