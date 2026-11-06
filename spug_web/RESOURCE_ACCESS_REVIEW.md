# Frontend resource permission review

Task 6 implementation is frozen for independent review. Only spug_web files were changed by this implementation agent; no backend edits, commits, publication, or customer-server calls were performed.

Behavior:
- Load current effective permissions and resource levels before mounting authenticated pages; refresh on focus and quota-affecting resource writes. API failures hide the workspace and offer retry.
- Account policy supports an explicit zero, integer cap, or null/unlimited. Editing unchanged policy fields omits replacements. Current persisted effective feature codes and host/panel levels are available in the account editor.
- Shared quota appears in account rows, host/panel lists, and personal settings. New records cannot be added when quota is exhausted; editing is independent.
- Resource writes require the independent feature code and backend effective manage scope. Mixed batch targets remain visible and disable the write, with an explanation.
- Role catalog and five presets expose independent SSH, panel login/test/actions, server/SSL monitoring, inventory, jobs, expiry and schedule run/enable permissions. Existing legacy monitor-task permissions remain separate.
- Backend-supplied group and schedule can_manage flags, and SSL job source_keys, bound their write controls. Hidden panel groups may be preserved during edit.
- Template edit/delete respects created_by_id ownership; public administrator templates remain readable. Effective administrators retain global writes.
- Host detail synchronization requires host.host.verify and resource manage independently of host.host.edit; revoking scope disables saves and synchronization.
- Host/panel editors, group move/copy, and Excel/cloud imports disable readonly groups while keeping their writable descendants reachable. Existing groups are preserved on edit, and submitted destination groups are checked again.
- Changing existing host/panel groups additionally requires effective source=owned or administrator status. Assigned managers can save unchanged visible groups or omit hidden groups. Single and mixed batch move/copy retain assigned selections and block the whole request; missing source does not grant ownership.
- Open host/panel forms disable saving and explain current edit/add scope or quota failures. Submit, asynchronous panel validation and static host password confirmation recheck a live permission ref before sending; unmounted forms invalidate delayed callbacks. Panel connection/certificate handlers independently require test permission and resource manage scope.
- Public-template and execution-history targets remain selected when readonly. Task execution checks all host targets; file distribution checks every source and destination, including after effective policy refreshes. File-distribution-only accounts have a batch menu entry.
- Successful cloud imports refresh effective permissions and shared quota. The saved effective preview labels backend source values as owned or administrator-assigned independently of explicit grants.

Validation actually run:
- TDD red/green: effective scope/feature intersection, zero/null/policy omission, effective-provider loading, account policy editor, panel read-only and mixed batch handling, independent role catalog, schedule run/enable versus edit and read-only resources, renewal-only panel action.
- Review-correction TDD red/green: template ownership, verify/edit separation, group assignment and import guards, owned/assigned preview labels, real cloud-import refresh path, public-template task targets, transfer source/target checks and transfer-only menu visibility.
- Owner-only group follow-up: meaningful red demonstrated four unauthorized frontend submissions (host edit, panel edit, single move, mixed batch move). Seventeen added behavior tests cover assigned-manager denial, owner/admin allowance, creation, preserving visible/hidden groups, retained-subset changes, and missing-source denial.
- Active-form permission follow-up: twenty behavior tests cover live manage-to-read changes, missing add/edit codes, exhausted creation quota, asynchronous validation, saved confirmation callbacks, unmount cancellation, independent panel tests, and retained assigned-resource saves.
- Full Jest after review corrections, owner-only group and active-form follow-ups: CI=true; D:/nodejs/node.exe node_modules/react-app-rewired/bin/index.js test --watchAll=false --runInBand. Exit 0; 62 suites and 584 tests passed.
- Production build: NODE_OPTIONS=--openssl-legacy-provider; GENERATE_SOURCEMAP=false; D:/nodejs/node.exe node_modules/react-app-rewired/bin/index.js build. Exit 0.
- git diff --check: exit 0.

Remaining verification limits:
- The build reports two existing hook-dependency warnings: deploy/app/AutoDeploy.js isAdmin and server-monitor/index.js monitor. No new resource-access warnings remain.
- Browser visual review, a real administrator/ordinary-user click-through, and live customer-server execution were not performed. Jest checks rendered behavior using local fixtures. Backend authorization remains authoritative.
- The account preview shows the currently saved effective policy. Role/grant draft changes are labeled as requiring save and reopen to inspect the newly saved backend result.

