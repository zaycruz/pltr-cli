# Foundry internal lineage APIs — monocle, build2, branch-service

Undocumented internal Foundry APIs for resource lineage (monocle) and transform/pipeline
lineage (build2). Reverse-engineered and **live-verified** against
`https://zap.usw-18.palantirfoundry.com` on 2026-07-21.

All calls use:

```
Authorization: Bearer <token>
Accept: application/json
Content-Type: application/json
```

Every request made while producing this document is recorded in `endpoint_audit.log`
(timestamp, method, path, status).

---

## How these schemas were recovered (method, so it can be repeated)

Three techniques, in order of yield:

1. **Sourcemap mining.** `@palantir/mcp` v0.397.0 ships unminified with sourcemaps at
   `~/.npm/_npx/63a3fbe1b43eb419/node_modules/@palantir/mcp/dist/index.mjs.map`
   (3568 `sourcesContent` entries). This yields **endpoint paths, HTTP verbs, method
   names, and docstrings** — e.g. `monocle-api/graphService.js`,
   `build2-api/.../jobSpecService.js`. It does **not** yield request field names:
   these are Conjure-generated TypeScript `interface` declarations, which compile to
   nothing and therefore never appear in any JS bundle or sourcemap.

2. **The type-confusion oracle** (how most field names here were found). On monocle and
   build2, unknown JSON fields are silently ignored, and a missing required field
   produces `400 Default:InvalidArgument` with an empty `parameters` object — no field
   name. But sending a **wrong-typed value** for a field distinguishes the two cases:

   | Probe | Meaning |
   |---|---|
   | `{"candidate": {"__zz__":1}}` → `400 Default:InvalidArgument` | field is **unknown** (ignored; the 400 is the still-missing required field) |
   | `{"candidate": {"__zz__":1}}` → `422 Conjure:UnprocessableEntity` | field **exists**, object was the wrong type for it |
   | `{"candidate": {"__zz__":1}}` → `200` | field exists **and** was the last missing required field |

   Iterating a candidate-name list against this oracle enumerates a request type's real
   fields without any source access. This is what found `branchFallbacks` (the field
   that unblocked all of build2) — it returned `200` where 45 other names returned 400.

   Caveat: the oracle assumes unknown fields are ignored. That is **not universal** —
   `branch-service`'s `dependency-graph` uses strict deserialization and returns
   `422` for any unknown key (see that section). Confirm the service's behaviour with a
   junk key before trusting oracle results on it.

   Second caveat: the oracle can produce **false negatives on enum fields**. Probing
   `type` on `intersectionV2` with an object value returned `400` (reading as
   "unknown"), yet `type` is in fact a required enum on that endpoint. Enum fields may
   fail closed. Retest promising names with plausible *values*, not just wrong types.

3. **Frontend bundle mining** (what cracked the V3 generation). The Foundry frontend is
   served from the customer host and contains the real call sites. The bearer token
   alone returns a login shell on `/workspace/*`; sending it **as a cookie** returns the
   real app HTML:

   ```
   Cookie: PALANTIR_TOKEN=<token>
   ```

   `/workspace/monocle/` (`<title>Data lineage</title>`) lists its bundles at
   `/assets/content-addressable-storage/frontend/<sha256>.js`. Sourcemaps are **not**
   served (`.js.map` → 503), but minified code preserves JSON field-name string keys, so
   object literals at call sites are directly readable.

---

## ⚠️ V2 is dead code — the live frontend uses V3

`monocle` `GraphService` exposes three generations: `/links` (V1), `/links/*V2`, and
`/links/*V3`. Grepping all 68 bundles of the shipped Data Lineage app found **zero call
sites for any V2 method**. V2 exists only as unused generated Conjure client code. The
live application calls `getGraphV3`, `getHierarchyV3`, and `getIntersectionV3`
exclusively.

All three generations are **live and functional server-side** (verified below), so a
pltr-cli fork may use either. **Prefer V3**: it is what Palantir's own product exercises,
so it is the least likely to be removed or to drift.

The V2→V3 difference is confined to the `branch` field and one link-union variant:

| | V2 | V3 |
|---|---|---|
| branch | two flat fields: `"branch":"master"`, `"fallbacks":[]` | one tagged union: `"branch":{"type":"legacyBranch","legacyBranch":{"branch":"master","fallbacks":[]}}` |
| ontology links | `ontologyLink` | `ontologyLinkV2` (also still emits `ontologyLink`) |

---

## Common types

### `BranchSpec` (V3 `branch` field) — tagged union

