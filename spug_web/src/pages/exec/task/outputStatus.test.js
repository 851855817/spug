import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import Command from './Output';
import Transfer from '../transfer/Output';
import commandStore from './store';
import transferStore from '../transfer/store';

jest.mock('mobx-react', () => ({observer: value => value}));
jest.mock('libs', () => ({t: value => value, X_TOKEN: 'fixture', http: {patch: jest.fn()}}));
jest.mock('gStore', () => ({terminal: {fontFamily: 'monospace'}}));
jest.mock('./store', () => ({outputs: {}, tag: '', items: [], counter: {}}));
jest.mock('../transfer/store', () => ({outputs: {}, tag: '', items: [], counter: {}}));
jest.mock('xterm', () => ({Terminal: class {options = {}; attachCustomKeyEventHandler() {} loadAddon() {} open() {} write() {} }}));
jest.mock('xterm-addon-fit', () => ({FitAddon: class {fit() {}}}));
jest.mock('antd', () => {
  const React = require('react'); const Wrapper = ({children}) => <div>{children}</div>;
  return {PageHeader: Wrapper, Tooltip: Wrapper, Tag: Wrapper, Button: Wrapper, message: {error: jest.fn()}};
});
let container, sockets, OriginalSocket;
beforeEach(() => {
  container = document.createElement('div'); document.body.appendChild(container);
  sockets = []; OriginalSocket = global.WebSocket;
  global.WebSocket = class {constructor() {sockets.push(this);} close() {}};
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); global.WebSocket = OriginalSocket;});

test.each([[Command, commandStore], [Transfer, transferStore]])('connection loss is unknown rather than a remote failure code', (View, store) => {
  store.outputs = {1: {title: 'fixture host', status: -2, data: 'measured output'}};
  act(() => {ReactDOM.render(<View/>, container);});
  act(() => {sockets[0].onclose(); ReactDOM.render(<View token="fixture"/>, container);});
  expect(container.textContent).toContain('结果待核实');
  expect(container.textContent).not.toContain('失败 (-1)');
  expect(store.outputs[1].data).toContain('measured output');
});
