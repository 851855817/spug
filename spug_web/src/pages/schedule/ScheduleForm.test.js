import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { hasPermission, http } from 'libs';
import store from './store';
import Step1 from './Step1';
import Step2 from './Step2';
import Step3 from './Step3';
import monitorStore from '../monitor/store';

jest.mock('mobx-react', () => ({observer: item => item}));
jest.mock('libs', () => ({http: {get: jest.fn(), post: jest.fn()}, t: value => value, hasPermission: jest.fn(),
  cleanCommand: value => value.trim(), docsUrl: value => value}));
jest.mock('./store', () => ({record: {}, trigger_args: {}, targets: [], types: [], page: 0, fetchRecords: jest.fn()}));
jest.mock('components', () => ({Container: ({children, visible}) => visible ? <div>{children}</div> : null,
  LinkButton: ({children, onClick}) => <button onClick={onClick}>{children}</button>,
  ACEditor: ({value, onChange}) => <textarea aria-label="命令" value={value} onChange={event => onChange(event.target.value)}/>}));
jest.mock('../exec/task/TemplateSelector', () => () => null);
jest.mock('./PanelPlanSelector', () => ({value, onChange}) => <div>宝塔模板 {value?.template_id}
  <button onClick={() => onChange({...value, acknowledgements: ['impact']})}>确认影响项</button></div>);
jest.mock('pages/host/Selector', () => () => <div>主机选择器</div>);
jest.mock('pages/host/store', () => ({initial: jest.fn(), rawRecords: []}));
jest.mock('../monitor/store', () => ({records: [1], cascaderOptions: [], fetchRecords: jest.fn()}));
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children}) => <div>{children}</div>;
  const form = {getFieldsValue: () => ({...require('./store').record}),
    setFieldsValue: values => Object.assign(require('./store').record, values), getFieldValue: key => require('./store').record[key]};
  const Form = ({children}) => <div>{children}</div>;
  Form.useForm = () => [form];
  Form.Item = ({children, label}) => <div>{label}{typeof children === 'function' ? children(form) : children}</div>;
  const Input = ({value, onChange, placeholder}) => <input placeholder={placeholder} value={value || ''} onChange={onChange} readOnly={!onChange}/>;
  Input.TextArea = Input;
  const Select = ({children}) => <select>{children}</select>;
  Select.Option = ({children, value}) => <option value={value}>{children}</option>;
  const Radio = {Group: ({children, onChange}) => <div>{React.Children.map(children, child => React.cloneElement(child, {onClick: () => onChange({target: {value: child.props.value}})}))}</div>,
    Button: ({children, onClick, disabled}) => <button disabled={disabled} onClick={onClick}>{children}</button>};
  return {Form, Input, Select, Radio, Space: Wrapper, Typography: {Text: Wrapper, Paragraph: Wrapper}, Checkbox: Wrapper,
    Modal: {confirm: jest.fn()}, Tabs: ({items}) => <div>{items.map(item => <div key={item.key}>{item.label}{item.children}</div>)}</div>,
    DatePicker: () => null, Cascader: () => null, InputNumber: ({min, value}) => <input type="number" min={min} value={value || ''} readOnly/>,
    Alert: ({message, description}) => <div>{message}{description}</div>,
    Button: ({children, disabled, loading, onClick}) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>,
    message: {success: jest.fn(), error: jest.fn()}};
});

const plan = {template_id: 7, template_token: 'signed', operation: 'site_basedir', operation_label: '防跨站',
  panels: [{id: 2, name: '客户面板'}], scope: [{panel_id: 2, name: '客户面板', items: ['example.com']}],
  required_acknowledgements: [{code: 'impact', label: '影响网站'}], acknowledgements: ['impact']};
let container;
const button = text => [...container.querySelectorAll('button')].find(item => item.textContent === text);
beforeEach(() => {
  container = document.createElement('div'); document.body.appendChild(container); hasPermission.mockReturnValue(true);
  store.record = {name: '自动任务', type: '运维', interpreter: 'baota', panel_plan: plan, rst_notify: {mode: '0'}};
  store.trigger = 'interval'; store.trigger_args = {interval: 300}; store.targets = ['local']; store.page = 0;
  http.post.mockResolvedValue({});
  monitorStore.records = [1]; monitorStore.fetchRecords.mockResolvedValue();
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.clearAllMocks();});

