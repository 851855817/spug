// Modified by K on 2026-10-28 for KOps · 云枢 branding and legal notices.
/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import styles from './index.module.css';
import { Descriptions, Spin, Alert } from 'antd';
import { observer } from 'mobx-react'
import { http, VERSION, t } from 'libs';
import Brand from '../../../branding/Brand';
import { LegalNotice } from '../../../branding/LegalNotice';
import { RELEASE_REVISION } from '../../../branding/source';


class About extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      fetching: true,
      info: {}
    }
  }

  componentDidMount() {
    http.get('/api/setting/about/')
      .then(res => this.setState({info: res}))
      .finally(() => this.setState({fetching: false}))
  }


  render() {
    const {info, fetching} = this.state;
    return (
      <Spin spinning={fetching}>
        <div className={styles.title}><Brand/></div>
        <Descriptions column={1}>
          <Descriptions.Item label={t('应用版本')}>{/^[0-9a-f]{40}$/.test(RELEASE_REVISION || '') ? RELEASE_REVISION.slice(0,7) : t('开发构建')}</Descriptions.Item>
          <Descriptions.Item label={t('操作系统')}>{info['system_version']}</Descriptions.Item>
          <Descriptions.Item label={t('Python版本')}>{info['python_version']}</Descriptions.Item>
          <Descriptions.Item label={t('Django版本')}>{info['django_version']}</Descriptions.Item>
          <Descriptions.Item label={t('服务端内核版本')}>{info['spug_version']}</Descriptions.Item>
          <Descriptions.Item label={t('前端内核版本')}>{VERSION}</Descriptions.Item>
        </Descriptions>
        <LegalNotice/>
        {info['spug_version'] && info['spug_version'] !== VERSION && (
          <Alert showIcon style={{maxWidth: 500}} type="warning" message={t('服务端与前端内核版本不匹配，请刷新浏览器后再次查看。')}/>
        )}
      </Spin>
    )
  }
}

export default observer(About)
