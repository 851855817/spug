import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Modal } from 'antd';
import { hasPermission, http } from 'libs';
import { usePageVisibility } from '../server-monitor/useServerMonitor';
import { API, canStart, dateUTC8, isPollingJob, jobStates, text } from './helpers';
import Guidance from './Guidance';
import Status from './Status';
import PanelPreview from '../schedule/PanelPreview';
import styles from './index.module.less';

const planFields = {source: '来源', domain: '域名', domains: '影响域名', cert_path: '公开证书部署位置', engine: '续签客户端', validation: '验证方式', reload: '固定服务重载', impacts: '全部影响'};
export default function Renewal({jobId, onClose, onUpdate = () => {}}) {
  const [job, setJob] = useState(null), [error, setError] = useState(''), [accepted, setAccepted] = useState([]);
  const [revision, setRevision] = useState(0), [pending, setPending] = useState(false), [attempted, setAttempted] = useState(false);
  const [guidance, setGuidance] = useState(false);
  const visible = usePageVisibility(), mounted = useRef(false), locked = useRef(false), previous = useRef('');
  const updateRef = useRef(onUpdate); updateRef.current = onUpdate;
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  useEffect(() => {
    if (!hasPermission('admin') || !visible) return;
    let valid = true, timer;
    async function read() {
      try {
        const result = await http.get(`${API}jobs/${jobId}/`);
        if (!valid) return;
        if (previous.current !== result.revision) {setAccepted([]); previous.current = result.revision;}
        setJob(result); setError('');
        if (isPollingJob(result)) timer = window.setTimeout(read, 2500);
        else updateRef.current();
      } catch (_) {if (valid) setError('读取任务结果失败，请刷新本地结果。');}
    }
    read(); return () => {valid = false; window.clearTimeout(timer);};
  }, [jobId, visible, revision]);
  async function mutate(action) {
    if (locked.current || !hasPermission('admin') || !job || (action === 'start' && (attempted || !canStart(job, accepted)))) return;
    locked.current = true; setPending(true); setError('');
    if (action === 'start') setAttempted(true);
    try {
      await http.post(`${API}renew/${jobId}/${action}/`, action === 'start' ? {revision: job.revision,
        acknowledgements: job.acknowledgements.map(item => item.code).filter(code => accepted.includes(code))} : {});
      if (action === 'cancel' && mounted.current) {updateRef.current(); onClose(); return;}
      if (mounted.current) setRevision(value => value + 1);
    } catch (_) {
      if (mounted.current) {
        setError(action === 'start' ? '开始请求未得到确认。请刷新结果；执行状态不确定时仅做只读核验，不重复提交续签。' : action === 'cancel' ? '取消预检未得到确认，请刷新结果后核对。' : '核验请求未确认，请刷新结果后再查看。');
        updateRef.current();
      }
    } finally {locked.current = false; if (mounted.current) setPending(false);}
  }
  return <Modal visible width={760} title={job?.kind === 'check' ? '巡检任务结果' : 'SSL 续签预检与结果'} onCancel={onClose} footer={<>
    <Button onClick={onClose}>关闭</Button><Button disabled={pending} onClick={() => setRevision(value => value + 1)}>刷新结果</Button>
    {job?.state === 'prepared' && !attempted && <Button disabled={pending} onClick={() => mutate('cancel')}>放弃预检</Button>}
    {job?.state === 'uncertain' && <Button loading={pending} onClick={() => mutate('verify')}>只读核验结果</Button>}
    {job?.state === 'prepared' && <Button type="primary" loading={pending} disabled={attempted || !canStart(job, accepted)} onClick={() => mutate('start')}>确认并开始续签</Button>}
  </>}>
    {error && <Alert type="error" message={error}/>}
    {!job ? <p>正在读取本地任务…</p> : <>
      <p><Status state={job.state}>{jobStates[job.state] || '状态未知'}</Status> <span className={styles.secondaryInline}>任务 #{job.id}</span></p>
      <Alert showIcon type={job.state === 'uncertain' || job.state === 'blocked' ? 'warning' : 'info'}
        message={text(job.message || (job.state === 'prepared' ? '预检完成，请核对以下固定计划并逐项确认。' : '仅显示本地任务状态。'))}
        description={job.state === 'uncertain' ? '远程执行结果待核实，不自动重试。只读核验将重新检查部署结果。' : job.kind !== 'check' ? '预检和刷新结果不会执行续签；只有明确确认后才提交开始。' : undefined}/>
      <dl className={styles.facts}>{Object.entries(planFields).filter(([key]) => job.summary?.[key] !== undefined).map(([key, label]) =>
        <React.Fragment key={key}><dt>{label}</dt><dd>{Array.isArray(job.summary[key]) ? job.summary[key].map(text).join('、') : text(job.summary[key])}</dd></React.Fragment>)}</dl>
      {job.panel_task_id && <section><h3>宝塔面板原任务 #{job.panel_task_id}</h3><PanelPreview taskId={job.panel_task_id}/></section>}
      {job.state === 'prepared' && <div className={styles.acknowledgements}>{(job.acknowledgements || []).map(item => <Checkbox key={item.code} checked={accepted.includes(item.code)} disabled={pending || attempted}
        onChange={event => setAccepted(current => event.target.checked ? [...current, item.code] : current.filter(code => code !== item.code))}>{text(item.label)}</Checkbox>)}</div>}
      {job.state === 'blocked' && <p><Button onClick={() => setGuidance(true)}>查看客户端安装与配置指导</Button></p>}
      {job.result?.message && <Alert showIcon type={job.state === 'success' ? 'success' : 'warning'} message={text(job.result.message)}/>}
      {job.result?.local_updated !== undefined && <p>本地部署：{job.result.local_updated ? '已更新' : '尚未证实更新'} · 服务重载：{job.result.reload_verified ? '已核实' : '尚未核实'} · 公网证书：{job.result.public_updated ? '已生效' : '尚未生效'}</p>}
      {Array.isArray(job.result?.sources) && job.result.sources.map(source => <p key={source.source_key}>{text(source.source_key)} · {jobStates[source.state] || text(source.state)}
        {source.count !== undefined && ` · 发现 ${source.count} 条证书`}{source.coverage_incomplete && ' · 覆盖不完整'} · {text(source.message)}</p>)}
      {job.result?.local_certificate && <p>本地证书：{text(job.result.local_certificate.fingerprint)} · 到期 {dateUTC8(job.result.local_certificate.expires_at)}</p>}
      {job.result?.public_tls && <p>公网 TLS：{text(job.result.public_tls.message || job.result.public_tls.state)}</p>}
      <p className={styles.secondaryInline}>更新于 {dateUTC8(job.updated_at)}（UTC+8）</p>
    </>}
    {guidance && <Guidance onClose={() => setGuidance(false)}/>}
  </Modal>;
}