Both variants verified 200.

```jsonc
// legacy (catalog) branch — equivalent to V2's flat branch + fallbacks
{"type":"legacyBranch","legacyBranch":{"branch":"master","fallbacks":[]}}

// global branch (Global Branching)
{"type":"globalBranch","globalBranch":{
   "globalBranch":{"type":"default","default":{}},
   "fallbackGlobalBranches":[]}}
// non-default form: {"type":"nonDefault","nonDefault":{"rid":"<branchRid>"}}
```

### `BranchFallbacks` (build2) — **required on every jobspec graph walk**

```jsonc
{"branches": ["<branchId>", ...]}   // canonical form, from the frontend call site
{}                                  // also accepted; `branches` defaults to empty
```

This object being **required** is the single reason naive attempts at the build2
traversal endpoints return `400`. A JSON list here is rejected (`422`) — it is a map.

### `LinkDirection` enum

`INCOMING` | `OUTGOING` | `UNDIRECTED`

---

# monocle — `GraphService`

Base path: `/monocle/api`. All 9 documented endpoints plus the 3 undocumented V3
endpoints are covered.

Reference RIDs used in the examples below:

- `PROG` = `ri.ontology.main.object-type.018c03f7-9bdb-44e5-91e1-73bfcbe19f98` (object type "Program")
- `DS` = `ri.foundry.main.dataset.d9069f13-21cf-4086-8dd0-231fd10a1183`

## `POST /monocle/api/links/graphV3` ✅ preferred

One hop of the link graph for each requested RID.

**Request**

| field | type | required | notes |
|---|---|---|---|
| `resourceIdentifiers` | `string[]` (RIDs) | yes | |
| `branch` | `BranchSpec` | yes | tagged union, see above |
| `serviceTypeFilter` | `string[]` | no | `[]` = no filter |

```json
{"resourceIdentifiers":["ri.ontology.main.object-type.018c03f7-9bdb-44e5-91e1-73bfcbe19f98"],
 "branch":{"type":"legacyBranch","legacyBranch":{"branch":"master","fallbacks":[]}},
 "serviceTypeFilter":[]}
```

**Response** `200`

```json
{"nodes":[{"resourceIdentifier":"ri.ontology.main.object-type.018c03f7-...",
           "links":[{"type":"ontologyLinkV2",
                     "ontologyLinkV2":{"linkDirection":"UNDIRECTED",
                                       "objectTypeId":"ri.ontology.main.object-type.750751e2-...",
                                       "linkId":"..."}}]}]}
```

**Semantics / limits**

- Returns **one hop only**. Traverse by re-calling with the RIDs found in link payloads.
- A RID that monocle does not index is **silently dropped** — no node is returned for it
  at all (not a node with an empty `links` array). Requesting 2 RIDs can return 1 node.

## `POST /monocle/api/links/graphV2` ✅

Identical to V3 except `branch`/`fallbacks` are flat, and ontology links come back as
`ontologyLink` rather than `ontologyLinkV2`.

```json
{"resourceIdentifiers":["<rid>"],"branch":"master","fallbacks":[],"serviceTypeFilter":[]}
```

## `POST /monocle/api/links/hierarchyV3` ✅ preferred

Recursive traversal, **both directions**, returning depth per reachable RID.

**Request**

| field | type | required | notes |
|---|---|---|---|
| `resourceIdentifiers` | `string[]` | yes | |
| `branch` | `BranchSpec` | yes | |
| `serviceTypeFilter` | `string[]` | no | |
| `excludeDownstream` | `boolean` | no | `true` ⇒ `descendants` returned empty |

**Response** `200` — **not** node-shaped; two maps of `rid → depth (int)`:

```json
{"ancestors":{"ri.foundry.main.dataset.d9069f13-...":1},
 "descendants":{"ri.third-party-applications.main.application.c2b28336-...":1,
                "ri.third-party-applications.main.application.32bce346-...":1}}
```

Verified with `excludeDownstream:true` → `{"ancestors":{...},"descendants":{}}`.

## `POST /monocle/api/links/hierarchyV2` ✅

**Previously believed broken ("always `{"nodes":[]}`") — it is not.** It accepts the flat
V2 branch fields and returns the same `{ancestors, descendants}` shape as V3. The
`{"nodes":[]}` observation came from reading it as a graph-shaped response; `hierarchy*`
simply does not return `nodes`.

**Request**

