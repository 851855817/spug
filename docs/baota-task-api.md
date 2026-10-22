# Durable panel task API

All routes use the existing `X-Token` authentication and `{data, error}` response envelope. Application errors return HTTP 200 with a nonempty `error`. Task records are visible to their owner; `exec.panel.manage` permits access to other owners' records. Every command still requires its own permission; manage does not grant execution.

## Routes

| Method and path | Permission | Request / response |
| --- | --- | --- |
| `GET /baota/task-options/` | create or view | `{panels,groups,operations,limits,acknowledgements}`; panel fields: id, name, group_id, group_name, api_configured, api_checked, api_version, api_config_revision. No login URL, username, password, key or CA material. |
| `GET /baota/resources/` | create or view | Query `panel_id`, `kind` and only the selector required by that kind; selected resource metadata. |
| `GET /baota/catalog/` | create or view | Optional q, category, page (default 1), page_size (default 50, max 100). Returns total, catalog_total=374, categories, items, read_only=true. All 373 modern and one legacy official entries are searchable metadata; these entries cannot execute. |
| `POST /baota/tasks/` | create | `{operation,panel_ids?:[int],group_ids?:[int],params:object}` OR `{operation,panel_ids?,group_ids?,params_by_panel:{"panelID":object}}`. Parameters are exclusive alternatives. Mapping keys must exactly match the deduplicated selected panels, including group descendants. Returns task immediately in preflight_queued. |
| `GET /baota/tasks/` | view | Optional page, page_size (default 20, max 100), operation, state. Returns `{items,total,page,page_size}`. |
| `GET /baota/tasks/<id>/` | view | Task with immutable target snapshots and frozen child preview, current results and dispatch intents. |
| `POST /baota/tasks/<id>/start/` | execute | **Only** `{acknowledgements:[code]}`. Codes must exactly equal required_acknowledgements from the frozen preview. No parameters or target selectors may be submitted. |
| `POST /baota/tasks/<id>/cancel/` | cancel | `{}`. Cancels only unsent children. Does not claim that a remote operation stopped. |
| `POST /baota/tasks/<id>/reconcile/` | view | `{}`. Schedules bounded readonly checks for remote/uncertain targets; never resends a mutation. |
| `POST /baota/tasks/<id>/resolve/` | manage | `{target_id,child_key,outcome:"success"|"failed",reason,evidence}`. Reason and evidence are nonempty strings, max 2000 characters. Rejects active calls. Preserves intents and appends resolution/audit history. |
| `POST /baota/tasks/<id>/retry/` | create | `{}`. Creates a new linked task with a new preflight. The previous uncertain target's lock remains authoritative. |

PanelView's existing `/baota/` list permissions are unchanged. Task selection uses the separate safe endpoint above.

## Task detail

Task fields: id, operation, owner_id, state, retry_parent_id, cancel_requested, acknowledgements, required_acknowledgements, created_at, updated_at, audit, targets.

Each target contains id, panel (the frozen safe panel fields above), state, message, summary (frozen preflight counts), children. A child is `{snapshot,state,intents,result,resolutions,...}`. `snapshot` has key, label, target, before, plan, state, message. It is the server's immutable preflight plan, including any broad side effects and SSL inventory caveat. `state` outside the snapshot is the current state. `result` contains only state, message, evidence and optional remote metadata. An intent has phase (`apply` or `deploy`), at, status, api_config_revision and, after a returned call, returned_at/outcome. Never send any snapshot/result back to an execution endpoint.

Task states: preflight_queued, prepared, running, remote, uncertain, cancelled, completed. Target states additionally include preflighting, queued, dispatching, reconciling, failed. Child states: ready, already, skipped, failed, dispatching, remote, uncertain, success, cancelled. `completed` means all targets ended; inspect child results for failures. Preflight errors remain visible on individual targets. A task containing valid prepared targets can start even if another target failed preflight.

Poll detail while preflight_queued. Render the frozen children once prepared, including already/skipped/failed items, before permitting Start. The current all-sites list must not replace or expand the frozen snapshot. Prepare never authorizes a mutation. After start, poll current child states; remote and uncertain are not success. Uncertainty retains the panel lock until explicit administrator resolution, even if a later read produces reassuring evidence. File-only rewrite evidence always remains uncertain because runtime activation is unverified.

SSL ready children require `ssl_panel_sync`. Display the acknowledgement explanation returned by task-options, frozen `plan.ssl_scope`, and `before.ssl_sync_inventory` (up to 200 registered sites, always incomplete). The initial ACME call may sync certificates panel-wide, including unselected sites, orphan copies and panel TLS, reload Web services, and temporarily toggle redirect settings during renewals. A later selected-site deployment has a separate durable intent. Public SSL configuration readback does not verify every affected live TLS endpoint. A changed panel TLS pin is never automatically trusted.

9.2 stable upgrades additionally require `legacy_panel_upgrade`; PHP updates require `php_update_restart`. These codes live in each frozen ready child's `plan.required_acknowledgements`, are aggregated by the server and must be confirmed exactly at Start. The 9.2 upgrade checks a candidate immediately before its one dispatch, but the background official updater controls the eventual version; an unsupported/different version remains uncertain. PHP updates use the official compile path only and cannot pin the online script's patch. Success requires an unchanged installation-directory identity, uniquely correlated completed queue and exact expected CLI version. This is not FPM health verification; ambiguous delivery or any queue collision retains uncertainty.

