// KOps · 云枢 legal notices by K, 2026-10-27. Released under AGPL-3.0.
import React, { useState } from 'react';
import { Modal } from 'antd';
import { InfoCircleOutlined } from '@ant-design/icons';
import { RELEASE_REVISION, SOURCE_REPOSITORY, sourceLinks } from './source';
import styles from './branding.module.css';

const External = ({href, children}) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;

export function LegalLink({className = ''}) {
  const [visible, setVisible] = useState(false);
  const source = sourceLinks(RELEASE_REVISION);
  return <>
    <button type="button" className={`${styles.legalLink} ${className}`} onClick={() => setVisible(true)}>
      <InfoCircleOutlined aria-hidden="true"/> 开源许可与源码
    </button>
    <Modal title="KOps · 云枢 · 开源说明" visible={visible} footer={null} width={640} onCancel={() => setVisible(false)}>
      <div className={styles.legalContent}>
        <p><strong>KOps · 云枢</strong> 由 K 开发维护，基于 <External href="https://github.com/openspug/spug">Spug</External> 二次开发，与上游官方产品独立维护。</p>
        <p>原作品版权：© OpenSpug Organization；&lt;spug.dev@gmail.com&gt;。<br/>修改部分：© 2026 K。品牌与许可说明修改日期：2026-10-27。</p>
        <p>本修改版本遵循 <strong>GNU Affero General Public License v3.0（AGPL-3.0）</strong>。你可以依该协议使用、修改和再分发本软件。本软件不提供任何担保，包括适销性及特定用途适用性担保；详细权利与义务请阅读 <External href={`${process.env.PUBLIC_URL || ''}/kops-20261027/LICENSE.txt`}>完整许可证</External>。</p>
        {source ? <>
          <p>当前版本对应源码可免费获取，包含修改、依赖清单及构建部署脚本：<br/>
            <External href={source.tree}>浏览对应源码</External> · <External href={source.archive}>下载对应源码</External>
          </p>
          <p className={styles.revision}>版本：{source.revision}</p>
        </> : <p>开发构建（未绑定发布提交）：<External href={SOURCE_REPOSITORY}>源码与构建说明</External>。</p>}
        <p>各第三方依赖保留其原有版权与许可证。商标及第三方服务名称归各自权利人所有。</p>
      </div>
    </Modal>
  </>;
}

export function LegalNotice() {
  return <div className={styles.notice}>
    <div>由 K 开发维护 · 基于 Spug 二次开发</div>
    <div>© OpenSpug Organization · 修改部分 © 2026 K · <LegalLink/></div>
  </div>;
}