| field | type | required | notes |
|---|---|---|---|
| `resourceIdentifiers` | `string[]` | yes | |
| `branch` | `string` | yes | e.g. `"master"` |
| `fallbacks` | `string[]` | yes | `[]` accepted |
| `serviceTypeFilter` | `string[]` | no | |
| `excludeDownstream` | `boolean` | no | |

Verified on both a dataset and an object type, with `excludeDownstream` `true`/`false`/omitted.
Default (omitted) behaves as `false`.

## `POST /monocle/api/links/intersectionV3` ✅ preferred

**Request**

| field | type | required | notes |
|---|---|---|---|
| `resourceIdentifiers` | `string[]` | yes | |
| `branch` | `BranchSpec` | yes | |
| `type` | enum | **yes** | `BETWEEN` \| `ANCESTORS` \| `DESCENDANTS` |
| `serviceTypeFilter` | `string[]` | no | |

`type` is the required field whose absence causes the otherwise-inexplicable `400`.

**Response** `200` — a flat array of RIDs.

```json
["ri.third-party-applications.main.application.c2b28336-...",
 "ri.third-party-applications.main.application.32bce346-..."]
```

Verified all three enum values against `[PROG, DS]`: `BETWEEN` → `[]`,
`ANCESTORS` → `[]`, `DESCENDANTS` → 2 RIDs.

## `POST /monocle/api/links/intersectionV2` ✅

Same as V3 with flat branch fields — and it **also requires `type`**:

```json
{"resourceIdentifiers":["<ridA>","<ridB>"],"branch":"master","fallbacks":[],
 "serviceTypeFilter":[],"type":"DESCENDANTS"}
```

Verified: returns results identical to `intersectionV3` for the same inputs.
Documented limit (from the V1 docstring): results capped at **1000 RIDs**.

## `POST /monocle/api/links/downstreamObjectsV3` ✅

**Request:** `resourceIdentifiers` (required), `branch` (`BranchSpec`, required).
`serviceTypeFilter` is **not** a field here.

**Response** `200`

```json
{"resourceIdentifiersToObjects":{"ri.ontology.main.object-type.018c03f7-...":[]}}
```

For a dataset input, values are object-type descriptors:

```json
{"resourceIdentifiersToObjects":{"ri.foundry.main.dataset.d9069f13-...":
  [{"rid":"ri.ontology.main.object-type.018c03f7-..."}]}}
```

Note: this endpoint has **no call site anywhere in the shipped frontend** — it is
uncalled by Palantir's own product, so treat it as the least-exercised of the set.

## `POST /monocle/api/links/downstreamObjectsV2` ✅

Fields: `resourceIdentifiers`, `branch` (string), `fallbacks`. Same response shape.
Verified 200 returning a real object type for `DS`.

## `POST /monocle/api/links/source` ⚠️ deprecated

Marked `@deprecated This endpoint is deprecated. Do not use.`

**The request body is a bare JSON string, not an object.** Every object form returns
`422`; a JSON list also returns `422`.

```
POST /monocle/api/links/source
"ri.ontology.main.object-type.018c03f7-9bdb-44e5-91e1-73bfcbe19f98"
```

→ `200 []`

## `POST /monocle/api/links` (V1 `getGraph`) ⚠️ deprecated

`@deprecated This endpoint returns object types with fake RIDs assigned to them.`

Fields: `resourceIdentifiers`, `branch`, `fallbacks`, `serviceTypeFilter` (same as V2).
Verified `200`, but returned `{"nodes":[]}` for `PROG` where V2/V3 return links —
consistent with the fake-RID deprecation warning. **Do not use.**

## `POST /monocle/api/links/hierarchy` (V1) ⚠️ deprecated

Same fields as `hierarchyV2` including `excludeDownstream`. Verified `200` returning
`{"ancestors":{},"descendants":{...}}`. Note that V1 returned **empty ancestors** for
`PROG` where V2/V3 correctly return the upstream dataset — another fake-RID symptom.

## `POST /monocle/api/links/intersection` (V1) ⚠️ deprecated

Fields: `resourceIdentifiers`, `branch`, `fallbacks`, `serviceTypeFilter`. Returns `400`
without `type`, same as V2/V3.

---

## monocle link union variants

`links[].type` is the discriminant; the payload lives under a key of the same name.
Full set from `graphLink.js` (8 variants), plus `ontologyLinkV2` observed only in V3:

```
datasetLink, ontologyLink, federatedLink, provenanceLink,
mlLink, objectProvenanceLink, actionTypeLink, joinTableLink
+ ontologyLinkV2  (V3 responses)
```

