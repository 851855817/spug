/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import { Alert, Button, Modal, Space, Spin, Tag } from 'antd';
import Table from 'components/ResizableTable';
import http from 'libs/http';
import { t } from 'libs';
import store from './store';

class ComForm extends React.Component {
  state = {loading: true, info: {}, error: false};
  componentDidMount() {this.load();}
  load = () => {
    this.setState({loading: true});
    http.get(`/api/schedule/${store.record.id}/?id=${store.record.h_id}`)
      .then(info => this.setState({info, error: false}))
      .catch(() => this.setState({error: true}))
      .finally(() => this.setState({loading: false}));
  };
  render() {
    const {run_time, success, failure, outputs} = this.state.info;
    const items = Array.isArray(outputs) ? outputs.map((item, index) => ({...item, key: index})) : [];
    return <Modal open width={1000} maskClosable={false} title={t('任务执行结果')}
      onCancel={() => store.infoVisible = false} footer={null}>
      <Spin spinning={this.state.loading}>
        {this.state.error && <Alert type="error" message={t('结果读取失败，当前数据可能已过期')}
          action={<Button onClick={this.load}>{t('刷新')}</Button>}/>}
        <Space wrap style={{marginBottom: 16}}>
          {run_time && <span>{run_time}</span>}
          {Number.isInteger(success) && <span>{t('成功')} {success}</span>}
          {Number.isInteger(failure) && failure > 0 && <span>{t('失败')} {failure}</span>}
        </Space>
        <Table resizeKey="schedule-results-v2" rowKey="key" size="small" dataSource={items} pagination={false} scroll={{x: 800}}
          columns={[
            {title: t('主机'), dataIndex: 'name', width: 220},
            {title: t('状态'), dataIndex: 'code', width: 110, render: code =>
              <Tag color={code === 0 ? 'green' : Number.isInteger(code) ? 'red' : 'orange'}>
                {t(code === 0 ? '执行成功' : Number.isInteger(code) ? '执行失败' : '待核实')}
              </Tag>},
            {title: t('退出码'), dataIndex: 'code', width: 90, render: code => Number.isInteger(code) ? code : '—'},
            {title: t('耗时'), dataIndex: 'duration', width: 110, render: value => Number.isFinite(value) ? `${value} s` : '—'},
            {title: t('结果与异常原因'), dataIndex: 'output', render: (output, item) =>
              <pre style={{margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 260, overflowY: 'auto'}}>
                {typeof output === 'string' && output.trim() ? output : t(item.code === 0 ? '无输出' : '未取得输出，请核实执行结果')}
              </pre>},
          ]}/>
      </Spin>
    </Modal>;
  }
}

export default ComForm;
