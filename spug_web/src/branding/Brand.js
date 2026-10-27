// KOps · 云枢 branding by K, 2026-10-27. Released under AGPL-3.0.
import React from 'react';
import styles from './branding.module.css';

export default function Brand({compact = false, large = false, light = false}) {
  return <span className={`${styles.brand} ${large ? styles.large : ''} ${light ? styles.light : ''}`}
    aria-label="KOps · 云枢" title="KOps · 云枢">
    <svg viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <rect width="48" height="48" rx="13" fill="#2563eb"/>
      <path d="M14 12v24M17 24l15-12M17 24l15 12" fill="none" stroke="white" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round"/>
      <circle cx="34" cy="12" r="4" fill="#5eead4"/>
      <circle cx="34" cy="36" r="4" fill="#5eead4"/>
    </svg>
    {!compact && <span>KOps <span className={styles.dot}>·</span> 云枢</span>}
  </span>;
}