## Operation parameters and resources

Only these ten codes in nine categories execute. Validation rejects unknown keys. Site selectors are either `{sites:[id,...]}` or `{all_sites:true}`. IDs, software versions and extension identifiers belong to the individual target panel; use params_by_panel when they differ.

| Code | Parameters | Resource kinds |
| --- | --- | --- |
| panel_upgrade | 9.2: `{channel:"stable"}`; modern: `{version}` | upgrade_versions |
| php_update | `{php_version,m_version,min_version,install_type?:"0"}` | php_updates |
| site_add | `{entries:[{domains:[host,...],path,php_version,port?:80,remark?:""},...]}` | sites, php_versions |
| site_basedir | site selector + `{enabled:boolean}` | sites, webserver |
| site_rewrite | site selector + `{mode:"custom"|"template"|"clear",webserver:"nginx"|"apache",rules?:string,template?:name}` | sites, webserver, site_options |
| ssl_apply | site selector + optional `{domains:{"siteID":[host,...]}}` | sites, site_options |
| ssl_renew | same as ssl_apply | sites, site_options |
| software_install | `{name,m_version,min_version,install_type?:"0"|"1"}` | software |
| php_extension_install | `{php_version,extensions:[name,...],install_type?:"0"|"1"}` | php_versions, php_extensions, webserver |
| software_uninstall | `{name}` | software |

Resource kinds are fixed: sites, software, php_versions, php_updates, webserver, upgrade_versions require no extra selector. php_extensions requires php_version; site_options requires site_id. Other selectors are rejected. Every response includes compatibility information. site_options returns domains, rewrite template names and public SSL metadata; never a caller-controlled path/action. Reviewed Linux source versions 9.2.0, 11.7.0 and 13.1.0 are eligible for the corresponding adapter contracts. In 9.2, SSL additionally reads the fixed main Nginx/Apache configuration and canonical public certificate. Standard includes remain uninspected, so evidence distinguishes main configuration from complete configuration and never asserts live TLS verification. Unknown/custom certificate directives or paths are blocked. Software resources expose title, installed state, available versions and install_supported; only free type=5 asynchronous core software can be installed. Rewrite discovery uses singular GetRewriteList and readonly file hashes: missing target rule files are unavailable/failed, and resource/preflight/reconcile never create them. Template text is fetched through a fixed readonly download scope, hash checked and frozen before Start. GetRewriteLists/GetFileBody are excluded because they can create missing files, including public-certificate filenames whose domain contains `rewrite`. Before official run-path queries, readonly attributes must verify the website root exists and is a real directory. Missing roots fail without creating directories; Baota does not offer atomic protection against an administrator deleting a root between the attribute and run-path calls.

Limits: 50 unique panels, 200 children across the whole task, 1 MiB input/validated parameters/preflight JSON and 4 MiB runtime child metadata per target. The preflight budget covers all panels' stored child envelopes and summaries together; each completion checks it under the task lock. An overflowing target fails with no children and asks for a smaller batch, preserving previously frozen targets. Start checks the aggregate again before authorization. Custom rewrite rules are additionally capped at 64 KiB. Configuration failures, unsupported versions and dependency blockers appear in preflight target/child rows. A changed API configuration invalidates starting or sending the next phase.

For a mixed batch, a panel independently known from saved configuration to lack API setup/connection verification, or whose saved version is outside the exact supported versions, may use an **exactly empty** parameter object. The server immediately records that target as failed with its prerequisite reason, zero children and no worker claim or panel lock. Other targets still validate normally. All-failed tasks end as `completed`, meaning ended rather than successful. Nonempty invalid objects and unknown fields are always rejected; a ready panel cannot use this exception. A later connection repair cannot make the failed target runnable. Retrying such a task after prerequisites are fixed requires refilling operation parameters through a new preflight. Network/resource lookup failures on otherwise ready panels do not qualify for this exception.

## Worker

Run `python manage.py runpanelworker`, optionally `--parallel 1|2` (default 2), `--lock-file /data/spug/baota-worker.lock`, or `--once` for one recovery/queue step. Deploy one coordinator with a shared stable lock file. The process lock excludes a second coordinator; database PanelLock's unique panel key serializes operations and preserves remote/uncertain ownership across crashes. Transactions cover claim/save only; network calls run outside transactions and outside Django ATOMIC_REQUESTS.

SIGTERM stops claims and the next HTTP request after the current bounded request completes. Interrupted readonly preflight is requeued. Unknown exceptions after a durable intent become redacted uncertainty. On startup, interrupted dispatch/reconcile and previously remote work become uncertain; no pending conditional deployment resumes automatically. Explicit readonly reconcile is available. Polling is bounded to 900 seconds or 180 checks; timeout becomes uncertain and keeps the lock. Failure of a worker future stops the coordinator and exits nonzero for supervisor restart. No queue is wiped. SQLite runs sequentially; the MariaDB integration test is the concurrency authority.

No runtime migrations are committed. Generate/apply them using the deployment's existing volume migration flow. The official catalogue is packaged at `apps/baota/data/official_api_catalog.json`, copied byte-for-byte from the researched source so Docker does not need the docs tree or a network documentation fetch.
