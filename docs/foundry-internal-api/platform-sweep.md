# Platform sweep — remainder services, other GraphQL surfaces, app-shell inventory

Discovered and live-verified 2026-07-21 against `zap.usw-18.palantirfoundry.com`.
Complements `graphql-gateway.md`, `lineage-monocle-build2.md`, `reverse-indexes.md`.
Same stability warning applies: nothing here is public/supported. Pin, shape-check, degrade loudly.

Base-path convention confirmed for every service probed: `https://<stack>/<service-name>/api/…`.

---

## 0. Lead finding — the GraphQL gateway is a *unified reverse-index bus*, and schedules give a free O(1) count

The most valuable thing on this stack for a change-impact gate is not another REST service — it is
that the **`/graphql-gateway/api/bulk`** endpoint already fronts *every* reverse edge we care about
(ontology dependents, schedule triggers, function usages, automations), batchable in one POST, and one
of those edges returns a **server-side total count** so blast-radius sizing costs a single call:

```graphql
query($rid: RID!, $branch: String!) {
  schedules(filter: {buildDatasets:[{datasetRid:$rid, branchName:$branch}],
                     affectedDatasets:[{datasetRid:$rid, branchName:$branch}]}, pageSize: 1) {
    totalNumberOfResultsV2         # <-- exact count of schedules this dataset triggers/affects
  }
}
```
VERIFIED: `NumberOfSchedulesColorSchemeQuery` → `{totalNumberOfResultsV2: 0}` for a leaf dataset;
the unfiltered `schedules(pageSize:3)` returned `totalNumberOfResultsV2: 34` for the stack.
Every other reverse edge found here requires client-side pagination to count; **this one does not.**
The `schedules.filter` accepts `buildDatasets`, `affectedDatasets`, `inputDatasetRids`,
`affectedProjects`, `projectRids`, `userIds` — i.e. dataset *and* project granularity, three edge
directions (builds / affects / consumes-as-input). This is the GraphQL-backed, non-stale replacement
for the public SDK's 1-hour-stale `Dataset.get_schedules`.

---

## 1. Service-by-service verdict (the untouched remainder)

| Service (endpoints) | Base path verified | What it is | Change-impact verdict |
|---|---|---|---|
| **control-panel** (32) | `/control-panel/api` ✓ 200 | Enrollment / org / host tenancy admin. `getEnrollmentByAuthHeader` returns my enrollment+org; `…/application-and-extension-restrictions` returns `noRestrictions`. | **Not relevant.** Tenancy structure, not per-resource edges. Useful only to enumerate which apps an enrollment is *allowed* (consumer-surface gating), not what depends on a RID. |
| **multipass** (4) | `/multipass/api` ✓ 200 | Identity — org metadata & permissions. `organizations/v2/all` returns org display names/markings. | **Not relevant.** Identity/permissions, no resource lineage. |
| **foundry-metadata** (28) | `/foundry-metadata/api` ✓ | Namespaced key/value metadata on datasets/transactions/views. `…/all` is permission-gated (`UnboundedMetadataAccessDenied`). Has a batch: `POST /metadata/batch/namespaced-dataset-view-metadatas`. | **Marginal.** No reverse edges; metadata only. Batch endpoint noted for the table. |
| **language-model-service** (15) | `/language-model-service/api` ✓ 200 | AIP `SemanticSearchService` — vector indexes. `GET /semantics/v1/indexes` → `{"indices":{}}` (none on this stack). | **Potentially relevant, UNVERIFIED.** A semantic index is a *consumer* of an object type / dataset; changing the source should flag the index. No index existed to confirm the reverse direction. Worth re-checking on a stack with AIP search deployed. |
| **comments** (14) | `/comments/api` ✓ 200 | Comments keyed by resource RID. `POST /comments/counts` (batch) → `{"responses":{}}`. | **Marginal / weak signal.** Comment presence = "a human is watching this resource"; batchable count could enrich a blast-radius report but is not a dependency edge. |
| **repository-bootstrapper** (7) | `/repository-bootstrapper/api` ✓ 200 | Templates for *creating new* code repos (`GET /templates` → python-library, etc.). | **Not relevant.** Forward scaffolding, not reverse impact. |
| **foundry-telemetry-service** (15) | `/foundry-telemetry-service/api` ✓ 200 | Build/session/container runtime telemetry + logs. Batch: `POST /info/containers/get-batch` → `{"containerInfos":[]}`. | **Not relevant** to dependency impact (build-run health only). |
| **jemma** (8) | `/jemma/api` ✓ 200 | A build service (sibling of build2). `POST /builds/batch/status-report` → `{}`. `GET /builds/graph` **times out** (heavy). | **Redundant** — build lineage already covered by build2/monocle. |
| **job-tracker** (5) | `/job-tracker/api` ✓ mounted (422 on bad body) | Build-overview aggregator, keyed by dataset. `POST /builds/bulk`, `/builds/bulkV2`. | **Marginal.** Batch "current/last build status for many datasets" — build-health enrichment, not a dependency edge. Body shape UNVERIFIED. |
| **object-set-service** (7) | `/object-set-service/api` ✓ mounted (400 on bad body) | Materializes object sets (`objectSets/objects/all/initial`, `getBulkLinksPage`). | **Blast-radius sizing, UNVERIFIED shape.** Could count "N objects of type X affected," but the request proto is the Gotham object-set spec the public OSDK already wraps — prefer the public API. Not a *reverse* edge. |
| **authoring-server** (9) | 404 `Route:RouteNotMounted` on `/local-dev-access/palantir-mcp` | Local-dev access checks + git-token vault. | **Not relevant** (dev-access/secrets), and target routes not mounted here. |
| **log-receiver** (1) | — (write-only `POST /logs`) | Log ingest. | **Not relevant**, and it is a write — not probed. |

