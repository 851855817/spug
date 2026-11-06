import React, { lazy, Suspense, useState } from 'react';
import { Button, Modal } from 'antd';
import { hasPermission } from 'libs';

const Reminders = lazy(() => import('./Reminders'));

// Keep the existing homepage/topbar read path unchanged. SSL details load only
// when an administrator opens this independent, account-scoped reminder view.
export default function ReminderEntry({compact = false}) {
  const [open, setOpen] = useState(false);
  if (typeof hasPermission !== 'function' || !hasPermission('monitor.ssl.view')) return null;
  return <>
    <Button type="link" size="small" aria-label="查看 SSL 到期提醒" onClick={() => setOpen(true)}>{compact ? 'SSL 提醒' : '查看 SSL 到期提醒'}</Button>
    {open && <Modal visible title="SSL 到期提醒" width={720} onCancel={() => setOpen(false)} footer={<Button onClick={() => setOpen(false)}>关闭</Button>}>
      <Suspense fallback={<p>正在加载 SSL 提醒…</p>}><Reminders/></Suspense>
    </Modal>}
  </>;
}
