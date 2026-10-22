import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { http } from 'libs';
import { readResource, useResources } from './resources';

jest.mock('libs', () => ({http: {get: jest.fn()}}));

test('resource reads from different panels never exceed two concurrent requests', async () => {
  const pending = [];
  let running = 0;
  let maximum = 0;
  http.get.mockImplementation(() => {
    running++; maximum = Math.max(maximum, running);
    return new Promise(resolve => pending.push(value => {running--; resolve(value);}));
  });
  const reads = [1, 2, 3, 4].map(panel_id => readResource({panel_id, kind: 'sites'}));
  await Promise.resolve();
  expect(http.get).toHaveBeenCalledTimes(2);
  for (let i = 0; i < 4; i++) {
    pending[i]({sites: []});
    await reads[i];
    await new Promise(resolve => setTimeout(resolve, 0));
  }
  await Promise.all(reads);
  expect(maximum).toBe(2);
  http.get.mockReset();
});

test('changing panel or PHP branch discards stale responses', async () => {
  const pending = [];
  http.get.mockImplementation(() => new Promise(resolve => pending.push(resolve)));
  const container = document.createElement('div');
  const observed = jest.fn();
  function Reader({panelId, branch}) {
    const result = useResources(panelId, [{kind: 'php_extensions', php_version: branch}], true, observed);
    return <div>{JSON.stringify(result.data)}</div>;
  }
  await act(async () => {ReactDOM.render(<Reader panelId={1} branch="74"/>, container);});
  await act(async () => {ReactDOM.render(<Reader panelId={2} branch="82"/>, container);});
  await act(async () => {pending[0]({php_extensions: [{name: 'old'}]});});
  expect(observed).not.toHaveBeenCalled();
  await act(async () => {pending[1]({php_extensions: [{name: 'new'}]});});
  expect(container.textContent).toContain('new');
  expect(container.textContent).not.toContain('old');
  act(() => {ReactDOM.unmountComponentAtNode(container);});
});