Field lists **observed live** over a 26-RID BFS:

| variant | fields | observed types |
|---|---|---|
| `datasetLink` | `resourceIdentifier`, `linkDirection`, `name`, `inTrash` | string, enum, null, null |
| `ontologyLink` | `objectTypeId`, `linkDirection`, `cardinality`, `linkId`, `actionTypeId`, `name` | string, enum, string, string\|null, null, null |
| `ontologyLinkV2` | `objectTypeId`, `linkDirection`, `linkId` | string, enum, string |
| `objectProvenanceLink` | `resourceIdentifier`, `linkDirection`, `inTrash` | string, enum, bool\|null |
| `actionTypeLink` | `resourceIdentifier`, `linkDirection` | string, enum |
| `joinTableLink` | `resourceIdentifier`, `linkDirection`, `linkId` | string, enum, string |
| `federatedLink` | **not observed** — see UNRESOLVED | |
| `provenanceLink` | **not observed** — see UNRESOLVED | |
| `mlLink` | **not observed** — see UNRESOLVED | |

Every variant observed carries `linkDirection`. Only `ontologyLink*` lacks
`resourceIdentifier`, carrying `objectTypeId` instead — a client walking the graph must
special-case it.

---

## Which RID kinds monocle returns links for

Verified by issuing `graphV2` for each kind and counting returned nodes/links:

| RID kind | nodes returned | links | link types seen |
|---|---|---|---|
| `ri.ontology.main.object-type` | 1 | 4 | `ontologyLink`, `objectProvenanceLink`, `datasetLink` |
| `ri.foundry.main.dataset` | 1 | 2 | `ontologyLink`, `objectProvenanceLink` |
| `ri.third-party-applications.main.application` | 1 | 14 | `objectProvenanceLink` |
| `ri.actions.main.action-type` | 2 of 2 | 9 | `actionTypeLink`, `ontologyLink` |
| `ri.stemma.main.repository` (code repo) | **0** | 0 | — |
| `ri.ontology.main.ontology` | **0** | 0 | — |
| `ri.foundry.main.jobspec` | **0** | 0 | — |

(All rows come from single-kind `graphV2` probes except `action-type`, whose counts come
from the 26-RID BFS sweep.)

**The "code repos return zero links" observation is confirmed, and is stronger than
stated:** monocle returns **no node at all** for `ri.stemma.main.repository.*`. The same
holds for ontology RIDs and jobspec RIDs. Monocle indexes *data and ontology* resources,
not source-control or build-plane resources.

Practical consequence: **monocle cannot tell you which repo produces a dataset.** That
edge lives in build2 (`jobSpec.inputSpecs[].inputType == "artifacts"` points at
`ri.stemma.artifacts.repository.*` / `ri.<service>.artifacts.repository.*`).

Not all datasets are indexed either — a second dataset
(`...7a790af7`, an ontology-sync output) was dropped from a 2-RID request while
`...d9069f13` was returned.

---

# build2 — `JobSpecService`

Base path: `/build2/api`. **This is the transform/pipeline lineage edge.**

The key that unlocks all graph-walk endpoints: **`branchFallbacks` is required.**
Without it every request returns `400 Default:InvalidArgument` with no hint, regardless
of what else is supplied.

## `POST /build2/api/jobspecs/branches/{branch}/downstream-jobspecs` ✅

All job specs downstream of the given datasets.

**Request**

| field | type | required | notes |
|---|---|---|---|
| `datasetRids` | `string[]` | yes | list; a map here → `422` |
| `branchFallbacks` | `BranchFallbacks` | **yes** | `{}` or `{"branches":[...]}`; a list → `422` |
| `datasetRidsToIgnore` | `string[]` | no | from frontend call site; prunes traversal |
| `depth` | `integer` | no | hop limit; omitted ⇒ unlimited |

**Real request**

```
POST /build2/api/jobspecs/branches/master/downstream-jobspecs
```
```json
{"datasetRids":["ri.foundry.main.dataset.d9069f13-21cf-4086-8dd0-231fd10a1183"],
 "datasetRidsToIgnore":[],
 "branchFallbacks":{"branches":[]},
 "depth":1}
```

**Real response** `200` — a JSON **array** of `{jobSpec, branch}`:

