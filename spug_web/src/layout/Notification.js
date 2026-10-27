import React, { useState, useEffect, useRef } from 'react';
import { List, Dropdown, Badge, Button, notification } from 'antd';
import {
  NotificationOutlined,
  MonitorOutlined,
  FlagOutlined,
  ScheduleOutlined,
  AlertOutlined
} from '@ant-design/icons';
import { http, X_TOKEN, t } from 'libs';
import moment from 'moment';
import styles from './layout.module.less';
import ReminderDismiss, { ReminderControls } from '../components/ReminderDismiss';
import { getReminderPreferences, notificationReminder, usePersonalReminders } from '../components/personalReminders';

let ws = {readyState: 3};
let timer;


function Icon(props) {
  switch (props.type) {
    case 'monitor':
      return <MonitorOutlined style={{fontSize: 24, color: '#1890ff'}}/>
    case 'schedule':
      return <ScheduleOutlined style={{fontSize: 24, color: '#1890ff'}}/>
    case 'flag':
      return <FlagOutlined style={{fontSize: 24, color: '#1890ff'}}/>
    case 'alert':
      return <AlertOutlined style={{fontSize: 24, color: '#ff4d4f'}}/>
    default:
      return null
  }
}

export default function () {
  const [loading, setLoading] = useState(false);
  const [notifies, setNotifies] = useState([]);
  const [reads, setReads] = useState([]);
  const preferences = usePersonalReminders();
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    fetch();
    listen();
    timer = setInterval(() => {
      if (ws.readyState === 1) {
        ws.send('ping')
      } else if (ws.readyState === 3) {
        listen()
      }
    }, 10000)
    return () => {
      mounted.current = false;
      if (timer) clearInterval(timer);
      if (ws.close) ws.close()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function fetch() {
    setLoading(true);
    return http.get('/api/notify/')
      .then(res => {
        if (mounted.current) {
          setReads(res.filter(x => !x.unread).map(x => x.id));
          setNotifies(res);
        }
        return res;
      })
      .finally(() => {if (mounted.current) setLoading(false);})
  }

  function listen() {
    if (!X_TOKEN) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/api/ws/notify/?x-token=${X_TOKEN}`);
    ws.onopen = () => ws.send('ok');
    const owner = getReminderPreferences();
    ws.onmessage = e => {
      if (e.data !== 'pong') {
        try {
          const {title, content} = JSON.parse(e.data);
          // A failed list refresh must never swallow an alert already received over WebSocket.
          fetch().catch(() => []).then(async records => {
            if (!mounted.current || getReminderPreferences() !== owner) return;
            const store = getReminderPreferences(); await store.load();
            if (!mounted.current || getReminderPreferences() !== owner) return;
            const matched = records.find(item => item.title === title && item.content === content);
            const item = notificationReminder(matched);
            if (item && store.isMuted(item)) return;
            const key = `open${Date.now()}`;
            const description = <div style={{whiteSpace: 'pre-wrap'}}>{t(content)}</div>;
            const btn = <><ReminderDismiss item={item} onDismiss={() => notification.close(key)}/>
              <Button type="primary" size="small" onClick={() => notification.close(key)}>{t('知道了')}</Button></>;
            notification.warning({message: t(title), description, btn, key, top: 64, duration: null});
          }).catch(() => {});
        } catch (e) {

        }
      }
    }
  }

  function handleVisible(visible) {
    if (visible) {
      fetch()
    }
  }

  function handleRead(e, item) {
    e.stopPropagation();
    if (reads.indexOf(item.id) === -1) {
      reads.push(item.id);
      setReads([...reads])
      http.patch('/api/notify/', {ids: [item.id]})
    }
  }

  function handleReadAll() {
    const ids = visibleNotifies.map(x => x.id);
    setReads(current => [...new Set([...current, ...ids])]);
    http.patch('/api/notify/', {ids})
  }

  const visibleNotifies = notifies.filter(item => !preferences.isMuted(notificationReminder(item)));
  const count = visibleNotifies.filter(item => !reads.includes(item.id)).length;
  const notifyMenu = {
    className: styles.notify,
    items: [
      {
        key: 'list',
        style: {padding: 0, whiteSpace: 'unset'},
        label: (
          <>
            <List
              loading={loading}
              style={{maxHeight: 500, overflow: 'scroll'}}
              itemLayout="horizontal"
              dataSource={visibleNotifies}
              renderItem={item => (
                <List.Item className={styles.item} onClick={e => handleRead(e, item)}>
                  <List.Item.Meta
                    style={{opacity: reads.includes(item.id) ? 0.4 : 1}}
                    avatar={<Icon type={item.source}/>}
                    title={<span style={{fontWeight: 400, color: '#404040'}}>{t(item.title)}</span>}
                    description={[
                      <div key="1" style={{fontSize: 12, overflowWrap: 'anywhere'}}>{t(item.content)}</div>,
                      <div key="2" style={{fontSize: 12}}>{moment(item['created_at']).fromNow()}
                        <ReminderDismiss item={notificationReminder(item)}/></div>
                    ]}/>
                </List.Item>
              )}/>
            <ReminderControls/>
            {visibleNotifies.length !== 0 && (
              <div className={styles.btn} onClick={handleReadAll}>{t('全部 已读')}</div>
            )}
          </>
        )
      }
    ]
  };
  return (
    <div className={styles.notification}>
      <Dropdown trigger={['click']} onOpenChange={handleVisible} menu={notifyMenu}>
        <div className={styles.trigger}>
          <Badge count={count > 0 ? count : 0}>
            <NotificationOutlined style={{fontSize: 16}}/>
          </Badge>
        </div>
      </Dropdown>
    </div>
  )
}
