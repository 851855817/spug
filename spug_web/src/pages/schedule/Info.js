/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import { Alert, Button, Modal, Space, Spin, Tag } from 'antd';
import Table from 'components/ResizableTable';
import http from 'libs/http';
import { hasPermission, t } from 'libs';
import store from './store';
import PanelPreview from './PanelPreview';

const PANEL_STATES = {ready: '待执行', prepared: '预检完成', preflight_queued: '等待预检', preflighting: '预检中',
  queued: '等待执行', running: '执行中', dispatching: '正在下发', success: '成功', already: '已满足',
  failed: '失败', uncertain: '待核实', remote: '远端处理中', skipped: '已跳过', cancelled: '已取消未下发项',
  completed: '已结束（请查看结果）', pending: '等待中', reconciling: '只读核验中', paused: '已暂停'};
const PANEL_PHASES = {preflight: '重新预检', preflight_queued: '等待预检', prepared: '预检完成',
  running: '执行中', executing: '执行中', completed: '已结束', failed: '失败', uncertain: '待核实',
  blocked: '预检阻止执行', skipped: '已跳过', stopped: '已停止', cancelled: '已取消未下发项'};

class ComForm extends React.Component {
  state = {loading: true, info: {}, error: false, panelOpen: false};
  componentDidMount() {this.mounted = true; this.load();}
  componentWillUnmount() {this.mounted = false; clearTimeout(this.timer);}
  load = () => {
    clearTimeout(this.timer);
    this.setState({loading: true});
    http.get(`/api/schedule/${store.record.id}/?id=${store.record.h_id}`)
      .then(info => {
        if (!this.mounted) return;
        this.setState({info, error: false});
        if (info.kind === 'baota' && info.status === 0) this.timer = setTimeout(this.load, 3000);
      })
      .catch(() => {if (this.mounted) this.setState({error: true});})
      .finally(() => {if (this.mounted) this.setState({loading: false});});
  };
  render() {
    const {run_time, success, failure, outputs, kind, phase, message, status, panel_task_id, duration} = this.state.info;
    const isPanel = kind === 'baota';
    const items = Array.isArray(outputs) ? outputs.map((item, index) => ({...item, key: index})) : [];
    return <Modal open width={1000} maskClosable={false} title={t('任务执行结果')}
      onCancel={() => store.infoVisible = false} footer={null}>
      <Spin spinning={this.state.loading}>
        {this.state.error && <Alert type="error" message={t('结果读取失败，当前数据可能已过期')}
          action={<Button onClick={this.load}>{t('刷新')}</Button>}/>}
        {isPanel && message && <Alert showIcon type={status === 2 ? 'error' : status === 1 ? 'success' : 'info'} message={message} style={{marginBottom: 12}}/>}
        <Space wrap style={{marginBottom: 16}}>
          {isPanel && <Tag color={status === 1 ? 'green' : status === 2 ? 'red' : status === 3 ? 'default' : 'orange'}>{status === 1 ? '执行成功' : status === 2 ? '执行失败' : status === 3 ? '已跳过' : '执行中'}</Tag>}
          {isPanel && phase && <span>{PANEL_PHASES[phase] || phase}</span>}
          {run_time && <span>{run_time}</span>}
          {Number.isInteger(success) && <span>{t('成功')} {success}</span>}
          {Number.isInteger(failure) && failure > 0 && <span>{t('失败')} {failure}</span>}
          {isPanel && Number.isFinite(duration) && <span>{t('耗时')} {duration} s</span>}
          {isPanel && panel_task_id && hasPermission('exec.panel.view') && <Button onClick={() => this.setState({panelOpen: !this.state.panelOpen})}>查看关联面板任务</Button>}
        </Space>
        <Table resizeKey="schedule-results-v2" rowKey="key" size="small" dataSource={items} pagination={false} scroll={{x: 800}}
          columns={[
            {title: isPanel ? '面板' : t('主机'), dataIndex: 'name', width: 220},
            isPanel ? {title: t('状态'), dataIndex: 'state', width: 140, render: state =>
              <Tag color={['success', 'already'].includes(state) ? 'green' : state === 'failed' ? 'red' : 'orange'}>{PANEL_STATES[state] || state || '待核实'}</Tag>} :
            {title: t('状态'), dataIndex: 'code', width: 110, render: code =>
              <Tag color={code === 0 ? 'green' : Number.isInteger(code) ? 'red' : 'orange'}>
                {t(code === 0 ? '执行成功' : Number.isInteger(code) ? '执行失败' : '待核实')}
              </Tag>},
            ...(!isPanel ? [
              {title: t('退出码'), dataIndex: 'code', width: 90, render: code => Number.isInteger(code) ? code : '—'},
              {title: t('耗时'), dataIndex: 'duration', width: 110, render: value => Number.isFinite(value) ? `${value} s` : '—'},
            ] : []),
            {title: t('结果与异常原因'), dataIndex: 'output', render: (output, item) =>
              <pre style={{margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 260, overflowY: 'auto'}}>
                {typeof output === 'string' && output.trim() ? output : t((isPanel ? ['success', 'already'].includes(item.state) : item.code === 0) ? '无输出' : '未取得输出，请核实执行结果')}
              </pre>},
          ]}/>
        {isPanel && this.state.panelOpen && panel_task_id && hasPermission('exec.panel.view') && <div style={{marginTop: 16}}><PanelPreview taskId={panel_task_id}/></div>}
      </Spin>
    </Modal>;
  }
}

export default ComForm;
