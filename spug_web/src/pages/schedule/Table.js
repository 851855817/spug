/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import { observer } from 'mobx-react';
import { DownOutlined, PlusOutlined } from '@ant-design/icons';
import { Modal, Tag, Dropdown, Radio, message } from 'antd';
import { LinkButton, Action, TableCard, AuthButton } from 'components';
import { hasPermission, http, t } from 'libs';
import store from './store';
import PanelPlanSummary from './PanelPlanSummary';
import PanelPreview from './PanelPreview';
import {useResourceAccess} from 'resources/ResourceAccess';

class ComTable extends React.Component {
  canManage = info => this.props.access.is_supper || info.can_manage === true;
  state = {panelTaskId: null};
  componentDidMount() {
    store.fetchRecords()
  }

  colors = ['orange', 'green', 'red', 'default'];

  moreMenus = (info) => ({
    items: [
      {
        key: 'test',
        label: <LinkButton auth="schedule.schedule.run" disabled={!this.canManage(info)} onClick={() => this.handleTest(info)}>{info.interpreter === 'baota' ? '重新预检' : t('执行测试')}</LinkButton>
      },
      {
        key: 'active',
        label: (
          <LinkButton
            auth="schedule.schedule.enable" disabled={!this.canManage(info)}
            onClick={() => this.handleActive(info)}>
            {info.is_active ? t('禁用任务') : t('激活任务')}</LinkButton>
        )
      },
      {
        key: 'record',
        label: <LinkButton onClick={() => store.showRecord(info)}>{t('历史记录')}</LinkButton>
      },
      {type: 'divider'},
      {
        key: 'delete',
        label: <LinkButton danger auth="schedule.schedule.del" disabled={!this.canManage(info)} onClick={() => this.handleDelete(info)}>{t('删除')}</LinkButton>
      }
    ].filter(item => (!['test', 'active', 'delete'].includes(item.key) || hasPermission(`schedule.schedule.${{test: 'run', active: 'enable', delete: 'del'}[item.key]}`)) && (info.interpreter !== 'baota' || (
      (item.key !== 'active' || info.is_active || ['exec.panel.view', 'exec.panel.create', 'exec.panel.execute'].every(hasPermission)) &&
      (item.key !== 'test' || ['exec.panel.view', 'exec.panel.create'].every(hasPermission)))))
  });

  columns = [{
    title: t('任务名称'),
    dataIndex: 'name',
  }, {
    title: t('任务类型'),
    dataIndex: 'type',
  }, {
    title: t('触发方式'),
    dataIndex: 'trigger_alias'
  }, {
    title: t('最新状态'),
    render: info => {
      if (info.interpreter === 'baota' && !info.is_active && info.latest_status_alias) {
        return <><Tag color={this.colors[info.latest_status]}>{info.latest_status_alias}</Tag><Tag>{t('未激活')}</Tag></>;
      }
      if (info.is_active) {
        if (info['latest_status_alias']) {
          return <Tag color={this.colors[info['latest_status']]}>{info['latest_status_alias']}</Tag>
        } else {
          return <Tag color="blue">{t('待调度')}</Tag>
        }
      } else {
        return <Tag>{t('未激活')}</Tag>
      }
    },
  }, {
    title: t('更新于'),
    dataIndex: 'latest_run_time_alias',
    sorter: (a, b) => a.latest_run_time.localeCompare(b.latest_run_time)
  }, {
    title: t('描述信息'),
    dataIndex: 'desc',
    ellipsis: true
  }, {
    title: t('操作'),
    width: 180,
    render: info => (
      <Action>
        <Action.Button disabled={info['latest_run_time'] === '1970-01-01'}
                       onClick={() => store.showInfo(info)}>{t('详情')}</Action.Button>
        <Action.Button auth="schedule.schedule.edit" disabled={!this.canManage(info) || (info.interpreter === 'baota' && (info.is_active || info.latest_status === 0))}
                       onClick={() => store.showForm(info)}>{t('编辑')}</Action.Button>
        <Dropdown menu={this.moreMenus(info)} trigger={['click']}>
          <LinkButton>
            {t('更多')} <DownOutlined/>
          </LinkButton>
        </Dropdown>
      </Action>
    )
  }];

