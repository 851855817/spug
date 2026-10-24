import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Empty, Modal, Tag } from 'antd';
import { Breadcrumb } from 'components';
import { hasPermission, http } from 'libs';
import styles from './index.module.css';

const endpoint = '/api/setting/health/';
const recoverableServices = ['nginx', 'spug-api', 'spug-monitor', 'spug-panel-worker', 'spug-scheduler', 'spug-worker', 'spug-ws'];
const stoppedStates = ['STOPPED', 'EXITED', 'FATAL'];
const statuses = {
  ok: {label: '正常', color: 'green'},
  warning: {label: '需关注', color: 'orange'},
  error: {label: '异常', color: 'red'},
  unknown: {label: '待核实', color: 'default'},
};

function canRecover(check) {
  return check.recoverable === true && recoverableServices.includes(check.service) &&
    stoppedStates.includes(check.process_state) && ['warning', 'error'].includes(check.status);
}

function formatTime(value) {
  const date = new Date(value);
  return value && !Number.isNaN(date.getTime()) ? date.toLocaleString('zh-CN', {hour12: false}) : '—';
}

export default function PlatformHealth() {
  const allowed = hasPermission('admin');
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [starting, setStarting] = useState(null);
  const [actionResult, setActionResult] = useState(null);
  const mounted = useRef(false);
  const readPending = useRef(false);
  const startPending = useRef(false);
  const revision = useRef(0);
  const latest = useRef();
  latest.current = {snapshot, error, allowed};

  const refresh = useCallback(async () => {
    if (!allowed || !mounted.current || readPending.current) return;
    const request = ++revision.current;
    readPending.current = true;
    setLoading(true);
    try {
      const result = await http.get(endpoint);
      if (mounted.current && request === revision.current) {
        setSnapshot(result);
        setError(false);
      }
    } catch (_) {
      if (mounted.current && request === revision.current) setError(true);
    } finally {
      if (request === revision.current) {
        readPending.current = false;
        if (mounted.current) setLoading(false);
      }
    }
  }, [allowed]);

  useEffect(() => {
    mounted.current = true;
    refresh();
    return () => {
      mounted.current = false;
      revision.current += 1;
      readPending.current = false;
    };
  }, [refresh]);

  async function startService(id, service, confirmedRevision) {
    if (!mounted.current || !hasPermission('admin') || !latest.current.allowed || readPending.current || startPending.current) return;
    const current = latest.current.snapshot?.checks?.find(check => check.id === id && check.service === service);
    if (confirmedRevision !== revision.current || latest.current.error || !current || !canRecover(current)) {
      setActionResult({type: 'warning', message: '服务状态已变化或尚未确认，请重新检查后再操作。'});
      return;
    }
    startPending.current = true;
    setStarting(service);
    setActionResult(null);
    try {
      const result = await http.post(endpoint, {action: 'start', service});
      if (!mounted.current || !latest.current.allowed) return;
      const prefix = result.status === 'started' ? '启动命令已返回。' : result.status === 'refused' ? '未启动服务。' : '启动结果待核实。';
      setActionResult({type: result.status === 'started' ? 'info' : 'warning', message: `${prefix}${result.summary || '请查看实际状态。'}`});
    } catch (_) {
      if (!mounted.current || !latest.current.allowed) return;
      setActionResult({type: 'warning', message: '启动结果待核实。请查看最新检查；如仍无法确认，请通过 SSH 诊断，不要重复发送启动。'});
    } finally {
      if (mounted.current && latest.current.allowed) {
        await refresh();
        if (mounted.current) setStarting(null);
      }
      startPending.current = false;
    }
  }

  function confirmStart(check) {
    if (readPending.current || startPending.current || error || !canRecover(check)) return;
    const confirmedRevision = revision.current;
    Modal.confirm({
      title: `确认启动 ${check.name}？`,
      content: `将启动本平台已停止的 ${check.service}。启动可能恢复后台调度、队列处理或对外访问，请确认配置与待处理任务。操作后将重新检查实际状态。`,
      okText: '确认启动', cancelText: '取消',
      onOk: () => startService(check.id, check.service, confirmedRevision),
    });
  }

  function downloadReport() {
    if (!snapshot || readPending.current || startPending.current || !hasPermission('admin')) return;
    const report = {...snapshot, report_exported_at: new Date().toISOString(), snapshot_stale: error,
      query_error: error ? '最近一次健康检查失败，当前内容为上次快照。' : null};
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: 'application/json;charset=utf-8'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = `platform-health-${(snapshot.checked_at || 'unknown').replace(/[:.]/g, '-')}.json`;
    document.body.appendChild(link);
    try {
      link.click();
    } finally {
      link.remove();
      URL.revokeObjectURL(url);
    }
  }

  if (!allowed) return <Alert type="warning" showIcon message="仅超级管理员可访问平台健康。"/>;

  const checks = Array.isArray(snapshot?.checks) ? snapshot.checks : [];
  const busy = loading || !!starting;
  const status = busy || error ? 'unknown' : (statuses[snapshot?.status] ? snapshot.status : 'unknown');
  const statusLabel = starting ? '启动后待核实' : loading ? '检查中' : error ? '无法确认' : snapshot ? statuses[status].label : '待检查';
  const attentionCount = checks.filter(check => check.status !== 'ok').length;
  const showRecovery = !error && !busy;

  return <div className={styles.page}>
    <Breadcrumb>
      <Breadcrumb.Item>首页</Breadcrumb.Item>
      <Breadcrumb.Item>系统管理</Breadcrumb.Item>
      <Breadcrumb.Item>平台健康</Breadcrumb.Item>
    </Breadcrumb>
    <div className={styles.toolbar}>
      <div><h2>平台健康</h2><p>查看本平台的检查快照，按需手动更新。</p></div>
      <div className={styles.actions}>
        <Button disabled={!snapshot || busy} onClick={downloadReport}>下载报告</Button>
        <Button type="primary" loading={loading} disabled={!!starting} onClick={() => {
          if (!startPending.current) refresh();
        }}>立即检查</Button>
      </div>
    </div>
    <div className={styles.summaryGrid}>
      <Card title="平台状态">
        <div data-health-overview data-status={status} className={styles.summaryValue}>
          <Tag color={statuses[status].color}>{statusLabel}</Tag>
        </div>
        <span className={styles.muted}>以最近一次成功检查为准</span>
      </Card>
      <Card title="需要关注">
        <div className={styles.summaryValue}>{busy || error || !snapshot ? '待检查' : `${attentionCount} / ${checks.length}`}</div>
        <span className={styles.muted}>异常、需关注及待核实的检查项</span>
      </Card>
      <Card title="检查时间">
        <div className={styles.checkedTime}><time dateTime={snapshot?.checked_at} title={snapshot?.checked_at}>
          {formatTime(snapshot?.checked_at)}
        </time></div>
        <span className={styles.muted}>本地时间 · 手动检查，无自动轮询</span>
      </Card>
    </div>
    {error && <div className={styles.notice}><Alert showIcon type="error" message="健康检查失败，无法确认当前状态。"
      description={snapshot ? '上次结果已过期，仅供排查参考；请重新检查后再执行恢复。' : '尚未取得检查结果，请点击“立即检查”重试。'}/></div>}
    {loading && snapshot && <div className={styles.notice}><Alert type="info" showIcon message="正在重新检查，表格暂时展示上次快照。"/></div>}
    {actionResult && <div className={styles.notice}><Alert showIcon {...actionResult}/></div>}
    <div className={styles.notice}><Alert type="warning" showIcon message="恢复前请确认影响"
      description="启动可能恢复后台调度、队列处理或对外访问。确认当前配置及待处理任务后再操作，启动后仍需核对实际状态。"/></div>
    <Card title={error && snapshot ? '检查明细 · 上次结果已过期' : '检查明细'}>
      {checks.length ? <div className={styles.tableScroll} data-health-stale={error}>
        <table className={styles.table}>
          <thead><tr><th scope="col">检查项</th><th scope="col">状态</th><th scope="col">检查结果</th><th scope="col">处理建议</th><th scope="col">恢复操作</th></tr></thead>
          <tbody>{checks.map(check => <tr key={check.id}>
            <th scope="row"><span>{check.name}</span>{check.service && <code className={styles.service}>{check.service}</code>}</th>
            <td><Tag color={(statuses[check.status] || statuses.unknown).color}>{(statuses[check.status] || statuses.unknown).label}</Tag>
              {check.process_state && <span className={styles.processState}>{check.process_state}</span>}</td>
            <td>{check.summary || '—'}</td><td>{check.advice || '—'}</td>
            <td>{showRecovery && canRecover(check) ? <Button size="small" onClick={() => confirmStart(check)}>启动服务</Button> : '—'}</td>
          </tr>)}</tbody>
        </table>
      </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={loading ? '正在读取检查结果…' : error ? '暂无可用检查结果' : '暂无检查项'}/>}
    </Card>
    {Array.isArray(snapshot?.limitations) && snapshot.limitations.length > 0 && <div className={styles.limitations}>
      <strong>检查范围与限制</strong>
      <ul>{snapshot.limitations.map((text, index) => <li key={index}>{text}</li>)}</ul>
    </div>}
  </div>;
}