```json
[{"jobSpec":{
    "rid":"ri.foundry.main.jobspec.ffe7ed6b-08cd-4a61-aae7-22656401dbd7",
    "attribution":{"userId":"2a26023c-...","time":"2026-07-20T16:57:24.591040804Z"},
    "workerType":"transforms",
    "inputSpecs":[
      {"inputType":"foundry","branch":"master",
       "datasetLocator":{"datasetRid":"ri.foundry.main.dataset.d9069f13-...",
         "datasetProperties":{"startTransactionRid":null,"endTransactionRid":null,
           "schemaBranchId":null,"schemaVersionId":null,
           "numMaxTransactionsInBatchView":null,
           "batchEndTxnRidWhenReadingFromStart":null}},
       "assumedMarkings":{},"inputFailureStrategy":null,
       "identifier":"48f2c45e-959f-43ba-ae39-813c40f81652"},
      {"inputType":"artifacts","branch":null,
       "datasetLocator":{"datasetRid":"ri.objects-data-funnel.artifacts.repository.funnel-transforms",
                         "datasetProperties":{}},
       "assumedMarkings":{},"inputFailureStrategy":null,"identifier":"15e210b9-..."}
    ],
    "outputSpecs":[
      {"outputType":"foundry",
       "datasetLocator":{"datasetRid":"ri.foundry.main.dataset.7e80947d-...",
                         "datasetProperties":{ /* SYNC_SCHEMA_SPEC, ... */ }},
       "outputMetadata":{}},
      {"outputType":"funnel-security",
       "datasetLocator":{"datasetRid":"ri.objects-data-funnel.main.security-output....",
                         "datasetProperties":{}},
       "outputMetadata":{"storageDatabase":"ALTA"}}
    ],
    "computationParameters":{}, "runtimeParameters":{}, "adjudicationParameters":{},
    "sourceProvenance":{}, "tokenMode":"...", "useScopedTokens":true,
    "useInputScopedTokens":true, "maxAllowedDuration":null,
    "maxAllowedDurationPerStage":null, "executionConstraint":null,
    "jobParameters":{}, "resourceManagementMetadata":{},
    "resourceManagementMetadataV2":{}, "allowRunOnTrashedResources":false,
    "jobVariant":null, "incrementalSpec":null},
  "branch":"master"}]
```

**`JobSpec` top-level keys** (21): `rid`, `attribution`, `workerType`, `inputSpecs`,
`outputSpecs`, `computationParameters`, `runtimeParameters`, `adjudicationParameters`,
`sourceProvenance`, `tokenMode`, `useScopedTokens`, `useInputScopedTokens`,
`maxAllowedDuration`, `maxAllowedDurationPerStage`, `executionConstraint`,
`jobParameters`, `resourceManagementMetadata`, `resourceManagementMetadataV2`,
`allowRunOnTrashedResources`, `jobVariant`, `incrementalSpec`.

- `inputSpecs[]` keys: `inputType`, `branch`, `datasetLocator`, `assumedMarkings`,
  `inputFailureStrategy`, `identifier`
- `outputSpecs[]` keys: `outputType`, `datasetLocator`, `outputMetadata`
- `datasetLocator` keys: `datasetRid`, `datasetProperties`

**`inputType` values observed:** `foundry`, `artifacts`, `spark-module-runtime`,
`funnel-permissions`, `object-type-metadata`, `object-uid-secret-permissions`
**`outputType` values observed:** `foundry`, `foundry-telemetry`, `funnel-job-validator`,
`funnel-security`

**For lineage, use `inputType == "foundry"` / `outputType == "foundry"`.** The other
types are build plumbing (jar artifacts, Spark runtime, permission inputs), not data
lineage edges, and will produce false edges if treated as such.

**`depth` semantics** — verified as a hop limit:

| `depth` | jobspecs returned |
|---|---|
| `0` | 0 |
| `1` | 1 |
| `2` | 2 |
| omitted | 3 (all) |

**Documented limit:** capped at 10 000 job specs (per source docstring); the docstring
directs callers to the `-nodes` endpoint beyond that — but see UNRESOLVED.

## `POST /build2/api/jobspecs/branches/{branch}/upstream-jobspecs` ✅

Same request schema as `downstream-jobspecs` (`datasetRids`, `branchFallbacks`
required; `datasetRidsToIgnore`, `depth` optional). Same response shape.

Verified: for `ri.foundry.main.dataset.7a790af7-...` (an ontology-sync **output**)
returns 3 job specs. For a dataset with no producing job spec it correctly returns `[]`
— an empty result here means "not produced by a transform", not an error.

Traversal semantics from the source docstring: when walking upstream from a dataset it
takes the **first branch in the fallback chain** that has a producing job spec and
continues only on that one; from a job spec it continues only on inputs whose spec
either names no input branch or names that same first fallback branch.