Nothing in the remainder adds a *new* reverse-dependency edge at the REST layer. The reverse edges all
live in the GraphQL gateway (§2) and in already-documented services (compass/datahealth batch, §4).

---

## 2. Other GraphQL surfaces + operations (harvested from non-hubble app bundles)

The gateway is shared; different app shells register different operations. Harvesting bundles from
**code / object-view / functions / foundry-rules / monocle** shells (339 new bundles beyond hubble's)
yielded **3,010 distinct GraphQL defs**. The impact-relevant, non-hubble operations — all executed
live against `/graphql-gateway/api/bulk` unless marked:

| Operation (source bundle) | Edge it computes | Status |
|---|---|---|
| `NumberOfSchedulesColorSchemeQuery` | **dataset → # schedules** that build/affect it (server count `totalNumberOfResultsV2`) | **VERIFIED** (0 for leaf; 34 stack-wide) |
| `GetSchedulesQuery` / `GetSchedulesForSearchQuery` | list schedules by `ScheduleSearchFilter` (buildDatasets / affectedDatasets / inputDatasetRids / affectedProjects / projectRids); `nextPageToken` + count | **VERIFIED** (paginates, count=34) |
| `actionType(rid).dependents(pageToken:)` (`UsagesSidebar_ActionTypeDependencies`) | **action-type → dependent resources**, *paginated* | **VERIFIED** (ThirdPartyApplication depends on "Modify Encounter"; `nextPageToken` present) |
| `actionTypes(filter:{ruleTypes:[FUNCTION], functionRid})` (`UsagesSidebar_ActionTypes`) | **function → action-types** that call it (client uses `pageSize:1000`) | **VERIFIED callable** (schema-valid, empty for probed fn) |
| `FunctionVersion.usageHistory` (`UsagesSidebar_Function` / `FunctionUsageHistory`) | **function-version → resources** that used it | shape extracted; **UNVERIFIED** (needs a version pin) |
| `resourceMetadata(rid).objectSetMonitors` (`UsagesSidebar_LinkedAutomations`) | **resource/logic → automations (monitors)** depending on it | **VERIFIED callable** (`null` for probed rid) |
| `GetBackingRepositories` | artifacts-repo → `backingRepositoryRids` | shape extracted |
| `ContractReferenceQuery` / `functionContract` | function contract versions | shape extracted |

Fragment tree for ontology dependents (object-type **and** action-type share it):
`ObjectType.dependents` / `ActionType.dependents` → `OntologyDependentPage { values:[ResourceMetadata], nextPageToken }`
→ `DownstreamResources` resolves to any `ResourceMetadata` (typed variants seen: `DownstreamDatasetResourceMetadata`,
`DownstreamObjectMetadata`, `DownstreamLinkMetadata`). `DownstreamResourcesCountObjectType` is **not** a
server count — it just re-selects `values{rid}` and counts client-side.

> **Correctness landmine (§5 too):** `objectTypeV2.dependents` takes **no** page arguments (per
> `graphql-gateway.md` it returns `nextPageToken` in the payload but there is no `pageToken:` arg to
> request page 2 → **silent truncation**). `actionType.dependents` **does** take `pageToken:`. The
> gate must treat these two asymmetrically: object-type dependents past page 1 = *inconclusive*, not safe.

No `mutation` was executed. The two `ImportSource…UsageMutation` ops seen in bundles were recorded read-only, never sent.

---

## 3. App-shell inventory (the full consumer surface)

Every Foundry app on this stack is served at **`/workspace/<app>/`** (bare `/<app>/` = 404). The shell
HTML is fetchable with the bearer as `Cookie: PALANTIR_TOKEN=<token>`, and its `content-addressable-storage/frontend/<sha256>.js`
bundles are fetchable with the bearer alone. 22 live shells (title / static-app marker):

| Route | Title | Route | Title |
|---|---|---|---|
| `/workspace/hubble/` | Object Explorer (Ontology Mgr) | `/workspace/code/` | Code repositories |
| `/workspace/carbon/` | Carbon Workspaces (home) | `/workspace/object-view/` | Object View |
| `/workspace/compass/` | file browser | `/workspace/functions/` | Functions |
| `/workspace/monocle/` | Data lineage | `/workspace/foundry-rules/` | Rules (taurus) |
| `/workspace/slate/` | Slate | `/workspace/scheduler/` | Schedules |
| `/workspace/vector/` | Code Workbook | `/workspace/job-tracker/` | Builds |
| `/workspace/quiver/` | Quiver (time series) | `/workspace/data-health/` | Data Health |
| `/workspace/fusion/` | Fusion (spreadsheet) | `/workspace/aip/` | AIP homepage |
| `/workspace/notepad/` | Notepad | `/workspace/marketplace/` | Marketplace |
| `/workspace/map/` | Map (vertex) | `/workspace/developer-console/` | Developer Console |
| `/workspace/home/` | Home (jigsaw) | `/workspace/apps/` | Applications Portal |

Any of these can *consume* an ontology/dataset resource; the gateway's `dependents`/`usages` edges are
how each surfaces "N apps use this." (This is exactly how hubble's "N apps" count was found.)

---

## 4. Batching & pagination limits

**Reverse-index batching primitives (avoid N+1):**

| Endpoint | Batches | Notes |
|---|---|---|
| `POST /graphql-gateway/api/bulk` | N GraphQL requests / POST, **independent per-request** | the reverse-index bus; each list field pages separately |
| `POST /compass/api/batch/resources` (+ `…/parents`, `…/ancestors`, `…/exist`, `/batch/paths`, `/batch/projects-by-rids`) | many RIDs → metadata | **the RID→metadata N+1 fix** for decorating any dependents list |
| `POST /monocle/api/links/graphV2` | array `resourceIdentifiers` | link graph (documented) |
| `POST /foundry-datahealth/api/checks/v2/by-dataset/bulk` · `…/by-schedule/bulk` | reverse: checks **by dataset / by schedule** | health-check reverse index, bulk (health agent's lane) |
| `POST /function-registry/api/functions/batch/{metadata,specs,identifiers,parent}` | many function RIDs | function metadata in bulk |
| `POST /job-tracker/api/builds/bulk` · `/builds/bulkV2` | many dataset RIDs → build overview | build-status enrichment |
| `POST /foundry-catalog/api/catalog/batch/datasets2` | many dataset RIDs | dataset load |
| `POST /comments/api/comments/counts` | many resource RIDs → counts | weak "watched" signal |
| `POST /foundry-metadata/api/metadata/batch/namespaced-dataset-view-metadatas` | many dataset views | metadata |

**Pagination observed (VERIFIED where a value is shown):**

| Surface | pageSize arg | Token | Server count | Hard cap |
|---|---|---|---|---|
| gateway `schedules` | `pageSize` (client used 20) | `pageToken`/`nextPageToken` (base64 cursor) | **`totalNumberOfResultsV2`** ✓ 34 | UNVERIFIED |
| gateway `actionTypes` | `pageSize` (client used 1000) | `nextPageToken` (base64) | none | UNVERIFIED (1000 = client cap) |
| gateway `functions` | `pageSize` | `nextPageToken` = plain offset (`"3"`) | none | UNVERIFIED |
| gateway `actionType.dependents` | — (no size arg) | `pageToken`/`nextPageToken` | none | UNVERIFIED |
| gateway `objectTypeV2.dependents` | — | payload has `nextPageToken` but **no `pageToken:` arg** | none | **truncates silently** |
| gateway `objectTypesV2` / `linkTypes` | `pageSize` | `pageToken` | (see graphql-gateway.md) | — |

**A gate must not read page 1 of `objectTypeV2.dependents` as complete.** With no way to request page 2
and no server count, a large fan-out object type reports a truncated dependents list that is
indistinguishable from "few dependents." Prefer the monocle link graph (documented) for object-type
reverse edges, and reserve the gateway `dependents` for action-types (which page correctly).

---

## 5. Things nobody asked for that matter

1. **Schedules are the only reverse edge with a free server-side count.** `totalNumberOfResultsV2` on
   `schedules(filter:{buildDatasets/affectedDatasets/inputDatasetRids/affectedProjects/projectRids})`
   answers "will editing this dataset/project trigger a build, and how many schedules?" in **one call** —
   at both dataset and project granularity, across three edge directions. This is the cheapest, highest-signal
   pre-merge check on the stack and is not in any of the four existing docs. VERIFIED.

2. **The object-type-vs-action-type `dependents` pagination asymmetry is a silent-safety bug waiting to
   happen.** Same field name, same return type, but only action-type accepts `pageToken:`. A gate that
   paginates one and not the other, or assumes both truncate the same way, will under-report object-type
   impact and call an unsafe change safe. Treat object-type `dependents` >1 page as *inconclusive*.

3. **Function change-impact has real reverse edges** the SDK declares undiscoverable: `function → actionTypes`
   (`actionTypes(filter:{ruleTypes:[FUNCTION],functionRid})`, VERIFIED callable) and
   `FunctionVersion.usageHistory` (resources that ran a function version). Pairs with the documented
   `function-registry` ontology bindings to close the function-wiring gap.

4. **`resourceMetadata(rid).objectSetMonitors` is a generic automation reverse-edge** — any resource can
   report the object-set monitors (automations) watching it. VERIFIED callable. Automations are a change
   consumer that none of the REST services expose.

5. **compass `/batch/resources*` is the missing N+1 fix.** Every reverse-index above returns bare RIDs or
   opaque `_id`s; decorating a 500-RID dependents list into paths/types is one `POST /batch/resources`,
   not 500 GETs. Without it, a gate over a large change is too slow to run in CI.

6. **datahealth exposes reverse-by-dataset and reverse-by-schedule in bulk** (`/checks/v2/by-dataset/bulk`,
   `/checks/v2/by-schedule/bulk`) — "what monitors break if this dataset/schedule changes," batched.
   (Owned by the ingestion/health agent, flagged here because it is a first-class change-impact edge.)

7. **The whole consumer surface is enumerable and static-fetchable** — 22 `/workspace/<app>/` shells,
   bundles readable with the bearer. A gate can keep its GraphQL operation set current by re-harvesting
   these bundles when the stack upgrades, rather than pinning to a snapshot that drifts.
