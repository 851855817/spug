/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React from 'react';
import { observer } from 'mobx-react';
import {Modal, Checkbox, Row, Col, message, Alert, Select, Button, Space} from 'antd';
import http from 'libs/http';
import { t } from 'libs';
import store from './store';
import codes from './codes';
import styles from './index.module.css';
import lds from 'lodash';
import { ROLE_PRESETS, buildPresetPermissions, presetSummary } from './presets';

class PagePerm extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      loading: false,
      preset: undefined,
    }
  }

  handleSubmit = () => {
    if (this.state.loading) return;
    this.setState({loading: true});
    http.patch('/api/account/role/', {id: store.record.id, page_perms: store.permissions})
      .then(res => {
        message.success(t('操作成功'));
        store.pagePermVisible = false;
        store.fetchRecords()
      }, () => this.setState({loading: false}))
  };

  handleAllCheck = (e, mod, page) => {
    const checked = e.target.checked;
    if (checked) {
      const key = `${mod}.${page}`;
      store.permissions[mod][page] = lds.clone(store.allPerms[key])
    } else {
      store.permissions[mod][page] = []
    }
  };

  handlePermCheck = (mod, page, perm) => {
    const perms = store.permissions[mod][page];
    if (perms.includes(perm)) {
      perms.splice(perms.indexOf(perm), 1)
    } else {
      perms.push(perm)
    }
  };

  PermBox = observer(({mod, page, perm, children}) => (
    <Checkbox
      value={perm}
      onChange={() => this.handlePermCheck(mod, page, perm)}
      checked={store.permissions[mod][page].includes(perm)}>
      {children}
    </Checkbox>
  ));

  render() {
    const PermBox = this.PermBox;
    const preset = ROLE_PRESETS.find(row => row.id === this.state.preset);
    const summary = preset && presetSummary(preset.id);
    return (
      <Modal
        open
        width={1000}
        maskClosable={false}
        title={t('功能权限设置')}
        className={styles.container}
        onCancel={() => store.pagePermVisible = false}
        confirmLoading={this.state.loading}
        onOk={this.handleSubmit}>
        <Alert
          closable
          showIcon
          type="info"
          style={{marginBottom: 12}}
          message={t('功能权限仅影响页面功能，管理应用的发布权限请在发布权限中设置。权限更改成功后会强制属于该角色的账户重新登录。')}/>
        <Space wrap style={{marginBottom: 12}}>
          <span>岗位模板</span>
          <Select aria-label="岗位权限模板" placeholder="选择员工岗位" value={this.state.preset} disabled={this.state.loading}
            style={{width: 190}} onChange={preset => this.setState({preset})}
            options={ROLE_PRESETS.map(row => ({value: row.id, label: row.name}))}/>
          <Button disabled={!preset || this.state.loading} onClick={() => {store.permissions = buildPresetPermissions(preset.id);}}>套用到当前草稿</Button>
        </Space>
        {preset && <Alert style={{marginBottom: 12}} type={summary.risks.length ? 'warning' : 'info'}
          message={`${preset.name} · ${summary.count} 项功能权限`} description={<>
            <div>{preset.description}</div><div>{preset.limits}</div>
            {summary.risks.length > 0 && <div>高风险权限：{summary.risks.join('、')}</div>}
            <details><summary>查看模板全部权限</summary>{summary.labels.map(label => <div key={label}>{label}</div>)}</details>
          </>}/>}
        <div style={{marginBottom: 12}}>套用只替换当前功能权限草稿，核对下方勾选后保存。SSH 主机组、发布应用和环境需另行授权。
          宝塔权限覆盖全部面板，不按 SSH 主机组隔离；网站与证书、运维作业、到期管理等仍仅超级管理员可用。</div>
        <table border="1" bordercolor="#dfdfdf" className={styles.table}>
          <thead>
          <tr>
            <th>{t('模块')}</th>
            <th>{t('页面')}</th>
            <th>{t('功能')}</th>
          </tr>
          </thead>
          <tbody>
          {codes.map(mod => (
            mod.pages.map((page, index) => (
              <tr key={page.key}>
                {index === 0 && <td rowSpan={mod.pages.length}>{mod.label}</td>}
                <td>
                  <Checkbox onChange={e => this.handleAllCheck(e, mod.key, page.key)}>
                    {page.label}
                  </Checkbox>
                </td>
                <td>
                  <Row>
                    {page.perms.map(perm => (
                      <Col key={perm.key} span={8}>
                        <PermBox mod={mod.key} page={page.key} perm={perm.key}>{perm.label}</PermBox>
                      </Col>
                    ))}
                  </Row>
                </td>
              </tr>
            ))
          ))}

          </tbody>
        </table>
      </Modal>
    )
  }
}

export default observer(PagePerm)
