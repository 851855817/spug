import React, { useEffect, useRef, useState } from 'react';
import { Table } from 'antd';
import './ResizableTable.less';

const MIN_WIDTH = 64;
const MAX_WIDTH = 1600;
const clamp = width => Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.round(width)));

function readWidths(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key));
    return Object.fromEntries(Object.entries(value || {}).filter(([, width]) =>
      Number.isFinite(width) && width >= MIN_WIDTH && width <= MAX_WIDTH));
  } catch (_) {return {};}
}
function saveWidths(key, widths) {
  try {localStorage.setItem(key, JSON.stringify(widths));} catch (_) { /* Browser preferences are optional. */ }
}
function childColumns(children) {
  const columns = [];
  React.Children.forEach(children, child => {
    if (React.isValidElement(child)) columns.push({...child.props, key: child.key ?? undefined,
      children: child.props.children ? childColumns(child.props.children) : undefined});
  });
  return columns;
}
function identify(columns, prefix = '') {
  const seen = {};
  return columns.map((column, index) => {
    const name = column.key ?? column.dataIndex ?? (typeof column.title === 'string' ? column.title : index);
    const base = `${prefix}col:${Array.isArray(name) ? JSON.stringify(name) : name}`;
    const count = seen[base] || 0;
    seen[base] = count + 1;
    const id = `${base}:${count}`;
    return {...column, resizeId: id, children: column.children ? identify(column.children, `${id}/`) : undefined};
  });
}
function leafColumns(columns) {return columns.flatMap(column => column.children ? leafColumns(column.children) : [column]);}

function HeaderCell({children, resizeId, resizeLabel, onResizeStart, onDragStart, onReset, style, ...props}) {
  const cell = useRef();
  function keyDown(event) {
    if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    if (event.key === 'Home') onReset();
    else onResizeStart(cell.current)(event.key === 'ArrowRight' ? 20 : -20, true);
  }
  return <th {...props} style={{...style, position: style?.position || 'relative'}} ref={cell}>
    {children}
    {resizeId && <span className="spug-column-resizer" role="separator" aria-orientation="vertical" tabIndex={0}
      aria-label={`调整${resizeLabel}列宽`}
      title="拖动调整列宽，双击恢复此表默认列宽" onPointerDown={event => onDragStart(event, cell.current)} onKeyDown={keyDown}
      onClick={event => event.stopPropagation()} onDoubleClick={event => {event.stopPropagation(); onReset();}}/>}
  </th>;
}

export default function ResizableTable({columns, children, resizeKey, components, scroll, className = '', ...props}) {
  const identified = identify(columns || childColumns(children));
  const leaves = leafColumns(identified);
  const storageKey = `spug:table-widths:v1:${window.location.pathname}:${resizeKey || leaves.map(column => column.resizeId).join('|')}`;
  const cleanup = useRef(() => {});
  useEffect(() => () => cleanup.current(), [storageKey]);
  const [saved, setSaved] = useState(() => ({key: storageKey, widths: readWidths(storageKey)}));
  const widths = saved.key === storageKey ? saved.widths : readWidths(storageKey);
  const resized = leaves.some(column => Number.isFinite(widths[column.resizeId]));
  function reset() {saveWidths(storageKey, {}); setSaved({key: storageKey, widths: {}});}
  function begin(id, cell) {
    const frozen = {...widths};
    cell.closest('thead').querySelectorAll('th[data-resize-column]').forEach(header => {
      const measured = header.getBoundingClientRect().width;
      if (measured > 0) frozen[header.getAttribute('data-resize-column')] = clamp(measured);
    });
    const startWidth = cell.getBoundingClientRect().width || frozen[id] || 150;
    return (delta, persist) => {
      const next = {...frozen, [id]: clamp(startWidth + delta)};
      setSaved({key: storageKey, widths: next});
      if (persist) saveWidths(storageKey, next);
    };
  }
  function drag(event, id, cell) {
    if (event.button !== 0) return;
    event.preventDefault(); event.stopPropagation(); cleanup.current();
    const startX = event.clientX;
    const pointerId = event.pointerId;
    const apply = begin(id, cell);
    let delta = 0;
    const oldCursor = document.body.style.cursor;
    const oldSelect = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none';
    function move(next) {
      if (pointerId !== undefined && next.pointerId !== pointerId) return;
      delta = next.clientX - startX; apply(delta, false);
    }
    function finish() {apply(delta, true); cleanup.current();}
    cleanup.current = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', finish);
      document.removeEventListener('pointercancel', finish);
      window.removeEventListener('blur', finish);
      document.body.style.cursor = oldCursor; document.body.style.userSelect = oldSelect;
      cleanup.current = () => {};
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', finish);
    document.addEventListener('pointercancel', finish);
    window.addEventListener('blur', finish);
  }
  function decorate(items) {
    return items.map(({resizeId, ...column}, index) => column.children
      ? {...column, children: decorate(column.children)}
      : {...column, width: widths[resizeId] || column.width,
        onHeaderCell: current => ({...column.onHeaderCell?.(current), resizeId,
          resizeLabel: typeof column.title === 'string' ? column.title : `第 ${index + 1} `,
          'data-resize-column': resizeId, onReset: reset, onResizeStart: cell => begin(resizeId, cell),
          onDragStart: (event, cell) => drag(event, resizeId, cell)})});
  }
  const selectionWidth = props.rowSelection ? Number(props.rowSelection.columnWidth) || 32 : 0;
  const hasExpand = props.expandable?.showExpandColumn !== false && (props.expandable?.expandedRowRender ||
    props.dataSource?.some(row => row[props.expandable?.childrenColumnName || 'children']?.length));
  const expandWidth = hasExpand ? Number(props.expandable?.columnWidth) || 48 : 0;
  const total = leaves.reduce((sum, column) => sum + (widths[column.resizeId] || Number(column.width) || 150), selectionWidth + expandWidth);
  return <Table {...props} className={`spug-resizable-table ${resized ? 'spug-table-resized' : ''} ${className}`}
    columns={decorate(identified)} components={{...components, header: {...components?.header, cell: HeaderCell}}}
    tableLayout={resized ? 'fixed' : props.tableLayout} scroll={resized ? {...scroll, x: total} : scroll}/>;
}

ResizableTable.Column = Table.Column;
ResizableTable.ColumnGroup = Table.ColumnGroup;
