import React, { useRef, useState } from 'react';
import { Alert, Button, Form, Input, Progress, Space, Typography } from 'antd';
import { http } from 'libs';
import { panelBlocker } from './helpers';
import { buildUploadRequest, collectUploadFiles, formatBytes, resolveUploadDirectory, uploadLimits } from './uploadHelpers';

export default function UploadForm({panels, options = {}, onPrepared, onBusyChange}) {
  const [files, setFiles] = useState([]);
  const [common, setCommon] = useState('');
  const [overrides, setOverrides] = useState({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState({label: '', loaded: 0});
  const pending = useRef(false);
  const fileInput = useRef(null), directoryInput = useRef(null);
  const limits = uploadLimits(options.upload_limits);
  const total = files.reduce((sum, file) => sum + file.size, 0);
  const progressTotal = files.length ? total : progress.loaded;
  const blockers = panels.map(panel => ({panel, message: panelBlocker(panel)})).filter(item => item.message);

  function select(event) {
    if (pending.current) return;
    setError('');
    try {setFiles(collectUploadFiles(event.target.files, options.upload_limits));}
    catch (error) {setFiles([]); setError(String(error?.message || error));}
    // A fresh selection replaces the previous batch; permit choosing the same files again.
    if (fileInput.current) fileInput.current.value = '';
    if (directoryInput.current) directoryInput.current.value = '';
    setProgress({label: '', loaded: 0});
  }

  async function prepare() {
    if (pending.current) return;
    setError('');
    let request;
    try {
      const checked = collectUploadFiles(files.map(item => item.file), options.upload_limits);
      request = buildUploadRequest(null, panels, common, overrides, checked);
    } catch (error) {setError(String(error?.message || error)); return;}
    pending.current = true; setBusy(true); if (onBusyChange) onBusyChange(true);
    let bundleId, creatingTask = false;
    try {
      setProgress({label: '创建上传批次', loaded: 0});
      const bundle = await http.post('/api/baota/uploads/', {files: files.map(({path, size}) => ({path, size}))});
      bundleId = bundle.id;
      const remoteFiles = new Map((bundle.files || []).map(file => [file.path, file]));
      let completed = 0;
      for (let index = 0; index < files.length; index++) {
        const local = files[index], remote = remoteFiles.get(local.path);
        if (!remote || remote.id === undefined) throw new Error('上传批次文件清单不匹配，请重新选择');
        const label = `暂存文件 ${index + 1}/${files.length}：${local.path}`;
        const uploaded = completed;
        const data = new FormData(); data.append('file', local.file);
        setProgress({label, loaded: uploaded});
        await http.post(`/api/baota/uploads/${bundleId}/files/${remote.id}/`, data, {timeout: 300000,
          onUploadProgress: event => setProgress({label, loaded: uploaded + Math.min(local.size, Math.max(0, Number(event.loaded) || 0))})});
        completed += local.size;
        setProgress({label, loaded: completed});
      }
      setProgress({label: '文件暂存完成，正在封存', loaded: total});
      await http.post(`/api/baota/uploads/${bundleId}/seal/`, {});
      creatingTask = true;
      setProgress({label: '正在生成冻结预检', loaded: total});
      const result = await http.post('/api/baota/tasks/', {...request, params: {...request.params, bundle_id: bundleId}});
      setFiles([]); setProgress({label: '冻结预检已生成，请核对后确认开始', loaded: total});
      if (onPrepared) onPrepared(result.id);
    } catch (error) {
      if (bundleId && !creatingTask) {
        try {await http.post(`/api/baota/uploads/${bundleId}/discard/`, {});} catch (_) { /* Draft expiry also removes abandoned staging. */ }
      }
      setError(creatingTask ? '任务创建结果暂不明确，请先检查任务记录；确认无新任务后再重新上传。' : String(error?.message || error || '文件暂存失败，请重试'));
    } finally {pending.current = false; setBusy(false); if (onBusyChange) onBusyChange(false);}
  }

  return <div>
    {blockers.map(({panel, message}) => <Alert key={panel.id} showIcon type="error" message={`${panel.name}：${message}`} style={{marginBottom: 12}}/>)}
    <Alert showIcon type="info" message="选择文件或目录 → 暂存并生成预检 → 核对目录与覆盖范围 → 确认开始"
      description={`每次选择会替换本地文件清单。目录保留所选根目录名称，空目录不包含在上传中。最多 ${limits.files} 个文件，单个 ${formatBytes(limits.file_bytes)}，合计 ${formatBytes(limits.bundle_bytes)}。`}/>
    <Form layout="vertical" style={{marginTop: 16}}>
      <Form.Item label="选择本地文件或目录">
        <Space wrap><label>选择多个文件 <input ref={fileInput} type="file" multiple disabled={busy} aria-label="选择多个文件" onChange={select}/></label>
          <label>选择目录 <input ref={directoryInput} type="file" multiple webkitdirectory="" directory="" disabled={busy} aria-label="选择目录" onChange={select}/></label></Space>
      </Form.Item>
      <Typography.Paragraph>已选择 {files.length} 个文件，共 {formatBytes(total)}</Typography.Paragraph>
      {files.length > 0 && <details><summary>查看本地相对路径</summary>
        <ul>{files.slice(0, 5).map(file => <li key={file.path}>{file.path} · {formatBytes(file.size)}</li>)}</ul>
        {files.length > 5 && <Typography.Text type="secondary">另有 {files.length - 5} 个文件</Typography.Text>}
      </details>}
      <Form.Item label="公共目标目录（单独目录留空时使用）"><Input aria-label="公共目标目录" placeholder="/www/wwwroot/example.com" value={common} disabled={busy}
        onChange={event => {if (!pending.current) setCommon(event.target.value);}}/></Form.Item>
      {panels.map(panel => {
        let preview;
        try {const destination = resolveUploadDirectory(panel, common, overrides); preview = `${destination.path}（${destination.source}）`;}
        catch (error) {preview = String(error.message);}
        return <Form.Item key={panel.id} label={`${panel.name} · 单独目标目录（可选）`}>
          <Input aria-label={`${panel.name} 单独目标目录`} placeholder="留空使用公共目录" value={overrides[panel.id] || ''} disabled={busy}
            onChange={event => {const value = event.target.value; if (!pending.current) setOverrides(current => ({...current, [panel.id]: value}));}}/>
          <Typography.Text type="secondary">最终目录：{preview}</Typography.Text>
        </Form.Item>;
      })}
    </Form>
    {error && <Alert type="error" showIcon message={error} style={{marginBottom: 12}}/>}
    {progress.label && <div style={{marginBottom: 12}}><Typography.Paragraph>{progress.label}</Typography.Paragraph>
      <Progress percent={progressTotal ? Math.min(100, Math.round(progress.loaded * 100 / progressTotal)) : 0} status={busy ? 'active' : undefined}/>
      <Typography.Text>{formatBytes(progress.loaded)} / {formatBytes(progressTotal)}</Typography.Text></div>}
    <Button type="primary" loading={busy} disabled={!files.length || !panels.length || panels.length > 50 || blockers.length > 0} onClick={prepare}>上传并生成冻结预检</Button>
  </div>;
}
