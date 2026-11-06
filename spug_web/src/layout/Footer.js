// Modified by K on 2026-10-27 for KOps · 云枢 branding and legal notices.
/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import { Layout } from 'antd';
import { LegalNotice } from '../branding/LegalNotice';
import styles from './layout.module.less';


export default function () {
  return (
    <Layout.Footer style={{padding: 0}}>
      <div className={styles.footer}>
        <LegalNotice/>
      </div>
    </Layout.Footer>
  )
}
