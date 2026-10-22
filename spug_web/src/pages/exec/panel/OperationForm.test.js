import React, { useState } from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { http } from 'libs';
import OperationForm from './OperationForm';

jest.mock('libs', () => ({http: {get: jest.fn()}}));
jest.mock('antd', () => {
  const React = require('react');
  const Wrapper = ({children, label}) => <div>{label}{children}</div>;
  return {Form: Object.assign(Wrapper, {Item: Wrapper}), Spin: Wrapper, Typography: {Paragraph: Wrapper, Text: Wrapper},
    Alert: ({message}) => <div>{message}</div>, Checkbox: Wrapper, Input: Object.assign(() => null, {TextArea: () => null}),
    Select: ({options = [], value, onChange}) => <select value={value || ''} onChange={e => onChange(e.target.value)}>
      <option value="">选择</option>{options.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select>};
});

test('software choices and versions remain tied to the panel that provided them', async () => {
  http.get.mockImplementation((url, {params}) => Promise.resolve({compatibility: {version: '13.1.0', system: 'Linux'},
    software: [{name: params.panel_id === 1 ? 'php' : 'nginx', title: params.panel_id === 1 ? 'PHP' : 'Nginx',
      versions: [{m_version: params.panel_id === 1 ? '7.4' : '1.24', min_version: '33'}]}]}));
  const container = document.createElement('div');
  function FormPanel({id}) {
    const [value, setValue] = useState({});
    return <section data-panel={id}><OperationForm panel={{id}} operation="software_install" active value={value} onChange={setValue}/>
      <output>{JSON.stringify(value)}</output></section>;
  }
  await act(async () => {ReactDOM.render(<><FormPanel id={1}/><FormPanel id={2}/></>, container);});
  const first = container.querySelector('[data-panel="1"]');
  const second = container.querySelector('[data-panel="2"]');
  expect(first.querySelector('select').textContent).toContain('PHP');
  expect(first.querySelector('select').textContent).not.toContain('Nginx');
  expect(second.querySelector('select').textContent).toContain('Nginx');
  act(() => {first.querySelector('select').value = 'php'; Simulate.change(first.querySelector('select'));});
  const version = first.querySelectorAll('select')[1];
  expect(version.textContent).toContain('7.4 / 33'); expect(version.textContent).not.toContain('1.24');
  act(() => {version.value = '7.4|33'; Simulate.change(version);});
  expect(JSON.parse(first.querySelector('output').textContent)).toMatchObject({name: 'php', m_version: '7.4', min_version: '33'});
  act(() => {ReactDOM.unmountComponentAtNode(container);});
});

test('9.2 upgrade presents official stable policy and does not offer a fictitious exact version', async () => {
  http.get.mockResolvedValue({current_version: '9.2.0', upgrade_mode: 'legacy_stable', checked_version: '13.1.0', versions: []});
  const container = document.createElement('div');
  function Panel() {
    const [value, setValue] = useState({});
    return <><OperationForm panel={{id: 7}} operation="panel_upgrade" active value={value} onChange={setValue}/><output>{JSON.stringify(value)}</output></>;
  }
  await act(async () => {ReactDOM.render(<Panel/>, container);});
  expect(container.textContent).toContain('官方稳定通道');
  expect(container.textContent).toContain('13.1.0');
  expect(container.textContent).toContain('无法锁定');
  const choice = container.querySelector('select');
  act(() => {choice.value = 'stable'; Simulate.change(choice);});
  expect(JSON.parse(container.querySelector('output').textContent)).toEqual({channel: 'stable'});
  act(() => {ReactDOM.unmountComponentAtNode(container);});
});

test('PHP update options display current and expected versions on the selected panel', async () => {
  http.get.mockResolvedValue({php_updates: [{php_version: '74', m_version: '7.4', min_version: '34', current_version: '7.4.33', target_version: '7.4.34'}]});
  const container = document.createElement('div');
  function Panel() {
    const [value, setValue] = useState({});
    return <><OperationForm panel={{id: 7}} operation="php_update" active value={value} onChange={setValue}/><output>{JSON.stringify(value)}</output></>;
  }
  await act(async () => {ReactDOM.render(<Panel/>, container);});
  expect(container.textContent).toContain('7.4.33 → 预计 7.4.34');
  const choice = container.querySelector('select');
  act(() => {choice.value = '74'; Simulate.change(choice);});
  expect(JSON.parse(container.querySelector('output').textContent)).toMatchObject({php_version: '74', m_version: '7.4', min_version: '34', install_type: '0'});
  act(() => {ReactDOM.unmountComponentAtNode(container);});
});