Local evidence logs (untracked, do not commit): .resource-ui-jest-final.txt and .resource-ui-build-final.txt.

Changed frontend source files:

- spug_web/src/App.js
- spug_web/src/components/ResourceMonitorButton.js
- spug_web/src/components/ResourceMonitorButton.test.js
- spug_web/src/components/ResourceMonitorDetail.js
- spug_web/src/libs/http.js
- spug_web/src/libs/http.test.js
- spug_web/src/pages/baota/Group.js
- spug_web/src/pages/baota/index.js
- spug_web/src/pages/baota/index.test.js
- spug_web/src/pages/baota/PanelForm.js
- spug_web/src/pages/baota/PanelForm.test.js
- spug_web/src/pages/baota/helpers.js
- spug_web/src/pages/exec/operations/index.js
- spug_web/src/pages/exec/operations/index.test.js
- spug_web/src/pages/exec/operations/RunDetail.js
- spug_web/src/pages/exec/operations/RunForm.js
- spug_web/src/pages/exec/panel/index.js
- spug_web/src/pages/exec/panel/index.test.js
- spug_web/src/pages/exec/panel/TaskDetail.js
- spug_web/src/pages/exec/panel/TaskDetail.test.js
- spug_web/src/pages/exec/panel/TaskForm.js
- spug_web/src/pages/exec/panel/TaskForm.test.js
- spug_web/src/pages/exec/panel/UploadResults.js
- spug_web/src/pages/exec/panel/UploadResults.test.js
- spug_web/src/pages/exec/task/index.js
- spug_web/src/pages/exec/task/index.test.js
- spug_web/src/pages/exec/template/Table.js
- spug_web/src/pages/exec/template/Table.test.js
- spug_web/src/pages/exec/transfer/index.js
- spug_web/src/pages/exec/transfer/index.test.js
- spug_web/src/pages/home/Todo.js
- spug_web/src/pages/host/Detail.js
- spug_web/src/pages/host/Detail.test.js
- spug_web/src/pages/host/Form.js
- spug_web/src/pages/host/Import.js
- spug_web/src/pages/host/CloudImport.js
- spug_web/src/pages/host/Group.js
- spug_web/src/pages/host/index.js
- spug_web/src/pages/host/Selector.js
- spug_web/src/pages/host/Table.js
- spug_web/src/pages/host/Table.test.js
- spug_web/src/pages/panel-inventory/index.js
- spug_web/src/pages/panel-inventory/index.test.js
- spug_web/src/pages/schedule/ScheduleForm.test.js
- spug_web/src/pages/schedule/Step3.js
- spug_web/src/pages/schedule/Table.js
- spug_web/src/pages/schedule/Table.test.js
- spug_web/src/pages/server-expiry/Editor.js
- spug_web/src/pages/server-expiry/index.js
- spug_web/src/pages/server-expiry/index.test.js
- spug_web/src/pages/server-expiry/Reminders.js
- spug_web/src/pages/server-monitor/index.js
- spug_web/src/pages/server-monitor/index.test.js
- spug_web/src/pages/server-monitor/ResourceCard.js
- spug_web/src/pages/ssh/index.js
- spug_web/src/pages/ssl-monitor/index.js
- spug_web/src/pages/ssl-monitor/index.test.js
- spug_web/src/pages/ssl-monitor/ReminderEntry.js
- spug_web/src/pages/ssl-monitor/Reminders.js
- spug_web/src/pages/ssl-monitor/Renewal.js
- spug_web/src/pages/ssl-monitor/SourceSettings.js
- spug_web/src/pages/system/account/Form.js
- spug_web/src/pages/system/account/ResourcePolicy.js
- spug_web/src/pages/system/account/ResourcePolicy.test.js
- spug_web/src/pages/system/account/Table.js
- spug_web/src/pages/system/role/codes.js
- spug_web/src/pages/system/role/PagePerm.js
- spug_web/src/pages/system/role/presets.js
- spug_web/src/pages/system/role/presets.test.js
- spug_web/src/pages/welcome/info/index.js
- spug_web/src/resources/access.js
- spug_web/src/resources/access.test.js
- spug_web/src/resources/groupAccess.js
- spug_web/src/resources/groupPickers.test.js
- spug_web/src/resources/resourceForms.test.js
- spug_web/src/resources/ResourceAccess.js
- spug_web/src/resources/ResourceAccess.test.js
- spug_web/src/routes.js
- spug_web/src/routes.test.js
