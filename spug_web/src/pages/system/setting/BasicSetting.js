/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import { observer } from 'mobx-react';
import { Form, Input, Button, message } from 'antd';
import styles from './index.module.css';
import { http, t } from 'libs';
import store from './store';

export default observer(function () {
  function handleSubmit() {
    const spug_key = store.settings.spug_key;
    if (!spug_key) return message.error(t('请输入调用凭据'));
    store.loading = true;
    http.post('/api/setting/', {data: [{key: 'spug_key', value: spug_key}]})
      .then(() => {
        message.success(t('保存成功'));
        store.fetchSettings()
      })
      .finally(() => store.loading = false)
  }

  return (
    <React.Fragment>
      <div className={styles.title}>{t('基本设置')}</div>
      <div style={{maxWidth: 340}}>
        <Form.Item
          label={t('调用凭据')}
          labelCol={{span: 24}}
          extra={t('仅使用外部邮件、微信和 MFA 服务时填写，无需使用请留空。')}>
          <Input
            value={store.settings.spug_key}
            onChange={e => store.update('spug_key', e.target.value)}
            placeholder={t('请输入服务商提供的调用凭据')}/>
        </Form.Item>
        <Form.Item style={{marginTop: 24}}>
          <Button type="primary" loading={store.loading} onClick={handleSubmit}>{t('保存设置')}</Button>
        </Form.Item>
      </div>
    </React.Fragment>
  )
})
