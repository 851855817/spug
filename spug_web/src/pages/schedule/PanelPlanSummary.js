import React from 'react';
import { Checkbox, Typography } from 'antd';

export default function PanelPlanSummary({plan, onChange}) {
  if (!plan) return null;
  const acknowledged = plan.acknowledgements || [];
  return <div style={{overflowWrap: 'anywhere'}}>
    <Typography.Paragraph strong>{plan.operation_label || plan.operation} · 固定 {(plan.panels || []).length} 个面板</Typography.Paragraph>
    <Typography.Paragraph>{(plan.panels || []).map(panel => `${panel.name} (#${panel.id})`).join('、')}</Typography.Paragraph>
    {(plan.scope || []).map((scope, index) => <div key={`${scope.panel_id}:${index}`} style={{marginBottom: 12}}>
      <Typography.Text strong>{scope.name || `面板 #${scope.panel_id}`}</Typography.Text>
      <ul style={{paddingLeft: 20}}>{(scope.items || []).map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul>
    </div>)}
    {(plan.required_acknowledgements || []).map(item => <div key={item.code} style={{marginBottom: 12}}>
      {onChange ? <Checkbox checked={acknowledged.includes(item.code)} onChange={event => onChange({...plan,
        acknowledgements: event.target.checked ? [...acknowledged, item.code] : acknowledged.filter(code => code !== item.code)})}>{item.label}</Checkbox> :
        <Typography.Text>{acknowledged.includes(item.code) ? '已确认：' : '待确认：'}{item.label}</Typography.Text>}
    </div>)}
  </div>;
}
