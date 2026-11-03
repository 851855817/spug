export function panelPlanReady(plan) {
  return Boolean(plan?.template_id && plan.template_token && plan.panels?.length &&
    (plan.required_acknowledgements || []).every(item => (plan.acknowledgements || []).includes(item.code)));
}

export function panelPlanPayload(plan) {
  return {template_id: plan.template_id, template_token: plan.template_token,
    acknowledgements: [...(plan.acknowledgements || [])]};
}

export function validPanelTrigger(trigger, args) {
  switch (trigger) {
    case 'interval': return ['string', 'number'].includes(typeof args.interval) &&
      Number.isInteger(Number(args.interval)) && Number(args.interval) >= 300;
    case 'date': return Boolean(args.date);
    case 'cron': return Boolean(args.cron?.rule?.trim());
    default: return false;
  }
}
