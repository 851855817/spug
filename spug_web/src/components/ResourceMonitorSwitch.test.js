import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { http } from 'libs';
import ResourceMonitorSwitch, { ResourceMonitorProvider } from './ResourceMonitorSwitch';

jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}}));
jest.mock('antd', () => ({Switch: ({checked, disabled, loading, onChange, ...props}) =>
  <button role="switch" aria-label={props['aria-label']} aria-checked={checked} disabled={disabled || loading}
    onClick={() => onChange(!checked)}>{checked ? '开' : '关'}</button>}));

let container;
const render = async (revision = 0, canEdit = true) => {
  await act(async () => ReactDOM.render(<ResourceMonitorProvider kind="host" revision={revision}>
    <ResourceMonitorSwitch id={1} canEdit={canEdit}/><ResourceMonitorSwitch id={2} canEdit={canEdit}/>
  </ResourceMonitorProvider>, container));
};
beforeEach(() => {
  container = document.createElement('div'); document.body.appendChild(container);
  http.get.mockResolvedValue({sources: [{id: 1, kind: 'host', enabled: false}, {id: 2, kind: 'host', enabled: true}], hosts: []});
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.resetAllMocks();});

test('one list read serves all row switches and only the saved server result changes the value', async () => {
  let complete;
  http.post.mockReturnValue(new Promise(resolve => {complete = resolve;}));
  await render();
  expect(http.get).toHaveBeenCalledTimes(1);
  expect(http.get).toHaveBeenCalledWith('/api/baota/monitor/sources/', {params: {kind: 'host'}});
  const switches = container.querySelectorAll('[role=switch]');
  act(() => switches[0].click());
  expect(http.post).toHaveBeenCalledWith('/api/baota/monitor/settings/', {kind: 'host', id: 1, enabled: true});
  expect(switches[0].getAttribute('aria-checked')).toBe('false');
  await act(async () => complete({id: 1, kind: 'host', enabled: true}));
  expect(switches[0].getAttribute('aria-checked')).toBe('true');
  await render(1);
  expect(switches[0].getAttribute('aria-checked')).toBe('false');
  expect(http.get).toHaveBeenCalledTimes(2);
});

test('server rejection keeps the old value, exposes failure and readonly switches cannot write', async () => {
  http.post.mockRejectedValue('forbidden');
  await render();
  await act(async () => container.querySelector('[role=switch]').click());
  expect(container.querySelector('[role=switch]').getAttribute('aria-checked')).toBe('false');
  expect(container.textContent).toContain('保存失败');
  await render(0, false);
  expect([...container.querySelectorAll('[role=switch]')].every(node => node.disabled)).toBe(true);
  expect(http.post).toHaveBeenCalledTimes(1);
});

test('a list read started before saving cannot overwrite the persisted switch', async () => {
  await render();
  let oldRead;
  http.get.mockReturnValue(new Promise(resolve => {oldRead = resolve;}));
  await render(1);
  http.post.mockResolvedValue({id: 1, kind: 'host', enabled: true});
  await act(async () => container.querySelector('[role=switch]').click());
  await act(async () => oldRead({sources: [{id: 1, enabled: false}], hosts: []}));
  expect(container.querySelector('[role=switch]').getAttribute('aria-checked')).toBe('true');
});
