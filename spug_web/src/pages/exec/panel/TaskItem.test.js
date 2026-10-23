import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import TaskItem from './TaskItem';

jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  return {Space: Wrapper, Typography: {Text: Wrapper, Paragraph: Wrapper}, Tag: Wrapper,
    Alert: ({message}) => <div>{message}</div>, Descriptions: Object.assign(Wrapper, {Item: Wrapper})};
});
let container;
beforeEach(() => {container = document.createElement('div'); document.body.appendChild(container);});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove();});

test.each([['individual', '单独设置'], ['common', '统一设置']])('upload frozen preview shows directory, %s source and overwrite counts with details closed', (source, label) => {
  act(() => {ReactDOM.render(<TaskItem operation="file_upload" child={{state: 'ready', snapshot: {label: '上传文件 / 目录',
    target: {target_dir: '/www/frozen', directory_source: source}, before: {new_files: 5, overwrite_files: 2},
    plan: {file_count: 7, total_bytes: 21, manifest_sha256: 'technical-hash'}}}}/>, container);});
  expect(container.textContent).toContain('最终目录：/www/frozen');
  expect(container.textContent).toContain(`目录来源：${label}`);
  expect(container.textContent).toContain('预计新增：5 个'); expect(container.textContent).toContain('预计覆盖：2 个');
  expect(container.querySelector('details').open).toBe(false);
  expect(container.textContent).not.toContain('technical-hash');
});