## `POST /build2/api/jobspecs/branches/{branch}/connecting-jobspecs` ✅

Job specs on paths **between** two dataset sets.

**Request**

| field | type | required |
|---|---|---|
| `upstreamDatasetRids` | `string[]` | yes |
| `downstreamDatasetRids` | `string[]` | yes |
| `branchFallbacks` | `BranchFallbacks` | **yes** |
| `datasetRidsToIgnore` | `string[]` | no |

```json
{"upstreamDatasetRids":["ri.foundry.main.dataset.d9069f13-..."],
 "downstreamDatasetRids":["ri.foundry.main.dataset.7a790af7-..."],
 "branchFallbacks":{"branches":[]}}
```

→ `200`, 3 job specs. Response shape identical to `downstream-jobspecs`.

## `POST /build2/api/jobspecs/get-jobspecs-for-datasets` ✅

Producing job spec per dataset. Note `branch` is in the **body** here — this path has no
`{branch}` segment.

**Request**

| field | type | required |
|---|---|---|
| `datasetRids` | `string[]` | yes |
| `branch` | `string` | yes |
| `branchFallbacks` | `BranchFallbacks` | yes |

```json
{"datasetRids":["ri.foundry.main.dataset.7a790af7-..."],
 "branch":"master","branchFallbacks":{"branches":[]}}
```

**Response** `200` — a **map** keyed by dataset RID (unlike the array-returning walks):

```json
{"ri.foundry.main.dataset.7a790af7-...":
   {"jobSpec":{"rid":"ri.foundry.main.jobspec.29cef6bc-...", ...},"branch":"master"}}
```

Datasets with no producing job spec are **omitted** from the map (verified: `DS` → `{}`).
Documented limit: `datasetRids` must not exceed **10 000**.

## `GET /build2/api/jobspecs/datasets/{datasetRid}` ✅

`getJobSpecsForDatasetInGraph` — no body, no branch. Returns all producing job specs for
the dataset **keyed by branch**. Returns `{}` when the dataset has no producing job spec
(verified on `DS`). Cheapest possible "is this dataset built by a transform?" check.

## `GET /build2/api/jobspecs/{jobSpecRid}` — `getJobSpecInGraph`

Single job spec from the current graph. Not exercised here; schema is the `JobSpec`
object documented above.

## Other `JobSpecService` endpoints (paths from sourcemap; **not** verified)

Read-only: `POST /jobspecs/get-jobspecs`, `GET /jobspecs/get-historical-jobspec/{rid}`,
`POST /jobspecs/get-historical-jobspecs`, `POST /jobspecs/get-jobspec-for-dataset`,
`POST /jobspecs/get-jobspecs-for-datasets-and-branches` (max 1000),
`POST /jobspecs/can-{edit-jobspecs-with-workers,remove-jobspecs,run-jobspecs,put-jobspecs}`,
`POST /jobspecs/has-job-spec-with-sever`.

**Mutating — do not call from a read-only client:** `put-jobspecs`, `put-jobspecs2`,
`put-jobspecs-on-behalf-of`, `remove-jobspecs`, `remove-jobspecs2`. The entire
`BuildManagerService` (`/build2/api/manager/*`) submits and cancels builds.

## Permission note

`POST /build2/api/jobspecs/events` and `GET /build2/api/jobspecs/events/offset` return
`403 Build2:PermissionDenied` for `build2:read-job-spec-events` on
`ri.build2.main.service.root`. This is a **service-root** permission, not a
resource-level one — an ordinary user token will not have it, so the event-stream
approach to enumerating job specs is not viable for a client like pltr-cli.

---

# branch-service — `BranchService`

Base path: `/branch-service/api`. Note this service uses **`PUT` for read operations**
(a Conjure convention for reads with a request body) — `PUT` here does not imply a write.

## `PUT /branch-service/api/branch/dependency-graph/{branchRid}` ⚠️ contract only

`getResourceDependencyGraph`.

**Request:** an **empty JSON object**. `branchRid` is carried entirely in the path.

```
PUT /branch-service/api/branch/dependency-graph/ri.branch.main.branch.<uuid>
{}
```

**This endpoint deserializes strictly** — unlike monocle and build2, it **rejects unknown
keys**:

