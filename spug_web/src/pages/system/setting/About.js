// Modified by K on 2026-10-27 for KOps · 云枢 branding and legal notices.
/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import styles from './index.module.css';
import { SmileTwoTone } from '@ant-design/icons';
import { Descriptions, Spin, Button, Alert, notification } from 'antd';
import { observer } from 'mobx-react'
import { http, VERSION, t, docsUrl } from 'libs';
import Brand from '../../../branding/Brand';
import { LegalNotice } from '../../../branding/LegalNotice';


@observer
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
    http.get(`https://api.spug.cc/apis/release/latest/?version=${VERSION}`)
      .then(res => {
        if (res.has_new) {
          notification.open({
            key: 'new_version',
            duration: 0,
            top: 88,
            message: t('Spug 上游发现新版本 {}', res.version),
            icon: <SmileTwoTone/>,
            btn: <a target="_blank" rel="noopener noreferrer" href={docsUrl('update-version/')}>{t('如何升级？')}</a>,
            description: <pre style={{lineHeight: '30px'}}>{res.content}<br/>{res.extra}</pre>
          })
        } else if (res.extra) {
          notification.open({
            key: 'new_version',
            duration: 0,
            top: 88,
            message: t('Spug 上游已是最新版本'),
            icon: <SmileTwoTone/>,
            btn: <Button type="link" onClick={() => notification.close('new_version')}>{t('知道了')}</Button>,
            description: <pre style={{lineHeight: '30px'}}>{res.extra}</pre>
          })
        }
      })
  }


  render() {
    const {info, fetching} = this.state;
    return (
      <Spin spinning={fetching}>
        <div className={styles.title}><Brand/></div>
        <Descriptions column={1}>
          <Descriptions.Item label={t('操作系统')}>{info['system_version']}</Descriptions.Item>
          <Descriptions.Item label={t('Python版本')}>{info['python_version']}</Descriptions.Item>
          <Descriptions.Item label={t('Django版本')}>{info['django_version']}</Descriptions.Item>
          <Descriptions.Item label={t('Spug 上游内核 API 版本')}>{info['spug_version']}</Descriptions.Item>
          <Descriptions.Item label={t('Spug 上游内核 Web 版本')}>{VERSION}</Descriptions.Item>
          <Descriptions.Item label={t('Spug 上游官网')}>
            <a href="https://spug.cc" target="_blank" rel="noopener noreferrer">https://spug.cc</a>
          </Descriptions.Item>
          <Descriptions.Item label={t('更新日志')}>
            <a href={docsUrl('change-log/')} target="_blank"
               rel="noopener noreferrer">{docsUrl('change-log/')}</a>
          </Descriptions.Item>
        </Descriptions>
        <LegalNotice/>
        {info['spug_version'] && info['spug_version'] !== VERSION && (
          <Alert showIcon style={{width: 500}} type="warning" message={t('Spug API版本与Web版本不匹配，请尝试刷新浏览器后再次查看。')}/>
        )}
      </Spin>
    )
  }
}

export default About
