/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React, { useState, useEffect, useRef } from 'react';
import { observer } from 'mobx-react';
import { ExclamationCircleOutlined, UploadOutlined } from '@ant-design/icons';
import { Modal, Form, Input, TreeSelect, Button, Upload, Alert, message } from 'antd';
import { http, X_TOKEN, t } from 'libs';
import store from './store';
import styles from './index.module.less';
import {useResourceAccess} from 'resources/ResourceAccess';
import {canAssignGroups, canChangeResourceGroups, manageableGroupTree} from 'resources/groupAccess';

export default observer(function () {
  const access = useResourceAccess();
  const latestAccess = useRef(access);
  latestAccess.current = access;
  const passwordModal = useRef();
  const canChangeGroups = canChangeResourceGroups(access, 'host', store.record.id);
  const saveReason = saveBlockReason(access, store.record.id);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [fileList, setFileList] = useState([]);

  useEffect(() => () => {
    latestAccess.current = null;
    if (passwordModal.current) passwordModal.current.destroy();
  }, []);

  useEffect(() => {
    if (passwordModal.current) passwordModal.current.update({okButtonProps: {disabled: !!saveReason}, title: saveReason || t('首次验证请输入密码')});
  }, [saveReason]);

  useEffect(() => {
    if (store.record.has_pkey) {
      // 后端不再返回私钥原文，用 keep 标记沿用已保存的独立密钥
      setFileList([{uid: '0', name: t('独立密钥'), keep: true}])
    }
  }, [])

  function saveBlockReason(policy, id) {
    if (!policy) return t('表单已关闭，请重新打开后操作');
    if (!policy.has(id ? 'host.host.edit' : 'host.host.add')) return t(id ? '当前账号缺少编辑主机权限，无法保存' : '当前账号缺少新增主机权限，无法保存');
    if (id && !policy.canManage('host', id, 'host.host.edit')) return t('此主机已变为只读或未授权，无法保存');
    if (!id && !policy.canAdd) return t(policy.quota ? '服务器额度已用尽，无法新增主机，请联系管理员调整' : '服务器额度尚未加载，暂不能新增主机');
    return '';
  }

  function allowSave(formData) {
    const policy = latestAccess.current;
    const reason = saveBlockReason(policy, formData.id);
    if (reason) {message.error(reason); return false;}
    if (!canAssignGroups(store.treeData, formData.group_ids || [], policy, store.record.id ? store.record.group_ids || [] : [],
      {kind: 'host', id: formData.id})) {
      message.error(t('仅资源归属账号或管理员可选择可管理的分组；原分组可保留'));
      return false;
    }
    return true;
  }

  function handleSubmit() {
    const formData = {...form.getFieldsValue(), id: store.record.id};
    if (!allowSave(formData)) return;
    setLoading(true);
    const file = fileList[0];
    if (file && file.data) {
      formData['pkey'] = file.data;
    } else if (file && file.keep) {
      formData['keep_pkey'] = true;
    }
    http.post('/api/host/', formData)
      .then(res => {
        if (res === 'auth fail') {
          setLoading(false)
          if (formData.pkey || formData.keep_pkey) {
            message.error(t('独立密钥认证失败'))
          } else {
            if (!allowSave(formData)) return;
            const onChange = v => formData.password = v;
            passwordModal.current = Modal.confirm({
              icon: <ExclamationCircleOutlined/>,
              title: t('首次验证请输入密码'),
              content: <ConfirmForm username={formData.username} onChange={onChange}/>,
              okButtonProps: {disabled: false},
              onOk: () => handleConfirm(formData),
            })
          }
        } else {
          message.success(t('验证成功'));
          store.formVisible = false;
          store.fetchRecords();
          store.fetchExtend(res.id)
        }
      }, () => setLoading(false))
  }

  function handleConfirm(formData) {
    if (!allowSave(formData)) return;
    if (formData.password) {
      return http.post('/api/host/', formData)
        .then(res => {
          message.success(t('验证成功'));
          store.formVisible = false;
          store.fetchRecords();
          store.fetchExtend(res.id)
        })
    }
    message.error(t('请输入授权密码'))
  }

  const ConfirmForm = (props) => (
    <Form layout="vertical" style={{marginTop: 24}}>
      <Form.Item required label={t('授权密码')} extra={t('用户 {} 的密码， 该密码仅做首次验证使用，不会存储该密码。', props.username)}>
        <Input.Password onChange={e => props.onChange(e.target.value)}/>
      </Form.Item>
    </Form>
  )

  function handleUploadChange(v) {
    if (v.fileList.length === 0) {
      setFileList([])
    }
  }

  function handleUpload(file, fileList) {
    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    http.post('/api/host/parse/', formData)
      .then(res => {
        file.data = res;
        setFileList([file])
      })
      .finally(() => setUploading(false))
    return false
  }

  const info = store.record;
  return (
    <Modal
      open
      width={700}
      maskClosable={false}
      title={store.record.id ? t('编辑主机') : t('新建主机')}
      okText={t('验证')}
      onCancel={() => store.formVisible = false}
      confirmLoading={loading}
      okButtonProps={{disabled: !!saveReason}}
      onOk={handleSubmit}>
      {saveReason && <Alert showIcon type="warning" message={saveReason} style={{marginBottom: 16}}/>}
      <Form form={form} labelCol={{span: 5}} wrapperCol={{span: 17}} initialValues={info}>
        <Form.Item required name="group_ids" label={t('主机分组')}
                   extra={!canChangeGroups ? t('管理员分配的资源保留原分组；改组需由资源归属账号或管理员操作。') : undefined}>
          <TreeSelect
            multiple
            disabled={!canChangeGroups}
            treeNodeLabelProp="name"
            treeData={manageableGroupTree(store.treeData, access)}
            showCheckedStrategy={TreeSelect.SHOW_CHILD}
            placeholder={t('请选择分组')}/>
        </Form.Item>
        <Form.Item required name="name" label={t('主机名称')}>
          <Input placeholder={t('请输入主机名称')}/>
        </Form.Item>
        <Form.Item required label={t('连接地址')} style={{marginBottom: 0}}>
          <Form.Item name="username" className={styles.formAddress1} style={{width: 'calc(30%)'}}>
            <Input addonBefore="ssh" placeholder={t('用户名')}/>
          </Form.Item>
          <Form.Item name="hostname" className={styles.formAddress2} style={{width: 'calc(40%)'}}>
            <Input addonBefore="@" placeholder={t('主机名/IP')}/>
          </Form.Item>
          <Form.Item name="port" className={styles.formAddress3} style={{width: 'calc(30%)'}}>
            <Input addonBefore="-p" placeholder={t('端口')}/>
          </Form.Item>
        </Form.Item>
        <Form.Item label={t('独立密钥')} extra={t('默认使用全局密钥，如果上传了独立密钥（私钥）则优先使用该密钥。')}>
          <Upload name="file" fileList={fileList} headers={{'X-Token': X_TOKEN}} beforeUpload={handleUpload}
                  onChange={handleUploadChange}>
            {fileList.length === 0 ? <Button loading={uploading} icon={<UploadOutlined/>}>{t('点击上传')}</Button> : null}
          </Upload>
        </Form.Item>
        <Form.Item name="desc" label={t('备注信息')}>
          <Input.TextArea placeholder={t('请输入主机备注信息')}/>
        </Form.Item>
        <Form.Item wrapperCol={{span: 17, offset: 5}}>
          <Alert showIcon type="info" message={t('首次验证时需要输入登录用户名对应的密码，该密码会用于配置SSH密钥认证，不会存储该密码。')}/>
        </Form.Item>
      </Form>
    </Modal>
  )
})
