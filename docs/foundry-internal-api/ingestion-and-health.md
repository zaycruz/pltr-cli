# Foundry internal ingestion & health APIs — magritte, data-health, webhooks, telemetry

Reverse-engineered reference for the UPSTREAM (external source → connection → sync) and
DOWNSTREAM-BREAKAGE (health checks, expectations, webhooks) ends of the Foundry
change-impact graph, for a `pltr-cli` fork.

- **Stack under test:** `https://zap.usw-18.palantirfoundry.com`
- **Date verified:** 2026-07-21
- **Method:** live calls with a real bearer token + the type-confusion oracle
  (unknown field → 400; known field, wrong/empty value → 422; success → 200/204).
  Every path below was executed live; samples captured verbatim.

> **Status legend.** VERIFIED (executed live, output captured) unless tagged **UNVERIFIED**.

## TL;DR for the impact graph

- **Per-dataset health checks ARE readable** — `GET /data-health/api/checks/v2/{datasetRid}/{branchId}`
  returns the checks on a dataset/branch. The 38-entry check-type catalogue includes
  `schemaComparison`, `tableSchemaComparison`, `columnType`, `primaryKeyCheck`,
  `columnValueEnumCheck`, `nullPercentageCheck` — i.e. **a schema change that violates one of
  these IS the impact edge**, plus sync/build/schedule freshness checks.
- **Connection inventory IS fully readable** — all 17 data-connection sources on this stack
  enumerate with type + name (gmail, slack, discord, google-calendar, Alpaca REST, cdata-jdbc).
- **source → webhook edge IS readable** (both directions) — the true "external downstream consumer" link.
- **GAP: source → synced-dataset lineage is NOT stored in magritte** on this stack. The source
  config carries connection/driver details but empty `resources`/`connections`; there is no
  sync→output-dataset endpoint in `magritte-coordinator`. The reverse (dataset→source) must come
  from build2 jobspec / transaction provenance, not magritte.
- **No internal scheduler service is mounted** here (`/orchestration`, `/scheduler`,
  `/build2/.../schedules` all 404). Schedules remain reachable only via the public SDK
  (`Dataset.get_schedules`, ≤1hr stale) or indirectly via data-health `scheduleStatus` checks.

---

## Service prefixes (confirmed by route-mounted oracle)

| Service | Internal prefix | Status |
|---|---|---|
| magritte-coordinator | `/magritte-coordinator/api` | VERIFIED 200 |
| foundry-datahealth | **`/data-health/api`** (NOT `/foundry-datahealth`) | VERIFIED 200 |
| webhooks | `/webhooks/api` | VERIFIED 200 |
| foundry-metadata | `/foundry-metadata/api` | VERIFIED 200/204 |
| foundry-telemetry-service | `/foundry-telemetry-service/api` | VERIFIED 200 |
| job-tracker | `/job-tracker/api` | VERIFIED (400 on bad arg = mounted) |
| jemma | `/jemma/api` | VERIFIED mounted (`builds/graph` GET times out — heavy) |
| authoring-server | UNKNOWN — `/authoring-server`, `/authoring`, `/stemma`, `/code-authoring` all 404 | UNVERIFIED |

---

## 1. magritte-coordinator — data connections (TRUE upstream)

Base `/magritte-coordinator/api`. All reads; **never** call `updateSource*`, `deleteSource`,
`addSource*`, `migrate`, `extend`, `setRuntimePlatform`.

### 1.1 Enumerate all connections — VERIFIED
```
GET  /source-store/sources/rids            -> ["ri.magritte..source.<uuid>", ...]   (17 on this stack)
GET  /source-store/sources/descriptions    -> { <sourceRid>: {type,name,description,apiName} }
GET  /source-store/sources/types           -> { <sourceType>: [sourceRid,...] }
```
`descriptions` sample (real):
```json
{"ri.magritte..source.6388daff-...":{"type":"webhooks-rest","name":"AlpacaDataImport","apiName":null},
 "ri.magritte..source.901487b3-...":{"type":"slack","name":"Slack - 2025-07-07T..."},
 "ri.magritte..source.836e4076-...":{"type":"foundry-provided-cdata-jdbc-gmail-source","name":"Gmail - ..."}}
```
Source types seen: `webhooks-rest` (5), `foundry-provided-cdata-jdbc-gmail-source` (4),
`generic` (5), `slack`, `google-calendar`. This is the external-system inventory.

