import { panelBlocker } from './helpers';

export const DEFAULT_UPLOAD_LIMITS = {files: 1000, file_bytes: 20 * 1024 * 1024, bundle_bytes: 200 * 1024 * 1024};

export function uploadLimits(value = {}) {
  const limits = {...DEFAULT_UPLOAD_LIMITS};
  Object.keys(limits).forEach(key => {
    if (Number.isSafeInteger(value[key]) && value[key] > 0) limits[key] = value[key];
  });
  return limits;
}

function pathBytes(path) {return new Blob([path]).size;}

function directoryText(value) {
  const text = String(value || '');
  // Check before trim: JavaScript treats the format control U+FEFF as whitespace.
  if (/[\p{Cf}\p{Cs}]/u.test(text)) throw new Error('目标目录包含无效字符');
  return text.trim();
}

function validatePath(path, absolute, label) {
  const segments = path.split('/').slice(absolute ? 1 : 0);
  const hasControl = /[\p{Cc}\p{Cf}\p{Cs}]/u.test(path);
  if (!path || path.startsWith('/') !== absolute || hasControl || /[\\&|~]/.test(path) ||
      pathBytes(path) > 1024 || segments.some(part => !part || part === '.' || part === '..' || pathBytes(part) > 255)) {
    throw new Error(`${label}无效：请使用安全的${absolute ? '非根绝对目录' : '相对路径'}，每段最多 255 字节、全路径最多 1024 字节`);
  }
  return path;
}

export function normalizeUploadDirectory(value) {
  return validatePath(directoryText(value).replace(/\/$/, ''), true, '目标目录');
}

export function collectUploadFiles(fileList, configuredLimits) {
  const limits = uploadLimits(configuredLimits);
  const files = Array.from(fileList || []);
  if (!files.length) throw new Error('请选择文件或目录');
  if (files.length > limits.files) throw new Error(`文件数量最多 ${limits.files} 个`);
  const paths = new Set();
  let total = 0;
  const selected = files.map(file => {
    const path = validatePath(file.webkitRelativePath || file.name, false, '本地文件路径');
    if (path.split('/').some(part => /[:<>"?*]/.test(part) || /[ .]$/.test(part) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$/i.test(part))) {
      throw new Error(`本地文件路径包含不可上传的名称：${path}`);
    }
    if (paths.has(path)) throw new Error(`本地文件路径重复：${path}`);
    paths.add(path);
    if (!Number.isSafeInteger(file.size) || file.size < 0 || file.size > limits.file_bytes) {
      throw new Error(`单个文件最多 ${formatBytes(limits.file_bytes)}：${path}`);
    }
    total += file.size;
    return {file, path, size: file.size};
  });
  selected.forEach(({path}) => {
    const parts = path.split('/');
    for (let index = 1; index < parts.length; index++) {
      if (paths.has(parts.slice(0, index).join('/'))) throw new Error(`文件与目录路径冲突：${path}`);
    }
  });
  if (total > limits.bundle_bytes) throw new Error(`文件总大小最多 ${formatBytes(limits.bundle_bytes)}`);
  return selected;
}

export function resolveUploadDirectory(panel, common, overrides) {
  const individual = directoryText(overrides[panel.id]);
  return {path: normalizeUploadDirectory(individual || common), source: individual ? '单独目录' : '公共目录'};
}

export function buildUploadRequest(bundleId, panels, common, overrides, files = []) {
  if (!panels.length || panels.length > 50) throw new Error('请选择 1 至 50 个面板');
  const commonText = directoryText(common);
  const target_dir = commonText ? normalizeUploadDirectory(commonText) : '';
  const params_by_panel = {};
  panels.forEach(panel => {
    const blocked = panelBlocker(panel);
    if (blocked) throw new Error(`${panel.name}：${blocked}`);
    const {path} = resolveUploadDirectory(panel, target_dir, overrides);
    files.forEach(file => validatePath(`${path}/${file.path}`, true, `面板 ${panel.name} 最终目标路径`));
    if (directoryText(overrides[panel.id])) params_by_panel[String(panel.id)] = {target_dir: path};
  });
  return {operation: 'file_upload', panel_ids: panels.map(panel => panel.id), params: {bundle_id: bundleId, target_dir}, params_by_panel};
}

export function formatBytes(bytes) {
  const number = Math.max(0, Number(bytes) || 0);
  if (number < 1024) return `${number} B`;
  const unit = number >= 1024 * 1024 ? 'MiB' : 'KiB';
  const value = number / (unit === 'MiB' ? 1024 * 1024 : 1024);
  return `${Number(value.toFixed(1))} ${unit}`;
}
