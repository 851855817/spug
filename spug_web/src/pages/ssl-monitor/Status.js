import React from 'react';
import { tone } from './helpers';
import styles from './index.module.less';

export default function Status({state, children}) {
  return <span className={`${styles.status} ${styles[tone(state)]}`}><i/>{children}</span>;
}
