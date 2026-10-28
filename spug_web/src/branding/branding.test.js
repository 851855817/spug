import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import Brand from './Brand';
import { LegalLink, LegalNotice } from './LegalNotice';
import { sourceLinks } from './source';

jest.mock('antd', () => ({Modal: ({visible, children, onCancel}) => visible ?
  <div role="dialog">{children}<button onClick={onCancel}>关闭</button></div> : null}));

let container;
beforeEach(() => {container = document.createElement('div'); document.body.appendChild(container);});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove();});

test('the compact brand keeps its accessible name without overflowing the collapsed sidebar', () => {
  act(() => {ReactDOM.render(<Brand compact/>, container);});
  expect(container.querySelector('[aria-label="KOps · 云枢"]')).not.toBeNull();
  expect(container.textContent).toBe('');
  act(() => {ReactDOM.render(<Brand/>, container);});
  expect(container.textContent).toBe('KOps · 云枢');
});

test('legal and source notices are usable without authentication, permission checks or API calls', () => {
  localStorage.clear();
  act(() => {ReactDOM.render(<LegalNotice/>, container);});
  expect(container.textContent).toContain('KOps · 云枢');
  expect(container.textContent).not.toMatch(/spug/i);
  act(() => {container.querySelector('button').click();});
  const dialog = container.querySelector('[role="dialog"]');
  expect(dialog.textContent).toContain('基于 Spug 二次开发');
  expect(dialog.textContent).toContain('OpenSpug');
  expect(dialog.textContent).toContain('AGPL');
  expect(dialog.textContent).toContain('不提供任何担保');
  expect(dialog.textContent).toContain('再分发');
  expect(dialog.textContent).toContain('2026-10-28');
  expect(dialog.querySelector('a[href$="/kops-20261027/LICENSE.txt"]')).not.toBeNull();
  act(() => {dialog.querySelector('button').click();});
  expect(container.querySelector('[role="dialog"]')).toBeNull();
  act(() => {ReactDOM.render(<LegalLink/>, container);});
  act(() => {container.querySelector('button').click();});
  expect(container.querySelector('[role="dialog"]')).not.toBeNull();
});

test('corresponding source links pin the full released commit and never accept an arbitrary URL', () => {
  const sha = '0123456789abcdef0123456789abcdef01234567';
  expect(sourceLinks(sha)).toEqual({revision: sha,
    tree: `https://github.com/851855817/spug/tree/${sha}`,
    archive: `https://github.com/851855817/spug/archive/${sha}.tar.gz`});
  for (const value of [undefined, '', 'main', 'abcdef0', 'https://example.com', '<script>', 'a'.repeat(41)]) {
    expect(sourceLinks(value)).toBeNull();
  }
});
