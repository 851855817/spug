/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React, { useState, useEffect } from 'react';
import { observer } from 'mobx-react';
import { Card, Input, Select, Space, Tooltip, Spin, message } from 'antd';
import { FrownOutlined, ReloadOutlined, SyncOutlined } from '@ant-design/icons';
import { t } from 'libs';
import styles from './index.module.less';
import store from './store';

const StyleMap = {
  '0': {background: 'var(--kops-subtle)', border: '1px solid var(--kops-border)', color: 'var(--kops-muted)'},
  '1': {background: 'var(--kops-success-bg)', border: '1px solid var(--kops-success)', color: 'var(--kops-success)'},
  '2': {background: 'var(--kops-warning-bg)', border: '1px solid var(--kops-warning)', color: 'var(--kops-warning)'},
  '3': {background: 'var(--kops-error-bg)', border: '1px solid var(--kops-error)', color: 'var(--kops-error)'},
  '10': {background: 'var(--kops-subtle)', border: '1px dashed var(--kops-border)', color: 'var(--kops-muted)'}
}

const StatusMap = {
  '1': t('正常'),
  '2': t('警告'),
  '3': t('紧急'),
  '0': t('未激活'),
  '10': t('待调度')
}

function CardItem(props) {
  const {status, type_alias, group, desc, name, target, latest_run_time} = props.data
  const title = (
    <div>
      <div>{t('分组')}: {group}</div>
      <div>{t('类型')}: {type_alias}</div>
      <div>{t('名称')}: {name}</div>
      <div>{t('目标')}: {target}</div>
      <div>{t('状态')}: {StatusMap[status]}</div>
      <div>{t('更新')}: {latest_run_time || '---'}</div>
      <div>{t('描述')}: {desc}</div>
    </div>
  )
  return (
    <Tooltip title={title}>
      <div className={styles.card} style={StyleMap[status]}/>
    </Tooltip>
  )
}

function MonitorCard() {
  const [autoReload, setAutoReload] = useState(false);
  const [status, setStatus] = useState();

  useEffect(() => {
    store.fetchOverviews()

    return () => store.autoReload = null
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function handleAutoReload() {
    store.autoReload = !autoReload
    message.info(autoReload ? t('关闭自动刷新') : t('开启自动刷新'))
    if (!autoReload) store.fetchOverviews()
    setAutoReload(!autoReload)
  }

  const filteredRecords = store.ovDataSource.filter(x => !status || x.status === status)
  return (
    <Card title={t('总览')} style={{marginBottom: 24}} bodyStyle={{padding: '12px 24px'}} extra={(
      <Space size="middle">
        <Space>
          <div>{t('分组：')}</div>
          <Select allowClear style={{minWidth: 150}} value={store.f_group} onChange={v => store.f_group = v}
                  placeholder={t('请选择')}>
            {store.groups.map(item => (
              <Select.Option value={item} key={item}>{item}</Select.Option>
            ))}
          </Select>
        </Space>
        <Space>
          <div>{t('类型：')}</div>
          <Select allowClear style={{width: 120}} value={store.f_type} onChange={v => store.f_type = v}
                  placeholder={t('请选择')}>
            {store.types.map(item => <Select.Option key={item} value={item}>{item}</Select.Option>)}
          </Select>
        </Space>
        <Space>
          <div>{t('名称：')}</div>
          <Input allowClear value={store.f_name} onChange={e => store.f_name = e.target.value} placeholder={t('请输入')}/>
        </Space>
      </Space>
    )}>
      <Spin spinning={store.ovFetching}>
        <div className={styles.header}>
          {Object.entries(StyleMap).map(([s, style]) => {
            const count = store.ovDataSource.filter(x => x.status === s).length;
            return count ? (
              <Tooltip key={s} title={StatusMap[s]}>
                <div
                  className={styles.item}
                  style={s === status ? style : {...style, background: 'var(--kops-surface)'}}
                  onClick={() => setStatus(s === status ? '' : s)}>
                  {store.ovDataSource.filter(x => x.status === s).length}
                </div>
              </Tooltip>
            ) : null
          })}
          <Tooltip title={t('自动刷新')}>
            <div className={styles.autoLoad} onClick={handleAutoReload}>
              {autoReload ? <SyncOutlined spin style={{color: 'var(--kops-primary)'}}/> : <ReloadOutlined/>}
            </div>
          </Tooltip>
        </div>
        {filteredRecords.length > 0 ? (
          <Space wrap size={4}>
            {filteredRecords.map(item => (
              <CardItem key={item.id} data={item}/>
            ))}
          </Space>
        ) : (
          <div className={styles.notMatch}><FrownOutlined/></div>
        )}
      </Spin>
    </Card>
  )
}

export default observer(MonitorCard)