  handleActive = (text) => {
    if (!this.canManage(text) || !hasPermission('schedule.schedule.enable')) return;
    const activatePanel = text.interpreter === 'baota' && !text.is_active;
    if (activatePanel && !['exec.panel.view', 'exec.panel.create', 'exec.panel.execute'].every(hasPermission)) return;
    Modal.confirm({
      title: activatePanel ? '确认激活宝塔自动执行计划' : t('操作确认'),
      okText: activatePanel ? '确认到点自动执行' : t('确定'),
      content: activatePanel ? <div>
        <p>激活【{text.name}】后，每次到点重新预检以下固定范围，全量通过后自动执行已确认的操作。失败或结果不确定时自动停用。</p>
        <PanelPlanSummary plan={text.panel_plan}/>
      </div> : text.is_active ? t('确定要禁用任务【{}】?', text['name']) : t('确定要激活任务【{}】?', text['name']),
      onOk: () => {
        return http.patch('/api/schedule/', {id: text.id, is_active: !text.is_active, ...(activatePanel ? {confirm_panel: true} : {})})
          .then(() => {
            message.success(t('操作成功'));
            store.fetchRecords()
          })
      }
    })
  };

  handleDelete = (text) => {
    if (!this.canManage(text) || !hasPermission('schedule.schedule.del')) return;
    Modal.confirm({
      title: t('删除确认'),
      content: t('确定要删除【{}】?', text['name']),
      onOk: () => {
        return http.delete('/api/schedule/', {params: {id: text.id}})
          .then(() => {
            message.success(t('删除成功'));
            store.fetchRecords()
          })
      }
    })
  };

  handleTest = (text) => {
    if (!this.canManage(text) || !hasPermission('schedule.schedule.run')) return;
    const isPanel = text.interpreter === 'baota';
    if (isPanel && !['exec.panel.view', 'exec.panel.create'].every(hasPermission)) return;
    Modal.confirm({
      title: isPanel ? '重新预检宝塔计划' : t('操作确认'),
      content: isPanel ? '仅对计划的固定范围生成新的只读预检，不会开始执行。计划现有调度规则继续生效。' : t('立即以串行模式执行该任务（不影响调度规则，且不会触发失败通知，测试执行会有120秒的超时，真实调度执行无此限制）？'),
      onOk: () => http.post(`/api/schedule/${text.id}/`, null, {timeout: 120000})
        .then(res => {
          if (isPanel) this.setState({panelTaskId: res.panel_task_id});
          else store.showInfo(text, res);
        })
    })
  };

  render() {
    return (
      <><TableCard
        tKey="si"
        rowKey="id"
        title={t('任务列表')}
        loading={store.isFetching}
        dataSource={store.dataSource}
        onReload={store.fetchRecords}
        actions={[
          <AuthButton
            auth="schedule.schedule.add"
            type="primary"
            icon={<PlusOutlined/>}
            onClick={() => store.showForm()}>{t('新建')}</AuthButton>,
          <Radio.Group value={store.f_active} onChange={e => store.f_active = e.target.value}>
            <Radio.Button value="">{t('全部')}</Radio.Button>
            <Radio.Button value="1">{t('已激活')}</Radio.Button>
            <Radio.Button value="0">{t('未激活')}</Radio.Button>
          </Radio.Group>
        ]}
        pagination={{
          showSizeChanger: true,
          showLessItems: true,
          showTotal: total => t('共 {} 条', total),
          pageSizeOptions: ['10', '20', '50', '100']
        }}
        columns={this.columns}/>
        {this.state.panelTaskId && hasPermission('exec.panel.view') && <Modal open width={1100} footer={null} title="宝塔计划只读预检"
          onCancel={() => this.setState({panelTaskId: null})} maskClosable={false}><PanelPreview taskId={this.state.panelTaskId}/></Modal>}
      </>
    )
  }
}

export default observer(function ResourceScheduleTable(props) {
  const access = useResourceAccess();
  return <ComTable {...props} access={access}/>;
})
