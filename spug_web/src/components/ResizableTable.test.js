import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import ResizableTable from './ResizableTable';

jest.mock('antd', () => {
  const React = require('react');
  const Table = ({columns = [], components, scroll}) => {
    const Cell = components?.header?.cell || 'th';
    return <table key={scroll?.x ? 'scroll' : 'plain'} data-scroll={scroll?.x}><thead><tr>{columns.map((column, index) => {
      const header = column.onHeaderCell?.(column) || {};
      return <Cell key={index} {...header} style={{...header.style, width: column.width}}>{column.title}</Cell>;
    })}</tr></thead></table>;
  };
  Table.Column = () => null;
  Table.ColumnGroup = () => null;
  return {Table};
});

let container;
let sort;
const columns = () => [{title: '名称', dataIndex: 'name', onHeaderCell: () => ({onClick: sort, 'data-original': 'kept'})},
  {title: '地址', dataIndex: 'address'}];
function render(props = {}) {act(() => {ReactDOM.render(<ResizableTable columns={columns()} resizeKey="main" {...props}/>, container);});}
function pointer(target, type, x) {
  const event = new Event(type, {bubbles: true, cancelable: true});
  Object.defineProperties(event, {clientX: {value: x}, button: {value: 0}});
  act(() => {target.dispatchEvent(event);});
}
function drag(delta) {
  const handle = container.querySelector('[role=separator]');
  expect(handle).not.toBeNull();
  pointer(handle, 'pointerdown', 150);
  pointer(document, 'pointermove', 150 + delta);
  pointer(document, 'pointerup', 150 + delta);
}
beforeEach(() => {
  localStorage.clear(); window.history.replaceState({}, '', '/host'); sort = jest.fn();
  container = document.createElement('div'); document.body.appendChild(container);
  jest.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
    return {width: parseFloat(this.style.width) || 150};
  });
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(container);}); container.remove(); jest.restoreAllMocks();});

test('drag changes only the selected column, persists across remount, and does not sort', () => {
  render(); drag(90);
  const cells = container.querySelectorAll('th');
  expect(cells[0].style.width).toBe('240px'); expect(cells[1].style.width).toBe('150px');
  expect(sort).not.toHaveBeenCalled(); expect(cells[0].getAttribute('data-original')).toBe('kept');
  act(() => {ReactDOM.unmountComponentAtNode(container);}); render();
  expect(container.querySelector('th').style.width).toBe('240px');
});

test('width limits, keyboard adjustment, and reset work without changing header clicks', () => {
  render(); drag(-500); expect(container.querySelector('th').style.width).toBe('64px');
  const handle = container.querySelector('[role=separator]');
  act(() => {handle.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));});
  expect(container.querySelector('th').style.width).toBe('84px');
  act(() => {handle.dispatchEvent(new MouseEvent('click', {bubbles: true}));}); expect(sort).not.toHaveBeenCalled();
  act(() => {container.querySelector('th').click();}); expect(sort).toHaveBeenCalledTimes(1);
  drag(5000); expect(container.querySelector('th').style.width).toBe('1600px');
  act(() => {handle.dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));});
  expect(container.querySelector('th').style.width).toBe('');
});

test('table and page preferences are isolated and hidden columns retain their own widths', () => {
  render(); drag(90);
  render({columns: [columns()[1]]}); expect(container.querySelector('th').style.width).toBe('150px');
  render(); expect(container.querySelector('th').style.width).toBe('240px');
  render({resizeKey: 'modal'}); expect(container.querySelector('th').style.width).toBe('');
  window.history.replaceState({}, '', '/baota'); render(); expect(container.querySelector('th').style.width).toBe('');
});

test('Column children are supported and unmount cancels document drag handlers', () => {
  render({columns: undefined, children: [<ResizableTable.Column key="name" title="名称" dataIndex="name"/>,
    false, <ResizableTable.Column key="address" title="地址" dataIndex="address"/>]});
  expect(container.querySelectorAll('[role=separator]')).toHaveLength(2);
  pointer(container.querySelector('[role=separator]'), 'pointerdown', 150);
  act(() => {ReactDOM.unmountComponentAtNode(container);});
  pointer(document, 'pointermove', 300); pointer(document, 'pointerup', 300);
  expect(localStorage.length).toBe(0);
});

test('unavailable browser storage does not prevent resizing', () => {
  jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {throw new Error('blocked');});
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {throw new Error('blocked');});
  render(); drag(50); expect(container.querySelector('th').style.width).toBe('200px');
});

test('fixed header positioning is preserved', () => {
  render({columns: [{...columns()[0], onHeaderCell: () => ({style: {position: 'sticky', right: 0}})}]});
  expect(container.querySelector('th').style.position).toBe('sticky');
});

test('different child schemas do not share widths through autogenerated React keys', () => {
  render({columns: undefined, resizeKey: undefined, children: [<ResizableTable.Column title="主机名称"/>, <ResizableTable.Column title="地址"/>]});
  drag(90);
  render({columns: undefined, resizeKey: undefined, children: [<ResizableTable.Column title="文件名称"/>, <ResizableTable.Column title="大小"/>]});
  expect(container.querySelector('th').style.width).toBe('');
});

test('first drag survives the header remount caused by horizontal scrolling', () => {
  render();
  pointer(container.querySelector('[role=separator]'), 'pointerdown', 150);
  pointer(document, 'pointermove', 160);
  pointer(document, 'pointermove', 230);
  pointer(document, 'pointerup', 230);
  expect(container.querySelector('th').style.width).toBe('230px');
});