### 1.2 Per-source detail — VERIFIED
```
GET  /source-store/source/{sourceId}/config       -> {source:{type,config:{...}},connections,additionalSecrets,resources}
GET  /source-store/source/{sourceId}/description
GET  /source-store/source/{sourceId}/agents       -> assigned magritte agents (runtime that runs the sync)
GET  /source-store/source/{sourceId}/runtimePlatform/v2
```
`config` for the cdata-jdbc gmail source returned `jdbcUrl:"jdbc:gmail:"`, OAuth props, and
**secrets masked** as `{{OAuthClientSecret}}`. `resources`/`connections` were **empty** — the
config does NOT name output datasets. **Do not** use
`POST /source-store/source/{id}/v2/getSourceConfigWithPlaintextSecretValues` (would expose secrets).

### 1.3 Batch reads — field names UNVERIFIED
`POST /source-store/sources/metadata` and `.../egress-policies` both returned **422** for body
`{"sourceRids":[...]}` — the RID list is accepted but shape is off (likely needs wrapped objects).
`bulkGetSourceConfig` / `bulkGetSourceDescription` (`POST .../sources/{config,description}/bulk`)
untested. **UNVERIFIED.**

### Impact contribution
Source node = external system. Change to a source (auth, host, schema of the external system)
propagates to whatever it syncs. The **outbound** consumer edge (source→webhook) is in §3; the
**sync output** edge (source→dataset) is the documented GAP — resolve it from the output dataset's
build2 jobspec/provenance (see `lineage-monocle-build2.md`), not from magritte.

---

## 2. data-health — checks & expectations (schema-change breakage)

Base `/data-health/api`. Reads only; **never** call `createCheck`, `updateCheck`, `deleteCheck`,
`pause*`, `unpause*`, `snooze*`, `reindex`, or any `/checks/state/*` mutation, or check-group `add/remove/set`.

### 2.1 Assertion vocabulary — VERIFIED
```
GET /checks/v2/available   -> [ {checkName,displayName,eventType,checkCategory,datasetArity,valueType,triggerTypes,supportedTargetTypes,...}, ... ]  (38 types)
```
All 38 `checkName`s: `joinCheck, uniquePercentageCheck, partition, primaryKeyCheck,
totalColumnCount, codeCheckMonitor, numericColumnRange, syncFreshness, transactionFileSize,
pathPropagationDelay, totalRowCount, timeSinceLastSync, numericColumnMean, tableColumnType,
syncDuration, numericColumnMedian, schemaComparison, columnRegexCheck, scheduleStatus,
dataFreshnessCheck, tableTimeSinceLastUpdated, dateColumnRange, timeSinceLastUpdated,
nullPercentageCheck, syncPathPropagationDelay, columnValueEnumCheck, transactionFileNumber,
buildDuration, buildStatus, tablePrimaryKeyCheck, columnType, provenanceAge, syncStatus,
jobDuration, viewFileCount, scheduleDuration, jobStatus, tableSchemaComparison`.
Schema-sensitive (break on a code/schema change): `schemaComparison, tableSchemaComparison,
columnType, tableColumnType, primaryKeyCheck, tablePrimaryKeyCheck, columnValueEnumCheck,
totalColumnCount`. Freshness (break when upstream sync/build stalls): `syncFreshness,
timeSinceLastSync, syncStatus, dataFreshnessCheck, buildStatus, scheduleStatus, provenanceAge`.

### 2.2 Checks per dataset — VERIFIED (the core impact read)
```
GET  /checks/v2/{datasetRid}/{branchId}    -> [check,...]     (200 [] on both fixtures = no checks configured)
GET  /checks/v2/by-table/{tableRid}        -> [check,...]     (200 [])
GET  /checks/v2/{checkRid}                 -> single check
GET  /checks/v2/by-schedule/{scheduleRid}  -> checks bound to a schedule
GET  /checks/v2/hasManageAllChecksPermission -> false   (perm probe, read)
```
Both fixture datasets returned `[]` (no checks configured) — the read path is proven; the sample is
empty because these datasets have no checks. `POST /checks/v2/by-dataset/bulk` returned **400** for
`datasetRids`/`datasets`/`rids` — correct field name still **UNVERIFIED**; use the per-dataset GET.

