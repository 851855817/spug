import React from 'react';
import { Alert, Button, Modal } from 'antd';
import { dateUTC8, text } from './helpers';
import styles from './index.module.less';

function Facts({certificate}) {
  const item = certificate || {};
  return <dl className={styles.facts}>
    <dt>签发时间（UTC+8）</dt><dd>{dateUTC8(item.not_before)}</dd>
    <dt>到期时间（UTC+8）</dt><dd>{dateUTC8(item.expires_at)}</dd>
    <dt>SHA-256 指纹</dt><dd>{text(item.fingerprint)}</dd>
    <dt>SAN 域名</dt><dd>{Array.isArray(item.domains) ? item.domains.map(text).join('、') : '—'}</dd>
  </dl>;
}
export default function CertificateDetail({row, onClose}) {
  const local = row.local_certificate || {}, publicTLS = row.public_tls || {}, remote = publicTLS.certificate || publicTLS;
  const differs = !!local.fingerprint && !!remote.fingerprint && local.fingerprint !== remote.fingerprint;
  const verified = publicTLS.verified === true || publicTLS.valid === true;
  return <Modal visible title={`${row.domain} · 证书详情`} width={760} onCancel={onClose} footer={<Button onClick={onClose}>关闭</Button>}>
    <p>{row.source_name} · TLS {row.port || 443} · 最近巡检 {dateUTC8(row.sampled_at)}（UTC+8）</p>
    {row.stale && <Alert type="warning" showIcon message="当前为旧缓存，不能据此判断证书仍然正常。"/>}
    <h3>源站本地证书</h3>
    <Alert type={row.state === 'unconfigured' ? 'info' : row.state === 'normal' ? 'success' : 'warning'} showIcon message={text(row.message)}/>
    {row.state === 'unconfigured' && <p>已核对宝塔开关和网站主配置；未遍历引用配置。</p>}
    {!!local.fingerprint && <><p>{row.source_key?.startsWith('panel:') ? '面板登记路径' : '部署路径'}：{text(row.cert_path || local.cert_path || row.renewal?.summary?.cert_path)}</p><Facts certificate={local}/></>}
    <h3>公网 SNI TLS</h3>
    <Alert type={verified ? 'success' : 'warning'} showIcon message={verified ? '公网证书验证通过' : '公网证书尚未通过完整验证'}
      description={text(publicTLS.message || publicTLS.reason || '握手成功本身不代表证书可信；请核对信任链、有效期和域名。')}/>
    <Facts certificate={remote}/>
    {differs && <Alert type="info" showIcon message="公网与源站证书不同，可能由 CDN 或反向代理提供。"
      description="源站续签完成后，仍需确认公网证书是否更新；两者独立展示。"/>}
    <p>巡检说明：{text(row.message || '—')}</p>
    <p>续签支持：{text(row.renewal?.reason || (row.renewal?.supported ? '可创建只读预检' : '需先检查来源和客户端配置'))}</p>
  </Modal>;
}
