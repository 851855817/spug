import React from 'react';
import { Alert, Button, Modal } from 'antd';

export default function Guidance({row, onClose, onRecheck}) {
  const panel = /^panel:([1-9][0-9]*)$/.exec(row?.source_key || '');
  return <Modal visible title={panel ? '宝塔证书续签指导' : '配置现有 ACME 客户端'} width={680} onCancel={onClose} footer={<Button onClick={onClose}>关闭</Button>}>
    {panel ? <>
      <Alert showIcon type="info" message="先核对网站配置，再预检续签。" description={row.renewal?.reason || '当前配置暂不支持从监控页直接续签。'}/>
      <p>包含未完整核实的配置引用时，监控页保留证书检查结果。可进入既有面板任务，选择网站并核对预检中的配置与同步影响，确认后再执行。</p>
      <p><a href={`/exec/panel?panel_id=${panel[1]}&operation=ssl_renew`}>前往宝塔续签任务</a></p>
    </> : <>
    <Alert showIcon type="info" message="先在服务器上完成安装与部署配置，再重新检测。"
      description="当前入口只提供指导，不安装客户端或运行远程安装命令。已有订单和 DNS 验证凭据会被保留。"/>
    <h3>acme.sh</h3>
    <p>按官方文档选择 HTTP 或 DNS 验证，为当前域名配置订单；用官方 install-cert 流程配置公开证书部署路径和明确的服务重载方式。</p>
    <p>当前可识别的订单重载命令是已验证 Nginx 绝对路径的配置检查与重载，例如 <code>/usr/sbin/nginx -t &amp;&amp; /usr/sbin/nginx -s reload</code>。使用独立主配置时，两段命令都须指定相同的已识别 <code>-c</code> 路径。自定义 hook、service／systemctl 命令暂不接管，请保留原有续签机制，不要为通过检测删除原配置。</p>
    <p><a href="https://github.com/acmesh-official/acme.sh" target="_blank" rel="noopener noreferrer">acme.sh 官方安装与使用文档</a></p>
    <h3>Certbot</h3>
    <p>按操作系统和 Web 服务选择官方安装方式，核对当前证书订单、域名、验证插件与部署目录，并配置可识别的服务重载方式。</p>
    <p><a href="https://certbot.eff.org/instructions" target="_blank" rel="noopener noreferrer">Certbot 官方安装指南</a> · <a href="https://eff-certbot.readthedocs.io/en/stable/using.html" target="_blank" rel="noopener noreferrer">证书管理与续签配置</a></p>
    <p>自定义 hook、多个匹配订单、动态路径或部署不一致会阻止续签。完成配置后，请巡检来源并重新创建预检。</p>
    </>}
    {onRecheck && <Button onClick={onRecheck}>重新检测来源</Button>}
  </Modal>;
}