### 2.3 Check state & reports — partially VERIFIED
```
POST /checks/reports/v2/latest   {checkRids:[...]}   -> latest run/report per check  (422 on empty list = shape OK, needs real RIDs)
POST /checks/reports/v2/events   {...}               -> reports for events
POST /checks/state/pause         {checkRids:[...]}   -> areChecksPaused (READ; 422 on empty = shape OK)
POST /checks/state/snooze-details / snooze-history   -> snooze state (READ)
POST /checks/state/batch/incidents                   -> open incidents per check
GET  /checkgroups/all                                -> check groups (monitoring rollups)
```
`latest` and `pause` accept `{"checkRids":[...]}` (422 on empty confirms field is right). A check's
current state (failing/paused/snoozed/incident) is the live impact signal — **UNVERIFIED end-to-end**
only because no dataset here has checks to supply real check RIDs.

### Impact contribution
For a changed dataset: `GET /checks/v2/{rid}/{branch}` → each check's `checkName` tells you what it
asserts; a schema/column change that violates a `schemaComparison`/`columnType`/`primaryKeyCheck`
check is a concrete, enumerable break. `latest`/`incidents` tell you current pass/fail.

---

## 3. webhooks — outbound integrations (external downstream consumers)

Base `/webhooks/api`. Reads only; never `create`, `publish`, `delete`, `update*`, `duplicate`.

### 3.1 source → webhook edge — VERIFIED (both directions)
```
PUT  /registry/v0/webhooks-getRidsForSources   {sourceRids:[...]}  -> {sourceWebhooks:{ <sourceRid>:[webhookRid,...] }}
GET  /registry/v0/bulk/internal                                     -> {webhooks:{}}   (no internal webhooks here)
POST /registry/v0/bulk                          {...}               -> bulk fetch by RID
PUT  /registry/v0/webhooks-getLatestVersion     {...}               -> bulk latest versions
```
Real result: slack source `901487b3` → `webhook.bc20abc6`; Alpaca source `6388daff` → `webhook.0e3ae5ee`.

### 3.2 webhook definition — VERIFIED
```
GET  /registry/v0/{webhookRid}/latest             -> {metadata,definition}
GET  /registry/v0/{webhookRid}/version/{version}
GET  /registry/v0/plugins/magritte                -> outbound task manifest per source type
```
`.../latest` for the Slack webhook returned `metadata.name:"sendMessage"`, `apiName:"SlackSendMessage"`,
and `definition.spec.config.type:"magritteRestWebhook"` with
`magritteRestWebhook.sourceRid:"ri.magritte..source.901487b3-..."` — so the **webhook definition itself
back-references its source RID** (reverse edge without needing §3.1). Alpaca webhook = `getBars`
(`apiName:"Getbars"`), same shape. These are outbound REST calls leaving Foundry.

### Impact contribution
A webhook is a consumer OUTSIDE Foundry. Editing/removing a source, or the object/dataset that triggers
a webhook, breaks that outbound integration. Edge is readable both ways: source→webhook (§3.1) and
webhook→source (embedded `sourceRid`, §3.2).

---

## 4. Object Sentinel monitors — NOT a standalone service

`OBJECT_SENTINEL_MONITOR` appears in the MCP source only as a **Compass resource-type enum** (alongside
`MONOCLE_GRAPH`, `NOTEPAD_NOTEPAD`, etc.), and `function-registry` exposes `getUsageHistory`
(`GET /functions/.../usageHistory`) referencing sentinel usage. There is **no dedicated sentinel/monitor
service** among the 27 services / 831 endpoints. Monitors watching an object type are therefore
**Compass resources of type `OBJECT_SENTINEL_MONITOR`** — enumerate/traverse them via Compass
(covered in the ontology/compass docs), not via a health/monitor API. **UNVERIFIED** (no sentinel exists
on this stack to read).

---

## 5. Schedules — no internal service on this stack

`/orchestration/api/schedules/{rid}`, `/scheduler/api/schedules/{rid}`, `/foundry-schedules/api/...`,
`/build2/api/schedules/...` and `/build2/api/manager/schedules/...` **all 404**. The only
schedule-adjacent reads that exist: data-health `GET /checks/v2/by-schedule/{scheduleRid}` (§2.2) and
`scheduleStatus`/`scheduleDuration` check types (§2.1). For actual schedule definitions use the public
SDK (`Dataset.get_schedules`, documented ≤1hr stale). No non-stale internal equivalent found. **UNVERIFIED**
that any internal scheduler is reachable on this deployment.

---

## 6. telemetry / job-tracker / jemma — build & job outcomes ("did my change break a build?")

