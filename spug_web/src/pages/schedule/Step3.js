/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React, { useEffect, useState } from 'react';
import { observer } from 'mobx-react';
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons';
import { Alert, Form, Select, Button, message } from 'antd';
import { Container } from 'components';
import HostSelector from 'pages/host/Selector';
import hostStore from 'pages/host/store';
import moment from 'moment';
import lds from 'lodash';
import { http, t } from 'libs';
import S from './store';
import styles from './index.module.less';
import PanelPlanSummary from './PanelPlanSummary';
import { panelPlanReady, panelPlanPayload, validPanelTrigger } from './panelPlan';
import {useResourceAccess} from 'resources/ResourceAccess';

export default observer(function (props) {
  const access = useResourceAccess();
  const [loading, setLoading] = useState(false)
  const isPanel = S.record.interpreter === 'baota';
  const validPanel = panelPlanReady(S.record.panel_plan) && validPanelTrigger(S.trigger, S.trigger_args);
  const canTargets = isPanel ? access.canAll('panel', (S.record.panel_plan?.panels || []).map(panel => panel.id), `exec.panel.create&exec.panel_action.${S.record.panel_plan?.operation}`)
    : S.targets.filter(Boolean).every(id => id === 'local' ? access.is_supper : id === 'monitor' ? access.is_supper : access.canManage('host', id));
  useEffect(() => {if (props.visible && !isPanel) hostStore.initial();}, [props.visible, isPanel]);

  function handleSubmit() {
    if (!canTargets || (isPanel && !validPanel)) return;
    const formData = lds.pick(S.record, ['id', 'name', 'type', 'interpreter', 'desc', 'rst_notify']);
    if (isPanel) formData.panel_plan = panelPlanPayload(S.record.panel_plan);
    else {formData.command = S.record.command; formData.targets = S.targets.filter(x => x);}
    formData['trigger'] = S.trigger;
    formData['trigger_args'] = _parse_args();
    if (!isPanel && S.trigger === 'monitor') {
      if (formData.targets.length > 1) {
        return message.error(t('监控告警类触发器，只能选择一个执行对象'))
      }
    } else if (!isPanel && formData.targets.includes('monitor')) {
      return message.error(t('执行对象选择有误，请重新选择'))
    }
    setLoading(true)
    http.post('/api/schedule/', formData)
      .then(res => {
        message.success(t('操作成功'));
        S.formVisible = false;
        S.fetchRecords()
      }, () => setLoading(false))
  }

  function _parse_args() {
    switch (S.trigger) {
      case 'date':
        return S.trigger_args.date.format('YYYY-MM-DD HH:mm:ss');
      case 'cron':
        const {rule, start, stop} = S.trigger_args.cron;
        return JSON.stringify({
          rule,
          start: start ? moment(start).format('YYYY-MM-DD HH:mm:ss') : null,
          stop: stop ? moment(stop).format('YYYY-MM-DD HH:mm:ss') : null
        });
      case 'monitor':
        return JSON.stringify(S.trigger_args.monitor.map(x => x[1]).filter(x => x))
      default:
        return S.trigger_args[S.trigger];
    }
  }

  function handleChange(ids) {
    if (S.targets.includes('local')) {
      ids.unshift('local')
    }
    S.targets = ids
  }

  return (
    <Container visible={props.visible} style={{width: isPanel ? '100%' : 420, margin: '0 auto'}}>
      <Form layout="vertical" style={{minHeight: 200}}>
        {isPanel ? <>
          <Alert showIcon type="info" message="保存后的计划默认未激活" description="请核对固定面板和操作范围。激活后，每次到点重新预检，全量通过后自动执行；失败或结果不确定时自动停用。"/>
          <div style={{margin: '16px 0'}}><PanelPlanSummary plan={S.record.panel_plan}/></div>
        </> : <><Form.Item required label={t('执行对象')}>
          {S.targets.map((id, index) => (
            <React.Fragment key={index}>
              <Select
                value={id}
                showSearch
                placeholder={t('请选择')}
                optionFilterProp="children"
                style={{width: 'calc(100% - 40px)', marginRight: 10, marginBottom: 12}}
                filterOption={(input, option) => option.props.children.toLowerCase().indexOf(input.toLowerCase()) >= 0}
                onChange={v => S.editTarget(index, v)}>
                <Select.Option value="local" disabled={!access.is_supper || S.targets.includes('local')}>{t('本机')}</Select.Option>
                {S.trigger === 'monitor' &&
                  <Select.Option value="monitor" disabled={S.targets.includes('monitor')}>{t('告警关联主机')}</Select.Option>}
                {hostStore.rawRecords.map(item => (
                  <Select.Option key={item.id} value={item.id} disabled={!access.canManage('host', item.id) || S.targets.includes(item.id)}>
                    {`${item.name}(${item['hostname']}:${item['port']})`}
                  </Select.Option>
                ))}
              </Select>
              {S.targets.length > 1 && (
                <MinusCircleOutlined className={styles.delIcon} onClick={() => S.delTarget(index)}/>
              )}
            </React.Fragment>
          ))}
        </Form.Item>
        <Form.Item extra={t('本机即平台服务运行所在的容器或主机。')}>
          <HostSelector value={S.targets.filter(x => x !== 'local')} onChange={handleChange}>
            <Button type="dashed" style={{width: 'calc(100% - 40px)'}} disabled={S.trigger === 'monitor'}>
              <PlusOutlined/>{t('添加执行对象')}
            </Button>
          </HostSelector>
        </Form.Item></>}
        <Form.Item>
          {!canTargets && <Alert type="warning" message="范围包含只读或未授权资源，或缺少具体动作权限；请调整选择。"/>}
          <Button loading={loading} disabled={!canTargets || (isPanel ? !validPanel : S.targets.filter(x => x).length === 0)} type="primary"
                  onClick={handleSubmit}>{t('提交')}</Button>
          <Button style={{marginLeft: 20}} onClick={() => S.page -= 1}>{t('上一步')}</Button>
        </Form.Item>
      </Form>
    </Container>
  )
})
