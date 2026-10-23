import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { message } from 'antd';
import { http } from 'libs';
import PanelForm from './PanelForm';

jest.mock('libs', () => ({http: {post: jest.fn()}}));

let container;
const oldRecord = {id: 101, name: 'Test panel', group_id: 1, username: 'admin',
  url: 'https://panel.example:8888/security-entry', api_base_url: '', has_api_key: false};

beforeAll(() => {
  window.matchMedia = window.matchMedia || (() => ({matches: false, addListener() {}, removeListener() {}}));
});

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  http.post.mockResolvedValue({});
  jest.spyOn(message, 'success').mockImplementation(() => {});
});

afterEach(() => {
  act(() => { ReactDOM.unmountComponentAtNode(container); });
  container.remove();
  jest.restoreAllMocks();
  jest.clearAllMocks();
});

function render(record = {}) {
  act(() => { ReactDOM.render(<PanelForm record={record} groups={[{id: 1, name: 'Test'}]}
    selectedGroup={1} onSaved={jest.fn()} onCancel={jest.fn()}/>, container); });
}

function change(id, value) {
  const input = document.getElementById(id);
  act(() => Simulate.change(input, {target: {value}}));
}

test('new records derive the API address as the login address changes without making requests', () => {
  render();
  change('url', 'https://one.example:8888/entry?login=1');
  expect(document.getElementById('api_base_url').value).toBe('https://one.example:8888');
  change('url', 'http://two.example:9999/other');
  expect(document.getElementById('api_base_url').value).toBe('http://two.example:9999');
  expect(http.post).not.toHaveBeenCalled();
});

test('manual proxy addresses survive later login-address edits', () => {
  render();
  change('url', 'https://one.example:8888/entry');
  change('api_base_url', 'https://proxy.example/customer-a');
  change('url', 'https://two.example:8888/entry');
  expect(document.getElementById('api_base_url').value).toBe('https://proxy.example/customer-a');
});

test('existing explicitly saved API configuration is preserved', () => {
  render({...oldRecord, api_base_url: 'https://proxy.example/customer-a', has_api_key: true});
  change('url', 'https://two.example:8888/entry');
  expect(document.getElementById('api_base_url').value).toBe('https://proxy.example/customer-a');
});

test('legacy unconfigured edits show the inferred address but keep API disabled when no key is supplied', async () => {
  render(oldRecord);
  expect(document.getElementById('api_base_url').value).toBe('https://panel.example:8888');
  const save = [...document.querySelectorAll('.ant-modal-footer button')].find(button => button.textContent.includes('保'));
  await act(async () => { Simulate.click(save); });
  expect(http.post).toHaveBeenCalledWith('/api/baota/', expect.objectContaining({id: 101, api_base_url: ''}));
});

test('setting only the API key submits the inferred login origin', async () => {
  render(oldRecord);
  change('api_key', 'Disposable-test-only-key');
  const save = [...document.querySelectorAll('.ant-modal-footer button')].find(button => button.textContent.includes('保'));
  await act(async () => { Simulate.click(save); });
  expect(http.post).toHaveBeenCalledWith('/api/baota/', expect.objectContaining({
    id: 101, api_base_url: 'https://panel.example:8888', api_key: 'Disposable-test-only-key',
  }));
});
