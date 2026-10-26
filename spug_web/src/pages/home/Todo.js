/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Tag } from 'antd';
import { hasPermission, http } from 'libs';
import { usePageVisibility } from '../server-monitor/useServerMonitor';
import styles from './index.module.less';

const categories = [
  {key: 'certificate-expired', label: '证书已过期', api: '/api/baota/inventory/sites/', path: '/exec/sites', state: 'expired', certificate: true},
  {key: 'certificate-expiring', label: '证书30 天内到期', api: '/api/baota/inventory/sites/', path: '/exec/sites', state: 'expiring', certificate: true},
  {key: 'server-expired', label: '服务器已过期', api: '/api/ops/expiry/assets/', path: '/host/expiry', state: 'expired'},
  {key: 'server-expiring', label: '服务器30 天内到期', api: '/api/ops/expiry/assets/', path: '/host/expiry', state: 'within_30'},
];

function link(category, item) {
  const query = new URLSearchParams({state: category.state});
  if (item) {
    if (category.certificate) query.set('panel_id', item.panel_id);
    query.set('q', category.certificate ? item.name : item.address || item.name);
  }
  return `${category.path}?${query}`;
}

function remaining(days) {
  if (!Number.isFinite(days)) return '日期未知';
  return days < 0 ? `已过期 ${Math.abs(days)} 天` : days === 0 ? '今天到期' : `剩余 ${days} 天`;
}

function TodoIndex() {
  const allowed = hasPermission('admin');
  const visible = usePageVisibility();
  const [revision, setRevision] = useState(0);
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!allowed || !visible) return;
    let valid = true, timer;
    setLoading(true);
    Promise.allSettled(categories.map(category => http.get(category.api,
      {params: {page: 1, page_size: 3, state: category.state}}))).then(values => {
      if (valid) setResults(values.map((value, index) => ({...categories[index],
        data: value.status === 'fulfilled' ? value.value : null})));
    }).finally(() => {
      if (valid) {
        setLoading(false);
        timer = window.setTimeout(() => setRevision(value => value + 1), 60000);
      }
    });
    return () => {valid = false; window.clearTimeout(timer);};
  }, [allowed, visible, revision]);
  const certificates = results.find(category => category.certificate && category.data)?.data;
  const incomplete = certificates && (!certificates.panels?.length || certificates.panels.some(panel => panel.status !== 'ok' || panel.stale));
  const empty = results.length === categories.length && results.every(category => category.data?.total === 0) && !incomplete;
  return (
    <Card title="待办事项" className={styles.todo} loading={loading && !results.length}
      extra={allowed && <Button type="link" disabled={loading} onClick={() => setRevision(value => value + 1)}>刷新待办</Button>}>
      {!allowed ? <p>暂无可查看的到期待办</p> : <>
        {incomplete && <Alert type="warning" showIcon message="证书数据不完整，部分面板尚未采集、缓存已失效或过期。"/>}
        {empty && <p>暂无到期待办</p>}
        {results.map(category => <section className={styles.todoGroup} key={category.key} aria-label={category.label}>
          <div className={styles.todoHeading}><a href={link(category)}>{category.label}</a>
            {category.data ? <Tag color={category.data.total ? category.state === 'expired' ? 'red' : 'orange' : 'default'}>{category.data.total}</Tag>
              : <span role="alert">读取失败，请刷新重试</span>}</div>
          {category.data?.items.slice(0, 3).map(item => <a className={styles.todoItem} key={item.id || item.key} href={link(category, item)}>
            <span className={styles.todoName}><b>{item.name}</b><small>{category.certificate ? item.panel_name : item.address}</small></span>
            <span className={styles.todoDue}><b>{remaining(item.days_remaining)}</b><small>{category.certificate ? item.expires_at : item.expires_on}
              {item.stale && ' · 旧缓存'}</small></span>
          </a>)}
        </section>)}
        <div className={styles.todoHint}>按已登记到期日期；证书来自上次采集。每类显示前 3 条，点击标题查看全部。</div>
      </>}
    </Card>
  )
}

export default TodoIndex
