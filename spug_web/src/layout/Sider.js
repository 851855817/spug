import React, { useRef, useState } from 'react';
import { Button, Layout, Menu } from 'antd';
import { HolderOutlined } from '@ant-design/icons';
import { hasPermission, history } from 'libs';
import styles from './layout.module.less';
import routes from '../routes';
import logo from './logo-spug-white.png';
import { moveChild, navigationOrderKey, orderChildren, readNavigationOrder } from './navigationOrder';

let selectedKey = window.location.pathname;
const OpenKeysMap = {};
for (let item of routes) {
  if (item.child) {
    for (let sub of item.child) {
      if (sub.title) OpenKeysMap[sub.path] = item.title
    }
  } else if (item.title) {
    OpenKeysMap[item.path] = 1
  }
}

export default function Sider(props) {
  const [openKeys, setOpenKeys] = useState([]);
  const [storageKey] = useState(navigationOrderKey);
  const [orders, setOrders] = useState(() => readNavigationOrder(storageKey));
  const [editing, setEditing] = useState(false);
  const [dropPath, setDropPath] = useState('');
  const [saveError, setSaveError] = useState(false);
  const dragged = useRef(null);

  function save(next) {
    setOrders(next);
    try {localStorage.setItem(storageKey, JSON.stringify(next)); setSaveError(false);}
    catch (_) {setSaveError(true);}
  }

  function handleRoute(item, group, siblings) {
    if (item.auth && !hasPermission(item.auth)) return
    if (!item.title) return;
    const menu = {label: item.title, key: item.path || item.title, icon: item.icon}
    if (item.child) {
      const childGroup = item.child.find(child => child.path)?.path.split('/')[1];
      const children = orderChildren(item.child.filter(child => child.title && (!child.auth || hasPermission(child.auth))), orders[childGroup]);
      menu.children = children.map(child => handleRoute(child, childGroup, children.map(child => child.path)));
    } else if (group) {
      const enabled = editing && !props.collapsed;
      const clear = () => {dragged.current = null; setDropPath('');};
      menu.label = <span data-nav-path={item.path} className={`${styles.navLabel} ${dropPath === item.path ? styles.navDrop : ''}`}
        draggable={enabled} tabIndex={enabled ? 0 : undefined} aria-label={enabled ? `调整${item.title}顺序` : undefined}
        onDragStart={event => {
          if (!enabled) {event.preventDefault(); return;}
          dragged.current = {group, path: item.path}; event.dataTransfer.effectAllowed = 'move';
          event.dataTransfer.setData('text/plain', item.path);
        }}
        onDragOver={event => {if (enabled && dragged.current?.group === group) {event.preventDefault(); setDropPath(item.path);}}}
        onDragEnd={clear}
        onDrop={event => {
          if (enabled && dragged.current?.group === group) {
            event.preventDefault(); event.stopPropagation();
            save({...orders, [group]: moveChild(siblings, dragged.current.path, item.path)});
          }
          clear();
        }}
        onKeyDown={event => {
          if (!enabled || !event.altKey || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
          event.preventDefault(); event.stopPropagation();
          const index = siblings.indexOf(item.path), to = index + (event.key === 'ArrowUp' ? -1 : 1);
          if (to < 0 || to >= siblings.length) return;
          const next = [...siblings]; [next[index], next[to]] = [next[to], next[index]];
          save({...orders, [group]: next});
        }}>
        {enabled && <HolderOutlined/>}{item.title}
      </span>;
    }
    return menu
  }
  const menus = routes.map(item => handleRoute(item)).filter(Boolean);

  const tmp = window.location.pathname;
  const openKey = OpenKeysMap[tmp];
  if (openKey) {
    selectedKey = tmp;
    if (openKey !== 1 && !props.collapsed && !openKeys.includes(openKey)) {
      setOpenKeys([...openKeys, openKey])
    }
  }
  return (
    <Layout.Sider width={208} collapsed={props.collapsed} className={styles.sider}>
      <div className={styles.logo}>
        <img src={logo} alt="Logo"/>
      </div>
      {!props.collapsed && <div className={styles.navTools}>
        <Button type="text" size="small" onClick={() => {dragged.current = null; setDropPath(''); setEditing(value => !value);}}>{editing ? '完成' : '调整导航'}</Button>
        {editing && <Button type="text" size="small" onClick={() => save({})}>恢复默认</Button>}
        {editing && <small>拖动子栏目排序 · Alt＋↑/↓</small>}
        {saveError && <small role="alert">未能保存，当前顺序仅本次有效</small>}
      </div>}
      <div className={styles.menus} style={{height: `calc(100vh - ${props.collapsed ? 64 : editing ? 140 : 104}px)`}}>
        <Menu
          theme="dark"
          mode="inline"
          items={menus}
          className={styles.menus}
          selectedKeys={[selectedKey]}
          openKeys={openKeys}
          onOpenChange={setOpenKeys}
          onSelect={menu => {if (!editing || props.collapsed) history.push(menu.key);}}/>
      </div>
    </Layout.Sider>
  )
}
