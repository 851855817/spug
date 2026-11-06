// Personal reminder controls by K, 2026-10-27. Released under AGPL-3.0.
import React, { useState } from 'react';
import { Alert, Button, Modal } from 'antd';
import { usePersonalReminders } from './personalReminders';
import styles from './reminderDismiss.module.css';

export default function ReminderDismiss({item, onDismiss}) {
  const prefs = usePersonalReminders();
  return <Button type="link" size="small" disabled={!item || prefs.status !== 'ready' || prefs.pending}
    onClick={event => {event.preventDefault(); event.stopPropagation();
      prefs.mute(item).then(() => {if (onDismiss) onDismiss();}).catch(() => {});
    }}>不再提醒</Button>;
}

export function ReminderControls() {
  const prefs = usePersonalReminders();
  const [visible, setVisible] = useState(false);
  const items = Object.values(prefs.records).filter((row, index, rows) => row.muted &&
    rows.findIndex(other => other.muted && other.identity === row.identity && other.cycle === row.cycle) === index);
  return <>
    {prefs.error && <Alert type="error" message={prefs.error} action={
      <Button size="small" disabled={prefs.pending} onClick={prefs.reload}>重试读取</Button>}/>}
    <Button type="link" size="small" onClick={() => {setVisible(true); prefs.reload();}}>已忽略（{items.length}）</Button>
    <Modal title="我忽略的提醒" visible={visible} footer={null} onCancel={() => setVisible(false)}>
      <p>仅对当前账号生效；到期日期更新或出现新消息时会再次提醒。</p>
      {prefs.error && <Alert type="error" message={prefs.error}/>}
      {!items.length && <p>暂无已忽略的提醒</p>}
      {items.map(item => <div className={styles.item} key={`${item.identity}:${item.cycle}`}>
        <span>{item.label}<small>{item.cycle}</small></span>
        <Button type="link" disabled={prefs.pending || prefs.status !== 'ready'}
          onClick={() => prefs.restore(item).catch(() => {})}>恢复提醒</Button>
      </div>)}
    </Modal>
  </>;
}