test('panel content offers a template instead of a command and blocks next until impacts are acknowledged', async () => {
  store.record.panel_plan = {...plan, acknowledgements: []};
  await act(async () => ReactDOM.render(<Step1 visible/>, container));
  expect(container.textContent).toContain('宝塔面板任务');
  expect(container.querySelector('textarea')).toBeNull();
  expect(button('下一步').disabled).toBe(true);
  await act(async () => button('确认影响项').click());
  await act(async () => button('下一步').click());
  expect(store.page).toBe(1);
  expect(store.record.panel_plan.acknowledgements).toEqual(['impact']);
});

test('panel triggers omit monitor alerts and enforce a minimum 300 second interval', async () => {
  store.trigger_args.interval = 299;
  await act(async () => ReactDOM.render(<Step2 visible/>, container));
  expect(container.textContent).not.toContain('监控告警');
  expect(container.querySelector('input[type=number]').min).toBe('300');
  expect(button('下一步').disabled).toBe(true);
  store.trigger_args.interval = 300;
  await act(async () => ReactDOM.render(<Step2 visible/>, container));
  expect(button('下一步').disabled).toBe(false);
});

test('saving a panel schedule submits the template reference and fixed scope without command or host targets', async () => {
  store.record.command = 'private'; store.record.panel_plan = {...plan, secret: 'private'};
  await act(async () => ReactDOM.render(<Step3 visible/>, container));
  expect(container.textContent).toContain('example.com');
  expect(container.textContent).not.toContain('主机选择器');
  expect(container.textContent).toContain('默认未激活');
  await act(async () => button('提交').click());
  const request = http.post.mock.calls[0][1];
  expect(request.panel_plan).toEqual({template_id: 7, template_token: 'signed', acknowledgements: ['impact']});
  expect(request).not.toHaveProperty('command'); expect(request).not.toHaveProperty('targets');
  expect(request).not.toHaveProperty('is_active');
});

test('panel submit remains disabled if acknowledgements or trigger validity change', async () => {
  store.record.panel_plan = {...plan, acknowledgements: []};
  await act(async () => ReactDOM.render(<Step3 visible/>, container));
  expect(button('提交').disabled).toBe(true);
  store.record.panel_plan = plan; store.trigger = 'monitor';
  await act(async () => ReactDOM.render(<Step3 visible/>, container));
  expect(button('提交').disabled).toBe(true);
  expect(http.post).not.toHaveBeenCalled();
});

test('Shell retains its command, local target and short interval behavior', async () => {
  store.record = {...store.record, interpreter: 'sh', command: 'echo ok'}; store.trigger_args.interval = 10;
  await act(async () => ReactDOM.render(<Step2 visible/>, container));
  expect(container.textContent).toContain('监控告警'); expect(container.querySelector('input[type=number]').min).toBe('10');
  await act(async () => ReactDOM.render(<Step3 visible/>, container));
  expect(container.textContent).toContain('主机选择器');
  await act(async () => button('提交').click());
  expect(http.post.mock.calls[0][1]).toMatchObject({interpreter: 'sh', command: 'echo ok', targets: ['local'], trigger_args: 10});
  expect(http.post.mock.calls[0][1]).not.toHaveProperty('panel_plan');
});

test('saved panel plans cannot be converted into Shell and saved scripts cannot become panel plans', async () => {
  store.record.id = 8;
  await act(async () => ReactDOM.render(<Step1 visible/>, container));
  expect(button('Shell').disabled).toBe(true); expect(button('Python').disabled).toBe(true);
  act(() => {ReactDOM.unmountComponentAtNode(container);});
  store.record.interpreter = 'sh';
  await act(async () => ReactDOM.render(<Step1 visible/>, container));
  expect(button('宝塔面板任务').disabled).toBe(true);
});

test('monitor options are deferred until the script trigger step is visible', async () => {
  monitorStore.records = []; store.record.interpreter = 'sh';
  await act(async () => ReactDOM.render(<Step2 visible={false}/>, container));
  expect(monitorStore.fetchRecords).not.toHaveBeenCalled();
  await act(async () => ReactDOM.render(<Step2 visible/>, container));
  expect(monitorStore.fetchRecords).toHaveBeenCalledTimes(1);
  await act(async () => ReactDOM.render(<Step2 visible={false}/>, container));
  await act(async () => ReactDOM.render(<Step2 visible/>, container));
  expect(monitorStore.fetchRecords).toHaveBeenCalledTimes(1);
});