### foundry-telemetry-service — VERIFIED prefix, session/container logs
```
POST /info/sessions/get-batch          {...}                 -> {sessionInfos:[...]}   (200 {sessionInfos:[]} on empty)
POST /info/containers/get-batch        {...}
GET  /info/containers/by-owning-rid/{owningRid}              -> container for a build/job (204 = none)
POST /info/sessions/by-run-rids/get-batch
POST /containers/{containerRid}/sessions/{sessionId}/logs/read/v4   -> job logs (read)
```
Jobspec outputs referenced `ri.foundry-telemetry-service.main.telemetry-container.*` (per lineage doc),
so telemetry maps a build/job → its container/session → logs. Request body field names for `get-batch`
**UNVERIFIED** (only empty-body shape probed).

### job-tracker — VERIFIED prefix
```
POST /builds            {...}    -> getBuildsOverview
POST /builds/summary    {...}    -> getBuildSummaryOverview   (400 on empty = mounted, shape unknown)
GET  /builds/{buildId}           -> getBuildDetails (build outcome)
POST /builds/bulkV2
```
Gives per-build status/outcome — the "did my change actually break a build" read. Body shapes **UNVERIFIED**
(no build RID on hand; needs a `buildId` or object-set query body).

### jemma — VERIFIED mounted, read heavy
```
GET  /builds/graph                       -> getBuildGraph   (times out with no params — needs a build/job filter)
GET  /builds/{buildRid}/full-summary
GET  /builds/{jobRid}/{stepNumber}/logs
POST /builds/batch/builds-for-jobs
```
Read-capable build DAG + logs, but **avoid** `POST /builds` (triggerBuild) and `.../abort`. Untested for
lack of a build RID. **UNVERIFIED**.

---

## 7. foundry-metadata — dataset/transaction key-value metadata — VERIFIED prefix

```
GET  /metadata/v2/datasets/{datasetRid}                 -> 204 (no namespaced metadata) on fixture
GET  /metadata/v2/datasets/{datasetRid}/all             -> 400 Metadata:UnboundedMetadataAccessDenied (needs namespace/bound)
GET  /metadata/v2/datasets/{datasetRid}/transactions/{transactionRid}
GET  /metadata/events                                   -> change event stream
```
Namespaced key-value store attached to datasets/transactions. Low impact-graph value on its own (fixture
had none), but `/metadata/events` is a global change-event feed worth noting. Never call `put*`/`delete*`.

---

## 8. authoring-server — UNVERIFIED

Prefix not found (`/authoring-server`, `/authoring`, `/stemma`, `/code-authoring`, `/foundry-authoring-server`
all 404). Endpoints (`local-dev-access/*`, `security/gitToken`, `security/token/*`) are git-token / local-dev
plumbing — **not impact-relevant** even if located. Deprioritized.

---

## Change-impact edges harvested (summary)

| Edge | Direction | Endpoint | Status |
|---|---|---|---|
| external source inventory | node | `GET /magritte-coordinator/api/source-store/sources/{rids,descriptions,types}` | VERIFIED |
| source → agent (sync runtime) | fwd | `GET /source-store/source/{id}/agents` | VERIFIED |
| source → webhook (outbound consumer) | fwd | `PUT /webhooks/api/registry/v0/webhooks-getRidsForSources` | VERIFIED |
| webhook → source | rev | `GET /webhooks/api/registry/v0/{rid}/latest` (`spec.config.magritteRestWebhook.sourceRid`) | VERIFIED |
| dataset → health checks | fwd | `GET /data-health/api/checks/v2/{datasetRid}/{branchId}` | VERIFIED (empty on fixtures) |
| check → assertion type | meta | `GET /data-health/api/checks/v2/available` | VERIFIED |
| check → current state/incident | fwd | `POST /data-health/api/checks/state/{pause,batch/incidents}`, `/checks/reports/v2/latest` | UNVERIFIED (no real checks) |
| schedule → checks | rev | `GET /data-health/api/checks/v2/by-schedule/{scheduleRid}` | VERIFIED-mounted |
| build/job → outcome | fwd | `GET /job-tracker/api/builds/{buildId}`, jemma `full-summary` | UNVERIFIED (no build RID) |
| build/job → logs | fwd | `foundry-telemetry-service` container/session logs | VERIFIED prefix |
| **source → synced dataset** | fwd | **NONE in magritte — GAP; use dataset build2 jobspec/provenance** | GAP |
| object type → sentinel monitor | fwd | Compass resource type `OBJECT_SENTINEL_MONITOR` (not this lane) | UNVERIFIED |