| body | status |
|---|---|
| *(none)* | `400 Default:InvalidArgument` |
| `{}` | `403 Branch:PermissionDeniedError` (reached authz ⇒ body valid) |
| `{"__unknown__":1}` | `422 Conjure:UnprocessableEntity` |
| `{"branchRid":"ri.branch.main.branch..."}` | `422 Conjure:UnprocessableEntity` |

**Not live-verified with a real graph.** Probes with syntactically valid but nonexistent
branch RIDs return:

```json
{"errorCode":"PERMISSION_DENIED","errorName":"Branch:PermissionDeniedError",
 "parameters":{"expectedOperation":"branch:view-branch",
               "rid":"ri.branch.main.branch.00000000-0000-0000-0000-000000000000"}}
```

The service is reachable and the body contract is confirmed; only the response payload is
unverified. See UNRESOLVED for why no real `branchRid` could be obtained.

## `GET /branch-service/api/branch/backend-information` ✅

No body. Returns per-consumer protocol versions — useful for capability detection:

```json
{"consumerVersions":{"authoring":{"consumerVersion":"V7"},
                     "restricted-view":{"consumerVersion":"V7"},
                     "module":{"consumerVersion":"V7"},
                     "monitor":{"consumerVersion":"V8"},
                     "object-view":{"consumerVersion":"V7"},
                     "ontology":{"consumerVersion":"V8"},
                     "eddie":{"consumerVersion":"V7"},
                     "service:third-party-applications":{"consumerVersion":"V8"},
                     "service:foundry-ml-live":{"consumerVersion":"..."}}}
```

## Other `BranchService` endpoints (paths from sourcemap; not verified)

Read (`PUT`): `/branch/load/{branchRid}`, `/branch/proposal/load/{proposalRid}`,
`/branch/branch-resources/{branchRid}`, `/branch/branch-versions/{branchRid}`,
`/branch/check-deployable/branch/{branchRid}/proposal/{proposalRid}`,
`/branch/check-previewable/branch/{branchRid}`, `/branch/deployment-record`,
`/branch/deployment-history`, `/branch/validate-*`.

**Mutating — do not call:** `POST /branch/create`, `PUT /branch/add-resources`,
`PUT /branch/update/{branchRid}`, `PUT /branch/close/{branchRid}`,
`POST /branch/proposal/create`, `PUT /branch/proposal/update/{proposalRid}`,
`PUT /branch/proposal/close/{proposalRid}`,
`POST /branch/deploy-proposal/branch/{branchRid}/proposal/{proposalRid}`,
`PUT /branch/abort-deployment`, `PUT /branch/mark-active/{branchRid}`.

---

# Error taxonomy (how to read failures)

| status / errorName | meaning |
|---|---|
| `400 Default:InvalidArgument`, empty `parameters` | body deserialized, but a **required field is missing**. No field name is given. |
| `422 Conjure:UnprocessableEntity` | body **failed to deserialize** — wrong type for a known field, wrong top-level shape, or (strict services only) an unknown key. |
| `403 <Service>:PermissionDenied…` | reached authorization; `parameters.operation` / `expectedOperation` names the missing permission and `parameters.rid`/`resources` the resource. |
| `200` with empty `[]` / `{}` / `{"nodes":[]}` | valid query, genuinely no results — **not** an error. Common and expected. |

The `400`-vs-`422` split is the whole basis of the oracle described at the top.

---

# UNRESOLVED

## 1. `POST /build2/api/jobspecs/get-branches` — second required field not identified

`datasetRids` is confirmed real (wrong-typed value → `422`; a map value → `422`, so it is
a list). But every request still returns `400 Default:InvalidArgument`, so at least one
further required field exists.

Tried and rejected as unknown (all → `400`, i.e. ignored): `branch`, `branches`,
`branchIds`, `branchName`, `branchId`, `branchRid`, `branchRids`, `branchFallbacks`,
`fallbacks`, `fallbackBranches`, `fallbackBranchIds`, `branchPrefix`, `branchFilter`,
`branchesToCheck`, `candidateBranches`, `allBranches`, `resolvedBranches`,
`datasetRidsToBranches`, `datasetRidsToIgnore`, `jobSpecRids`, `depth`, `limit`,
`pageSize`, `offset`, `pageToken`, `maxResults`, `page`, `pageRequest`, `token`,
`query`, `searchQuery`, `includeDeleted`, `onlyLatest`, `workerTypes`, `jobSpecTypes`,
`severs`, `includeSever`, `types`, `kind`, `mode`, `options`, `config`, `settings`,
`context`, `scope`, `filter`, `filters`, `request`, `requests` (~120 candidates total).

