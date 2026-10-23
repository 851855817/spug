import React, { useEffect } from 'react';
import { Alert, Button, Empty, Modal, Spin } from 'antd';
import { hasPermission } from 'libs';
import ResourceCard from 'pages/server-monitor/ResourceCard';
import useServerMonitor, { usePageVisibility } from 'pages/server-monitor/useServerMonitor';

export default function ResourceMonitorDetail({kind, id, name, onCancel}) {
  const monitor = useServerMonitor(hasPermission('monitor.monitor.view'));
  const visible = usePageVisibility();
  const card = monitor.cards.find(row => row.sources.some(source => source.kind === kind && source.id === id));
  const key = card?.key;
  const sampler = monitor.sampler;
  const active = !!key && monitor.ready && !monitor.loading && visible;
  useEffect(() => {
    sampler.update({keys: key ? [key] : [], interval: 60, active});
    return () => sampler.stop();
  }, [sampler, key, active, monitor.revision, visible]);
  return <Modal visible title={`${name || '服务器'} · 监控详情`} width={560} footer={null} destroyOnClose onCancel={onCancel}>
    <Spin spinning={monitor.loading}>
      {monitor.error ? <Alert type="error" showIcon message="监控信息加载失败"
        action={<Button onClick={() => monitor.refresh()}>重试</Button>}/>
        : card ? <ResourceCard card={card} interval={60} collecting={!!monitor.collecting[key]}
          collectDisabled={!visible || monitor.loading} collectionResult={monitor.collectionResult[key]}
          onCollect={() => sampler.collect(key)}/>
          : monitor.ready && <Empty description="未开启监控或已无权查看"/>}
    </Spin>
  </Modal>;
}
