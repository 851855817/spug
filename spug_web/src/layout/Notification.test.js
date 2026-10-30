import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { http } from 'libs';
import { notification } from 'antd';
import Notification from './Notification';
import { getReminderPreferences, notificationReminder } from '../components/personalReminders';

jest.mock('libs', () => ({X_TOKEN: 'fixture', t: value => value, http: {get: jest.fn(), post: jest.fn(), patch: jest.fn()}}));
jest.mock('antd', () => {
  const List = ({dataSource, renderItem}) => <div>{dataSource.map(row => <section key={row.id} data-row={row.id}>{renderItem(row)}</section>)}</div>;
  List.Item = ({children, onClick}) => <div onClick={onClick}>{children}</div>;
  List.Item.Meta = ({title, description}) => <div>{title}{description}</div>;
  return {List, Dropdown: ({children, menu}) => <div>{children}{menu.items.map(item => <div key={item.key}>{item.label}</div>)}</div>,
    Badge: ({children, count}) => <span data-count={count}>{children}</span>,
    Button: ({children, onClick, disabled}) => <button disabled={disabled} onClick={onClick}>{children}</button>,
    Modal: ({children, visible}) => visible ? <div role="dialog">{children}</div> : null,
    Alert: ({message}) => <div role="alert">{message}</div>, notification: {warning: jest.fn(), close: jest.fn()}};
});
const flush = async () => {for (let i=0;i<30;i++) await Promise.resolve();};
let container, socket, settings, records;
const row = id => ({id, title: '任务失败', content: '模拟任务无法连接', unread: true, source: 'schedule', created_at: `2030-01-01 01:00:0${id}`});
beforeEach(() => {
  jest.useFakeTimers(); localStorage.setItem('id', String(Math.random())); localStorage.setItem('token','fixture');
  Object.defineProperty(window,'crypto',{configurable:true,value:{getRandomValues: values => require('crypto').randomFillSync(values)}});
  global.WebSocket = class {constructor(){socket=this;this.readyState=1;} send(){} close(){this.readyState=3;}};
  settings={}; records=[row(1)];
  http.get.mockImplementation(path => Promise.resolve(path.includes('/setting/user/') ? {...settings} : records));
  http.post.mockImplementation((url, body) => {settings[body.key]=body.value; return Promise.resolve({...settings});});
  http.patch.mockResolvedValue({});
  container=document.createElement('div');document.body.appendChild(container);
});
afterEach(async () => {act(()=>{ReactDOM.unmountComponentAtNode(container);});await flush();container.remove();
  jest.useRealTimers();jest.resetAllMocks();localStorage.clear();});
const render=async()=>act(async()=>{ReactDOM.render(<Notification/>,container);await flush();});
const incoming=async()=>act(async()=>{socket.onmessage({data:JSON.stringify({title:records[0].title,content:records[0].content})});await flush();});

test('dismissing a notification changes only personal preferences and removes its own badge count',async()=>{
  await render();
  await act(async()=>{container.querySelector('[data-row="1"] button').click();await flush();});
  expect(http.patch).not.toHaveBeenCalled();
  expect(container.querySelector('[data-row="1"]')).toBeNull();
  expect(container.querySelector('[data-count]').dataset.count).toBe('0');
  expect(http.post).toHaveBeenCalledTimes(1);
  await incoming();expect(notification.warning).not.toHaveBeenCalled();
});

test('a new message ID still pops up after an older identical message is dismissed',async()=>{
  await render();
  await act(async()=>{await getReminderPreferences().mute(notificationReminder(records[0]));await flush();});
  records=[row(2),row(1)];await incoming();
  expect(notification.warning).toHaveBeenCalledTimes(1);
  expect(container.querySelector('[data-row="2"]')).not.toBeNull();
  expect(container.querySelector('[data-count]').dataset.count).toBe('1');
});

test('the live popup dismiss button closes that popup without touching global unread state',async()=>{
  await render();await incoming();
  const options=notification.warning.mock.calls[0][0];
  const popup=document.createElement('div');document.body.appendChild(popup);
  await act(async()=>{ReactDOM.render(options.btn,popup);await flush();});
  await act(async()=>{popup.querySelector('button').click();await flush();});
  expect(notification.close).toHaveBeenCalledWith(options.key);expect(http.patch).not.toHaveBeenCalled();
  act(()=>{ReactDOM.unmountComponentAtNode(popup);});popup.remove();
});

test('an in-flight event cannot open a popup after the header is unmounted',async()=>{
  await render();let resolve;
  http.get.mockImplementationOnce(()=>new Promise(finish=>{resolve=finish;}));
  await incoming();act(()=>{ReactDOM.unmountComponentAtNode(container);});
  await act(async()=>{resolve(records);await flush();});
  expect(notification.warning).not.toHaveBeenCalled();
});

test('a received live alert remains visible if refreshing notifications fails',async()=>{
  await render();
  http.get.mockImplementationOnce(()=>Promise.reject(new Error('fixture timeout')));
  await incoming();
  expect(notification.warning).toHaveBeenCalledTimes(1);
  expect(notification.warning.mock.calls[0][0].message).toBe(records[0].title);
  expect(http.post).not.toHaveBeenCalled();
});

test('marking visible notifications read preserves the read state of a hidden notification',async()=>{
  records=[{...row(1),unread:false},row(2)];
  await render();
  const item=notificationReminder(records[0]);
  await act(async()=>{await getReminderPreferences().mute(item);await flush();});
  await act(async()=>{[...container.querySelectorAll('div')].find(el=>el.textContent==='全部 已读').click();await flush();});
  expect(http.patch).toHaveBeenCalledWith('/api/notify/',{ids:[2]});
  await act(async()=>{await getReminderPreferences().restore(item);await flush();});
  expect(container.querySelector('[data-row="1"]')).not.toBeNull();
  expect(container.querySelector('[data-count]').dataset.count).toBe('0');
});
