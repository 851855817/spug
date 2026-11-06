import { panelPlanReady, panelPlanPayload, validPanelTrigger } from './panelPlan';

const plan = {template_id: 7, template_token: 'signed-template', operation: 'file_upload',
  panels: [{id: 2, name: 'A'}], required_acknowledgements: [{code: 'upload_overwrite', label: '确认覆盖'}],
  acknowledgements: []};

test('a frozen plan requires every explicit impact acknowledgement before saving', () => {
  expect(panelPlanReady(plan)).toBe(false);
  expect(panelPlanReady({...plan, acknowledgements: ['upload_overwrite']})).toBe(true);
  expect(panelPlanReady({...plan, template_token: '', acknowledgements: ['upload_overwrite']})).toBe(false);
  expect(panelPlanReady(null)).toBe(false);
});

test('only the server template identity and acknowledgements are submitted', () => {
  expect(panelPlanPayload({...plan, acknowledgements: ['upload_overwrite'], command: 'private definition', params: {secret: 'hidden'}}))
    .toEqual({template_id: 7, template_token: 'signed-template', acknowledgements: ['upload_overwrite']});
});

test('panel schedules reject alert triggers and intervals shorter than five minutes', () => {
  expect(validPanelTrigger('monitor', {monitor: [[1, 2]]})).toBe(false);
  expect(validPanelTrigger('interval', {interval: 299})).toBe(false);
  expect(validPanelTrigger('interval', {interval: 300})).toBe(true);
  expect(validPanelTrigger('interval', {interval: '300'})).toBe(true);
  expect(validPanelTrigger('interval', {interval: 300.5})).toBe(false);
  expect(validPanelTrigger('cron', {cron: {rule: '0 1 * * *'}})).toBe(true);
  expect(validPanelTrigger('date', {date: '2027-01-01'})).toBe(true);
});