Value variations also tried: `datasetRids` empty vs populated, and as a map.
`getBranches` has **no call site in any of the 68 shipped frontend bundles**, so the
bundle route cannot resolve it either.

Impact: low. `GET /build2/api/jobspecs/datasets/{datasetRid}` already returns job specs
keyed by branch and covers the likely use case.

## 2. `-nodes` endpoint variants always return `[]`

`downstream-jobspecs-nodes`, `upstream-jobspec-nodes`, and `connecting-jobspec-nodes`
(note the inconsistent pluralization — it is in the server contract, not a typo here)
accept exactly the same fields as their non-`-nodes` counterparts (confirmed via the
oracle: `datasetRids`, `branchFallbacks`, `depth` all resolve) and return `200 []`
**in every configuration tried** — both test datasets, `depth` omitted / `1` / `5`,
`branchFallbacks` as `{}` and `{"branches":[]}` — including cases where the non-`-nodes`
endpoint returns 3 job specs.

The docstrings say "Same as getDownstreamJobSpecs but returns only nodes", and direct
callers here when results exceed the 10 000-job-spec cap. Either they require a request
field not shared with the base endpoints (none surfaced by the oracle), or they are
gated by a permission that fails open to an empty list, or they are simply not populated
on this stack. **Not cracked.**

## 3. `federatedLink`, `provenanceLink`, `mlLink` field lists unknown

These 3 of the 8 union variants in `graphLink.js` never appeared in a 26-RID BFS across
object types, datasets, applications, and action types. Their existence and discriminant
names are certain (from the sourcemap); their **payload field lists are unknown**.

They are presumably emitted only on stacks using federated data sources, Foundry ML
models, or the legacy provenance service. A client should decode them defensively —
treat any unrecognized `type` as opaque and preserve the raw payload rather than
assuming a field set.

## 4. `branch-service` dependency-graph response payload unverified

No real `branchRid` could be obtained on this stack:

- `PUT /compass/api/branches-for-resources` returned `200 {"pages":{}}` for both the test
  dataset and the object type — no branches exist for them.
- `branch-service` has no list/search endpoint in the sourcemap's 24 `BranchService`
  methods; every read requires a `branchRid` you already hold.
- Probes with well-formed nonexistent RIDs (`ri.branch.main.branch.<uuid>` and
  `ri.ontology.main.branch.<uuid>`) return `403 Branch:PermissionDeniedError`, which is
  indistinguishable from "exists but not visible to this token".

The request contract (`PUT`, empty `{}` body, strict deserialization, RID in path) **is**
verified. The response schema is not. Most likely this stack simply has no Global
Branching branches.

## 5. V2 request types were not recoverable from source

The V2 field names documented above were derived from the live type-confusion oracle,
**not** from source. Because V2 has zero call sites in the shipped frontend and Conjure
interfaces leave no runtime trace, there is no artifact on this machine or host that
states the V2 request types. The V2 schemas here are empirically correct against this
stack but carry no source-of-truth backing — a further reason to prefer V3.

## 6. No sourcemaps for frontend bundles

`.js.map` and `.map` on `/assets/content-addressable-storage/frontend/<sha>.js` both
return `503`. All frontend-derived field names come from reading minified call sites.
Field-name string keys survive minification, so these are reliable — but surrounding
types, optionality, and defaults are not recoverable this way.

---

# Recommended client design for a pltr-cli fork

1. **Use V3 monocle endpoints.** They are what Palantir's own Data Lineage app calls.
   Build the `BranchSpec` tagged union once and reuse it.
2. **Two lineage planes, joined on dataset RID.** monocle gives ontology/application/
   dataset relationships; build2 gives transform edges. Neither is complete alone —
   monocle does not know about repos or transforms, and build2 does not know about
   object types or applications.
3. **Always send `branchFallbacks`** on build2 graph walks. Send `{"branches":[]}` for
   `master`; put the fallback chain in `branches` for a non-default branch.
4. **Filter build2 specs to `inputType`/`outputType == "foundry"`** when constructing
   data-lineage edges; everything else is build plumbing and creates false edges.
5. **Treat `200` + empty collection as a normal result**, not an error — it is the
   correct answer for un-indexed RID kinds and for datasets with no producing transform.
6. **Decode link unions defensively.** 3 of 9 variants were never observed; preserve
   unknown payloads verbatim rather than assuming a field set.
7. **Do not depend on the `-nodes` endpoints** or on `jobspecs/events` (service-root
   permission) — neither is usable from an ordinary user token on this stack.
