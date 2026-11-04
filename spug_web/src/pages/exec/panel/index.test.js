import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { Router, Route } from 'react-router-dom';
import { createMemoryHistory } from 'history';
import { hasPermission, http } from 'libs';
import PanelTasks, { LegacyOperations } from './index';

jest.mock('libs', () => ({hasPermission: jest.fn(), http: {get: jest.fn(), post: jest.fn()},
  history: {replace: jest.fn(), push: jest.fn()}}));
jest.mock('components', () => {
  const Wrapper = ({children}) => <div>{children}</div>;
  return {Breadcrumb: Object.assign(Wrapper, {Item: Wrapper}), AuthDiv: Wrapper};
});
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children, title}) => <div>{title}{children}</div>;
  return {Card: Wrapper, Space: Wrapper, Spin: Wrapper, Empty: Wrapper,
    Alert: ({message}) => <p>{message}</p>, Typography: {Paragraph: Wrapper, Text: Wrapper},
    Button: ({children, onClick}) => <button onClick={onClick}>{children}</button>,
    Select: ({value, options, onChange, 'aria-label': label}) => <select aria-label={label} value={value} onChange={event => onChange(event.target.value)}>
      {options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>,
    Tabs: Object.assign(({children, activeKey, onChange}) => <div>
      {React.Children.toArray(children).map(child => <button key={child.key} onClick={() => onChange(child.key)}>{child.props.tab}</button>)}
      {React.Children.toArray(children).find(child => child.key.replace(/^\.\$/, '') === activeKey)}
    </div>, {TabPane: Wrapper})};
});
jest.mock('./TaskForm', () => ({initialSearch}) => <div data-testid="panel-form">{initialSearch}</div>);
jest.mock('./TaskDetail', () => () => <div>宝塔结果</div>);
jest.mock('./Records', () => () => <div>宝塔执行记录</div>);
jest.mock('../operations/RunForm', () => () => <div>主机巡检表单</div>);
jest.mock('../operations/Records', () => () => <div>巡检执行记录</div>);
jest.mock('../operations/RunDetail', () => ({runId}) => <div>巡检结果 #{runId}</div>);

let container, navigation;
beforeEach(() => {
  container = document.createElement('div'); document.body.appendChild(container);
  hasPermission.mockReturnValue(true);
  http.get.mockImplementation(url => Promise.resolve(url === '/api/ops/catalog/' ? {jobs: []} : {panels: []}));
  http.post.mockClear();
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.clearAllMocks();});
async function render(path) {
  navigation = createMemoryHistory({initialEntries: [path]});
  await act(async () => ReactDOM.render(<Router history={navigation}>
    <Route exact path="/exec/panel" component={PanelTasks}/><Route exact path="/exec/operations" component={LegacyOperations}/>
  </Router>, container));
}
async function choose(value) {
  await act(async () => {const select=container.querySelector('select'); select.value=value; Simulate.change(select);});
}
test('panel entry includes host inspections and switches without submitting any task', async () => {
  await render('/exec/panel');
  expect(container.textContent).toContain('宝塔操作');
  expect(container.textContent).toContain('主机巡检');
  expect(http.get).toHaveBeenCalledWith('/api/baota/task-options/');
  expect(http.get).not.toHaveBeenCalledWith('/api/ops/catalog/');
  await choose('inspection');
  expect(navigation.location.pathname).toBe('/exec/panel');
  expect(navigation.location.search).toBe('?type=inspection');
  expect(container.textContent).toContain('主机巡检表单');
  expect(http.get).toHaveBeenCalledWith('/api/ops/catalog/');
  expect(http.post).not.toHaveBeenCalled();
});
test('panel-only role never loads privileged host inspection data even with a crafted URL', async () => {
  hasPermission.mockImplementation(code => code === 'exec.panel.view');
  await render('/exec/panel?type=inspection&run_id=7');
  expect(container.textContent).not.toContain('主机巡检');
  expect(http.get).not.toHaveBeenCalledWith('/api/ops/catalog/');
  expect(container.textContent).toContain('宝塔执行记录');
  expect(http.post).not.toHaveBeenCalled();
});
test('unauthorized visitors do not fetch either API', async () => {
  hasPermission.mockReturnValue(false);
  await render('/exec/panel');
  expect(http.get).not.toHaveBeenCalled();
  expect(http.post).not.toHaveBeenCalled();
});

test('inspection-only authorization selects inspections without loading panel data', async () => {
  hasPermission.mockImplementation(code => code === 'admin');
  await render('/exec/panel');
  expect(container.querySelector('select').value).toBe('inspection');
  expect(http.get).toHaveBeenCalledWith('/api/ops/catalog/');
  expect(http.get).not.toHaveBeenCalledWith('/api/baota/task-options/');
  expect(http.post).not.toHaveBeenCalled();
});
test('SSL site prefill survives the combined panel entry', async () => {
  const search='?panel_id=3&site_id=7&operation=ssl_renew';
  await render('/exec/panel'+search);
  expect(container.querySelector('[data-testid="panel-form"]').textContent).toBe(search);
  expect(http.post).not.toHaveBeenCalled();
});
test('legacy operations link redirects while preserving result ID and hash', async () => {
  await render('/exec/operations?run_id=7#results');
  expect(navigation.location.pathname).toBe('/exec/panel');
  expect(new URLSearchParams(navigation.location.search).get('type')).toBe('inspection');
  expect(new URLSearchParams(navigation.location.search).get('run_id')).toBe('7');
  expect(navigation.location.hash).toBe('#results');
  expect(container.textContent).toContain('巡检结果 #7');
  expect(http.get).toHaveBeenCalledWith('/api/ops/catalog/');
  expect(http.post).not.toHaveBeenCalled();
});
test('back and forward navigation restore the selected task type', async () => {
  await render('/exec/panel'); await choose('inspection');
  await act(async () => navigation.goBack());
  expect(container.querySelector('select').value).toBe('panel');
  await act(async () => navigation.goForward());
  expect(container.querySelector('select').value).toBe('inspection');
});

test('history navigation between saved results restores each inspection ID', async () => {
  await render('/exec/panel?type=inspection&run_id=7');
  await act(async () => navigation.push('/exec/panel?type=inspection&run_id=8'));
  expect(container.textContent).toContain('巡检结果 #8');
  await act(async () => navigation.goBack());
  expect(container.textContent).toContain('巡检结果 #7');
  expect(http.post).not.toHaveBeenCalled();
});
