import { collectUploadFiles, buildUploadRequest, resolveUploadDirectory, formatBytes } from './uploadHelpers';
import { summarizeTask, OPERATION_LABELS } from './helpers';

const panels = [1, 2].map(id => ({id, name: `Panel ${id}`, api_configured: true, api_checked: true, api_version: '13.1.0'}));
const file = (name, path = '', size = 3) => ({name, webkitRelativePath: path, size});

test('directory selection retains its root and ordinary files use basenames', () => {
  expect(collectUploadFiles([file('a.txt', 'site/assets/a.txt'), file('b.txt')]).map(item => item.path))
    .toEqual(['site/assets/a.txt', 'b.txt']);
});

test('individual destinations override common paths without mutating them and blank restores common', () => {
  const values = {1: '/www/a'};
  expect(resolveUploadDirectory(panels[0], '/www/common', values)).toEqual({path: '/www/a', source: '单独目录'});
  expect(buildUploadRequest(8, panels, '/www/common/', values)).toEqual({operation: 'file_upload', panel_ids: [1, 2],
    params: {bundle_id: 8, target_dir: '/www/common'}, params_by_panel: {'1': {target_dir: '/www/a'}}});
  expect(resolveUploadDirectory(panels[0], '/www/changed', values).path).toBe('/www/a');
  expect(resolveUploadDirectory(panels[0], '/www/changed', {1: ''})).toEqual({path: '/www/changed', source: '公共目录'});
});

test('common directory may be empty when every panel has its own valid directory', () => {
  expect(buildUploadRequest(9, panels, '', {1: '/www/a', 2: '/www/b'}).params).toEqual({bundle_id: 9, target_dir: ''});
  expect(() => buildUploadRequest(9, panels, '', {1: '/www/a'})).toThrow(/目录/);
});

test('an invalid individual path never falls back to a valid common directory', () => {
  expect(() => buildUploadRequest(9, panels, '/www/common', {1: '/www/../bad'})).toThrow(/目录/);
  expect(() => buildUploadRequest(9, [{...panels[0], api_configured: false}], '/www/a', {})).toThrow(/API/);
});

test.each(['../a', '/a', 'a/../b', 'a\\b', 'a&b', 'a|b', '~a', 'a\u0001', 'a\u0085', 'a//b', 'a/./b', 'a:stream', 'a?.txt', 'con.txt', 'a.', 'a '])('rejects unsafe local path %s', path => {
  expect(() => collectUploadFiles([file('a', path)])).toThrow(/路径/);
});

test.each(['\u200b', '\u202e', '\ufeff', '\u00ad', '\ud800', '\udc00', '\u{e0001}'])('rejects Unicode format controls and isolated surrogates %j in source and destination paths', control => {
  expect(() => collectUploadFiles([file(`文${control}件.txt`)])).toThrow(/路径/);
  expect(() => buildUploadRequest(9, panels, `/www/目${control}录`, {})).toThrow(/目录/);
  expect(() => buildUploadRequest(9, panels, '/www/common', {1: `${control}/www/override`})).toThrow(/目录/);
  expect(() => buildUploadRequest(9, panels, `/www/common${control}`, {1: '/www/a', 2: '/www/b'})).toThrow(/目录/);
});

test('ordinary Chinese and emoji remain valid in source and destination paths', () => {
  const files = collectUploadFiles([file('文件😀.txt', '目录😀/文件😀.txt')]);
  expect(files[0].path).toBe('目录😀/文件😀.txt');
  expect(buildUploadRequest(9, panels, '/www/目录😀', {}, files).params.target_dir).toBe('/www/目录😀');
});

test('source duplicates, file-directory collisions, UTF-8 lengths and configured quotas are enforced', () => {
  expect(() => collectUploadFiles([file('a'), file('a')])).toThrow(/重复/);
  expect(() => collectUploadFiles([file('a'), file('b', 'a/b')])).toThrow(/冲突/);
  expect(() => collectUploadFiles([file('x', '中'.repeat(86))])).toThrow(/路径/);
  expect(() => collectUploadFiles([file('a', '', 21 * 1024 * 1024)])).toThrow(/单个/);
  expect(() => collectUploadFiles([file('a'), file('b')], {files: 1})).toThrow(/数量/);
  expect(() => collectUploadFiles([file('a', '', 4)], {file_bytes: 3})).toThrow(/单个/);
  expect(() => collectUploadFiles([file('a'), file('b')], {bundle_bytes: 5})).toThrow(/总大小/);
  expect(() => buildUploadRequest(9, panels, `/${'x'.repeat(250)}/${'y'.repeat(250)}/${'z'.repeat(250)}/${'w'.repeat(250)}/${'a'.repeat(30)}`, {})).toThrow(/目录/);
});

test('upload task summaries count files instead of aggregate child rows', () => {
  expect(OPERATION_LABELS.file_upload).toBe('上传文件 / 目录');
  const counts = summarizeTask({operation: 'file_upload', state: 'prepared', targets: [{children: [{state: 'ready'}],
    summary: {upload: {files: 10, pending: 4, success: 3, failed: 1, uncertain: 2, cancelled: 0}}}]});
  expect(counts).toMatchObject({ready: 4, success: 3, failed: 1, uncertain: 2});
  expect(formatBytes(1024)).toBe('1 KiB');
});

test('running upload pending counts stay pending and combined destination byte limits are checked', () => {
  expect(summarizeTask({operation: 'file_upload', state: 'running', targets: [{summary: {upload: {files: 5, pending: 4, success: 1}}}]}))
    .toMatchObject({ready: 0, pending: 4, success: 1});
  const directory = `/${'x'.repeat(250)}/${'y'.repeat(250)}/${'z'.repeat(250)}/${'w'.repeat(250)}`;
  expect(() => buildUploadRequest(9, panels, directory, {}, [{path: 'a'.repeat(30)}])).toThrow(/路径/);
});
