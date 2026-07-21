# Foundry Internal API — Reverse Indexes

**Status:** live-verified 2026-07-21 against `https://zap.usw-18.palantirfoundry.com`
**Audience:** implementers of a `pltr-cli` fork
**Scope:** undocumented internal services that answer *"what uses this?"* — the reverse
direction of the public read APIs.

These are **Conjure** services. Two conventions matter before you read further:

1. **`PUT` is sometimes a read.** Conjure uses the verb to signal request-body semantics,
   not mutation. `PUT /ontology/ontology/actionTypesForObjectType` is a pure read.
   Verb alone is not a safety signal — check the method name.
2. **Unknown request fields are silently ignored.** Sending `{"bogus": [...]}` returns
   `200` with an empty result, *not* an error. A `200` with empty data therefore does
   **not** prove your field names are right. Field-name correctness in this document was
   established by finding non-empty results or by type-mismatch `422`s, and each endpoint
   below records which.

## Source of truth

| Artifact | Use |
| --- | --- |
| `@palantir/mcp` v0.397.0 `dist/index.mjs.map` (3568 `sourcesContent` entries) | Service definitions: path, verb, doc comments |
| `@palantir/ontology-metadata-api` (public npm) | Real `.d.ts` request/response interfaces — 4647 files |
| `@palantir/compass-api`, `@palantir/function-registry-api`, `@palantir/stemma-api` | **Not on public npm** (`E404`). Types recovered by live probing only |

Because the compass / function-registry / stemma type packages are unavailable, their
schemas below are derived from observed traffic and are marked as such.

## Auth

```python
from pltr.auth.storage import CredentialStorage
c = CredentialStorage().get_profile('zap')
HOST, TOK = c['host'].rstrip('/'), c['token']
```

Headers on every call: `Authorization: Bearer <TOK>`, `Accept: application/json`,
`Content-Type: application/json` (when a body is sent).

## Test fixtures used

| Thing | RID |
| --- | --- |
| Ontology | `ri.ontology.main.ontology.1a944941-d587-4363-8314-d6274b7b0381` |
| Ontology version | `0000001c-bc1d-a666-3827-ae12acd28336` |
| Default branch | `ri.ontology.main.branch.860f46c0-5ee8-48d6-b360-59b571e108b3` |
| Object type `Program` | `ri.ontology.main.object-type.018c03f7-9bdb-44e5-91e1-73bfcbe19f98` |
| Object type `Account` | `ri.ontology.main.object-type.04c1741f-d148-43a3-991c-830b41f3fe5e` |
| Dataset (backs `Program`) | `ri.foundry.main.dataset.d9069f13-21cf-4086-8dd0-231fd10a1183` |
| Imported dataset | `ri.foundry.main.dataset.891f099f-f135-4cd2-afac-dd7606a1d602` |
| Importing project | `ri.compass.main.folder.c45999f8-0659-41d5-acbb-377bffa450a2` |

Scale of this stack: 36 object types in the target ontology (96 across all ontologies),
33 stemma repositories, 68 Compass folders, 286 Compass resources, 3 registered functions.

---

# 1. Ontology Metadata — `/ontology-metadata/api`

Base path prefix for every endpoint in this section: `/ontology-metadata/api`.

## 1.1 Object type → Action types

**`PUT /ontology-metadata/api/ontology/ontology/actionTypesForObjectType`**

*Which Actions touch this object type?* Returns actions that create, edit, or take the
object as a parameter — **including** actions attached to an interface the object type
implements.

### Request — `GetActionTypesForObjectTypeRequest`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `objectType` | `string` (ObjectTypeRid) | **yes** | |
| `ontologyVersion` | `string \| null` | no | **Deprecated.** Throws if sent together with `versionReference` |
| `versionReference` | `VersionReference \| null` | no | `{"type":"ontologyVersion","ontologyVersion":"..."}` or `{"type":"ontologyBranch","ontologyBranch":"..."}` |
| `pageToken` | `string \| null` | no | Object type and version must stay identical across pages |
| `pageSize` | `number \| null` | no | Capped at 100 |

### Real request / response

```json
PUT /ontology-metadata/api/ontology/ontology/actionTypesForObjectType
{"objectType": "ri.ontology.main.object-type.04c1741f-d148-43a3-991c-830b41f3fe5e", "pageSize": 3}
```

```json
200
{
  "actionTypes": [ { "actionTypeLogic": {...}, "metadata": {...} } ],
  "nextPageToken": "v1,ontologyVersion:0000001c-bc1d-a666-3827-ae12acd28336,actionTypeRid:ri.actions.main.action-type.1b4a576e-f5a1-4c2c-b020-c7ee8772adf5",
  "totalActionTypeCount": 7,
  "resolvedBranch": {
    "type": "default",
    "default": { "rid": "ri.ontology.main.branch.860f46c0-5ee8-48d6-b360-59b571e108b3" }
  }
}
```

Each `actionTypes[]` element has exactly two keys: `actionTypeLogic` and `metadata`.
`metadata` keys: `actionApplyClientSettings`, `actionLogConfiguration`, `apiName`,
`branchSettings`, `displayMetadata`, `entities`, `formContentOrdering`,
`notificationSettings`, `parameterOrdering`, `parameters`, `provenance`, `returnType`,
`rid`, `scenarioSettings`, `sections`, `stagingMediaSetRid`, `status`,
`submissionConfiguration`, `version`.

Trimmed `metadata`:

```json
{
  "rid": "ri.actions.main.action-type.1b4a576e-f5a1-4c2c-b020-c7ee8772adf5",
  "apiName": "edit-account",
  "version": "2.0",
  "entities": {
    "affectedObjectTypes": ["kid3ntuq.id-db503abf-e77a-11dd-58f9-2271fa1848ad"],
    "affectedLinkTypes": [],
    "affectedInterfaceTypes": [],
    "typeGroups": []
  },
  "status": { "type": "experimental", "experimental": {} }
}
```

> **Implementation gotcha.** `metadata.entities.affectedObjectTypes` contains
> **ObjectTypeId** values (`kid3ntuq.id-…`), *not* ObjectTypeRid values
> (`ri.ontology.main.object-type.…`). Do not try to join it against the RID you passed in
> — you must resolve IDs separately. This is the single most likely source of a silently
> empty reverse index in a client implementation.

### Semantics, pagination, limits

- **Deviation from the published type:** the live response includes
  `totalActionTypeCount`, which is absent from the `.d.ts` in
  `@palantir/ontology-metadata-api`. The shipped types lag the deployed service. Treat
  the type package as a floor, not a contract.
- Pagination confirmed: `pageSize: 3` → 3 results + token; replaying with `pageToken` →
  next 3. Token format is readable and encodes the pinned version:
  `v1,ontologyVersion:<ver>,actionTypeRid:<last-rid>`.
- `pageSize: 5000` returns `200` with all 7 results — the cap is applied **silently**, no
  error. Never rely on an oversized `pageSize` erroring.
- `resolvedBranch` tells you which branch actually served the read.

### Verified coverage

Swept all 36 object types. 20 have at least one associated action:

| Object type | Actions | | Object type | Actions |
| --- | --- | --- | --- | --- |
| Account | 7 | | Patient | 1 |
| TreatmentPlan | 7 | | CustomerAccount | 1 |
| Contact | 5 | | EnrollmentStatusEvent | 1 |
| Interaction | 3 | | MemoryNote | 1 |
| TreatmentPlanMedication | 3 | | AgentRun | 1 |
| Opportunity | 3 | | SourceEvidence | 1 |
| CommandReceipt | 2 | | ProgramEnrollment | 1 |
| EnrollmentMilestoneProgress | 2 | | ActionItem | 1 |
| Encounter | 2 | | EnrollmentAttemptSeries | 1 |
| AgentTask | 2 | | AgentToolCall | 1 |

The remaining 16 (including `Program`) return `actionTypes: []`,
`totalActionTypeCount: 0`.

---

## 1.2 Dataset → Ontology entities

**`POST /ontology-metadata/api/ontology/ontology/bulkLoadEntitiesByDatasources`**

*Which object types and link types are backed by this dataset?* The dataset→ontology
reverse index.

### Request — `OntologyBulkLoadEntitiesByDatasourcesRequest`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `datasourceBackingRids` | `Array<DatasourceBackingRid>` | **yes** | **Max 500**; errors above that |
| `loadRedacted` | `boolean \| null` | no | Default `false`. Only set true if you handle redacted entities |
| `includeObjectTypesWithoutSearchableDatasources` | `boolean \| null` | no | Default `false` |

`DatasourceBackingRid` is a **tagged union** — a bare RID string fails with `422`:

```json
{"type": "datasetRid", "datasetRid": "ri.foundry.main.dataset.…"}
```

All 12 variants: `datasetRid`, `streamLocatorRid`, `restrictedStreamRid`,
`restrictedViewRid`, `timeSeriesSyncRid`, `mediaSetRid`, `mediaSetViewRid`,
`geotimeSeriesIntegrationRid`, `tableRid`, `editsOnlyRid`, `directSourceRid`,
`derivedPropertiesSourceRid`. Each is `{"type": "<name>", "<name>": "<rid>"}`.

### Failure then success

```json
POST …/bulkLoadEntitiesByDatasources
{"datasourceBackingRids": ["ri.foundry.main.dataset.d9069f13-…"]}
```
```json
422 {"errorCode":"INVALID_ARGUMENT","errorName":"Conjure:UnprocessableEntity",
     "errorInstanceId":"1182bf51-8d05-4e68-804a-63f1e3f2ea06","parameters":{}}
```

```json
{"datasourceBackingRids": [{"type":"datasetRid","datasetRid":"ri.foundry.main.dataset.d9069f13-21cf-4086-8dd0-231fd10a1183"}]}
```
```json
200
{ "entities": [ [ { "type": "objectType", "objectType": { /* ObjectTypeLoadResponse */ } } ] ] }
```

### Semantics

- `entities` is an **array of arrays**, positionally aligned with the request list. Entry
  *i* holds every entity backed by datasource *i*. Empty inner array = not found, not
  visible, or no backing entity. **Do not** assume `entities[i]` is a single object.
- Each element is a union: `{"type":"objectType","objectType":…}` or
  `{"type":"linkType","linkType":…}`.
- Not paginated. Batch is the pagination mechanism — chunk at 500.
- `422` here carries no field detail; `parameters` is always `{}`. Debug by bisecting
  the request.

---

## 1.3 Object type → Link types

**`POST /ontology-metadata/api/ontology/linkTypesForObjectTypes`**

The current, supported way to get links for object types.

### Request — `GetLinkTypesForObjectTypesRequest`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `objectTypeVersions` | `{[ObjectTypeRid]: string \| null}` | **yes** | `null` value = latest |
| `objectTypeBranches` | `{[ObjectTypeRid]: string \| null}` | **yes** | Send `{}` if unused. A RID in **both** maps throws |
| `loadRedacted` | `boolean \| null` | no | Default `false` |
| `includeObjectTypesWithoutSearchableDatasources` | `boolean \| null` | no | Default `false` |
| `includeLinkTypesForDerivedPropertyLinkDefinitions` | `boolean \| null` | no | Default `false`. Returned links may not directly reference the object type |

### Real request / response

```json
POST /ontology-metadata/api/ontology/linkTypesForObjectTypes
{"objectTypeVersions": {"ri.ontology.main.object-type.018c03f7-9bdb-44e5-91e1-73bfcbe19f98": null},
 "objectTypeBranches": {}}
```

```json
200
{
  "linkTypes": {
    "ri.ontology.main.object-type.018c03f7-9bdb-44e5-91e1-73bfcbe19f98": [
      {
        "description": null,
        "definition": {
          "type": "oneToMany",
          "oneToMany": {
            "cardinalityHint": "ONE_TO_MANY",
            "manyToOneLinkMetadata": {
              "displayMetadata": {"displayName":"Program","groupDisplayName":null,
                                  "pluralDisplayName":"Programs","visibility":"NORMAL"},
              "typeClasses": [], "apiName": "program"
            },
            "objectTypeRidManySide": "ri.ontology.main.object-type.750751e2-ae94-40d0-b52e-17160b1de69f",
            "objectTypeRidOneSide": "ri.ontology.main.object-type.018c03f7-9bdb-44e5-91e1-73bfcbe19f98",
            "oneToManyLinkMetadata": {
              "displayMetadata": {"displayName":"Template Version","groupDisplayName":null,
                                  "pluralDisplayName":"Template Versions","visibility":"NORMAL"},
              "typeClasses": [], "apiName": "templateVersions"
            },
            "oneSidePrimaryKeyToManySidePropertyMapping": {
              "ri.ontology.main.property.5a58ed37-6359-434e-8fc2-7f4108c32a46":
                "ri.ontology.main.property.f7fa581f-c177-4b8b-9e02-a8963152975d"
            }
          }
        },
        "id": "kid3ntuq.program-to-template-versions",
        "rid": "ri.ontology.main.relation.4a7dc86f-5c6b-4adf-84c0-87dad362d62c",
        "status": {"type":"experimental","experimental":{}},
        "redacted": null
      }
    ]
  }
}
```

### Semantics

- `linkTypes` is keyed by the ObjectTypeRid you asked for. Missing / invalid versions
  yield an empty set rather than an error.
- Link RIDs use the `ri.ontology.main.relation.*` namespace even though these are
  LinkTypes — the legacy noun survives in the RID.
- `oneSidePrimaryKeyToManySidePropertyMapping` gives the join keys as property RIDs.
- Not paginated; batch via the map.

---

## 1.4 Object type → Link metadata (cross-ontology)

**`POST /ontology-metadata/api/ontology/getLinkMetadataForObjectTypes`**

Same question as 1.3 but considers the latest version across **potentially multiple
ontologies**, and takes a plain array.

### Request — `GetLinkMetadataForObjectTypesRequest`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `objectTypes` | `Array<string>` (ObjectTypeRid) | **yes** | **Max 50 per request** |

> **Trap.** This endpoint takes `objectTypes` (array). Endpoint 1.3 takes
> `objectTypeVersions` (map). Sending 1.3's body here returns `200 {"links": {}}` — a
> silent empty result, not an error. This is the concrete case that proves the
> unknown-field-ignored rule; budget for it in tests.

```json
POST /ontology-metadata/api/ontology/getLinkMetadataForObjectTypes
{"objectTypes": ["ri.ontology.main.object-type.018c03f7-9bdb-44e5-91e1-73bfcbe19f98"]}
```
```json
200 { "links": { "ri.ontology.main.object-type.018c03f7-…": [ /* LinkMetadata */ ] } }
```

Response shape mirrors 1.3: map keyed by ObjectTypeRid. Not paginated.

---

## 1.5 Object type → Relations (deprecated)

**`POST /ontology-metadata/api/ontology/relationsForObjectTypes`**

Legacy `BidirectionalRelation` view. **Deprecated** upstream in favour of 1.3; it cannot
express links backed by restricted views or multiple datasources. Documented for
completeness — prefer 1.3.

### Request — `GetRelationsForObjectTypesRequest`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `partialObjectTypeVersions` | `{[ObjectTypeId]: string}` | **yes** | Value must be a **real** ontology version — `""` returns `422` |
| `includeObjectTypesWithoutSearchableDatasources` | `boolean \| null` | no | Default `false` |

```json
POST /ontology-metadata/api/ontology/relationsForObjectTypes
{"partialObjectTypeVersions": {
  "ri.ontology.main.object-type.018c03f7-9bdb-44e5-91e1-73bfcbe19f98":
    "0000001c-bc1d-a666-3827-ae12acd28336"}}
```
```json
200 { "bidirectionalRelations": { "ri.ontology.main.object-type.018c03f7-…": [] } }
```

Empty array is correct here: this ontology's links are modern LinkTypes, which this
deprecated view does not represent. Note the field is `partialObjectTypeVersions`, **not**
`objectTypeVersions` — passing the latter returns `200 {"bidirectionalRelations": {}}`
(silently wrong).

Get the current version from
`POST /ontology-metadata/api/ontology/ontology/ontologies/load/all` with body `{}`:

```json
200
{"ontologies": {
  "ri.ontology.main.ontology.1a944941-…": {
    "apiName": "ontology-a75cc311-593f-4913-bb24-f4a2d923a7f9",
    "displayName": "Raava Ontology",
    "currentOntologyVersion": "0000001c-bc1d-a666-3827-ae12acd28336",
    "defaultBranchRid": "ri.ontology.main.branch.860f46c0-5ee8-48d6-b360-59b571e108b3"
  }, … }}
```

---

## 1.6 Shared property type → Object types

**`PUT /ontology-metadata/api/ontology/ontology/load/objectTypesForSharedPropertyTypes`**

*Which object types use this shared property type?*

### Request — `GetObjectTypesForSharedPropertyTypesRequest`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `sharedPropertyTypeRids` | `Array<string>` | **yes** | **Max 50** |
| `ontologyVersion` | `string \| null` | no | **Deprecated**; throws alongside `versionReference` |
| `versionReference` | `VersionReference \| null` | no | Defaults to latest of source **and importing** ontologies |

### Verification status — schema proven, no data on this stack

This ontology contains **zero** shared property types
(`PUT /ontology/ontology/load/{ontologyRid}/{ontologyVersion}/loadAllSharedPropertyTypes`
→ `{"sharedPropertyTypes": [], "nextPageToken": null}`), so no non-empty result exists to
capture. The field name is nonetheless **positively confirmed** by a typed error:

```json
{"sharedPropertyTypeRids": ["ri.ontology.main.shared-property-type.00000000-0000-0000-0000-000000000000"]}
```
```json
400
{"errorCode": "INVALID_ARGUMENT",
 "errorName": "OntologyMetadata:SharedPropertyTypesNotFound",
 "errorInstanceId": "53f7ca52-3a59-4f2d-b9d9-d23214834b1b",
 "parameters": {"sharedPropertyTypeRids": "[ri.ontology.main.shared-property-type.00000000-…]"}}
```

The service echoes the field back in `parameters` — the name is right and the value is
parsed. Empty list returns `200 {"objectTypeRidsBySharedPropertyTypeRid": {}}`.

Response: `{"objectTypeRidsBySharedPropertyTypeRid": {"<sptRid>": ["<objectTypeRid>", …]}}`.
Not paginated; batch at 50.

> This is the one endpoint here whose *populated* response shape is taken from the
> published `.d.ts` rather than observed traffic. Given `totalActionTypeCount` (§1.1)
> proved the types lag the service, expect possible extra fields.

---

# 2. Compass — `/compass/api`

Compass type definitions are **not published on npm**; everything below is observed.

## 2.1 Resource → Importing projects

**`POST /compass/api/projects/imports/importing-projects`**

*Which projects import these resources?* The headline Compass reverse index.

### Request (observed)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `resourceRids` | `Array<string>` | **yes** | Doc comment says at most 100 (see limits) |

### Field-name proof

Field naming here needed real evidence, because *every* variant returns `200`:

| Body | Result |
| --- | --- |
| `{}` | `200 {"projectRidsbyResource": {}}` |
| `{"bogus": [rid]}` | `200 {"projectRidsbyResource": {}}` |
| `{"rids": [rid]}` | `200 {"projectRidsbyResource": {}}` |
| `{"resources": [rid]}` | `200 {"projectRidsbyResource": {}}` |
| `{"resourceRids": null}` | `200 {"projectRidsbyResource": {}}` |
| `{"resourceRids": "<string not array>"}` | **`422`** — type *is* validated |
| `{"resourceRids": [80 real rids]}` | **`200` with data** |

The `422` on a wrong *type* plus a non-empty result on the right *name* pins it down.
Corroborated independently by §2.2, where `resourceRids` returns data and `resources`
returns empty against the same resource.

### Real request / response

```json
POST /compass/api/projects/imports/importing-projects
{"resourceRids": ["…80 real resource rids…"]}
```
```json
200
{"projectRidsbyResource": {
  "ri.foundry.main.dataset.891f099f-f135-4cd2-afac-dd7606a1d602":
    ["ri.compass.main.folder.c45999f8-0659-41d5-acbb-377bffa450a2"]}}
```

Dataset `891f099f…` is `[Example] Workshop Explainers`; the importing project resolves to
a folder whose `name` is literally the RID of another ontology
(`ri.ontology.main.ontology.d7f220ad-631f-459f-af20-963abc8e55ad`).

### Semantics, pagination, limits

- **Response key is `projectRidsbyResource`** — lowercase `b` in `by`. Not a typo in this
  document; match it exactly.
- Sparse map: resources with no importing project are **omitted**, not returned empty.
  Iterate your request list, not the response keys.
- Unknown / non-existent RIDs are dropped silently — a fabricated dataset RID added to a
  working request changed nothing.
- Requires `compass:view-project-imports` **on the importing project**, which lives on a
  separate Gatekeeper node from the project node and is *not* inherited through the
  hierarchy for private/discoverable projects. Projects you cannot see are filtered out
  silently, so an empty result can mean "none" *or* "not permitted".
- Not paginated.
- **Limit not enforced as an error:** the doc comment says at most 100 resources, but 101
  RIDs returned `200`. Chunk at 100 defensively; do not depend on an error.

## 2.2 Resource → Context within a project

**`POST /compass/api/projects/imports/{projectRid}/context`**

*Is this resource directly in the project, or imported into it?*

| Field | Type | Required |
| --- | --- | --- |
| `resourceRids` | `Array<string>` | **yes** (max 100 per doc comment) |

```json
POST /compass/api/projects/imports/ri.compass.main.folder.c45999f8-…/context
{"resourceRids": ["ri.foundry.main.dataset.891f099f-f135-4cd2-afac-dd7606a1d602"]}
```
```json
200
{"directlyInProject": [],
 "importedInProject": ["ri.foundry.main.dataset.891f099f-f135-4cd2-afac-dd7606a1d602"]}
```

The same call with `{"resources": [...]}` returns `200` with **both arrays empty** — the
clean A/B that confirms `resourceRids` for the whole Compass import family.

Semantics: a resource both imported *and* directly present is reported only as **direct**,
unless the caller lacks read permission on it, in which case it flips to **imported**.
Resources not registered in Compass but parented under project nodes report as direct.
Resources absent from both arrays have no context in the project. Not paginated.

## 2.3 Project → Imported resource RIDs (forward index)

**`POST /compass/api/projects/imports/{projectRid}/rids`**

The forward direction, included because it is the cheapest way to enumerate a project's
imports and seed a reverse lookup.

```json
POST /compass/api/projects/imports/ri.compass.main.folder.c45999f8-…/rids
{}
```
```json
200
{"values": ["ri.foundry.main.dataset.2107aa62-…", "ri.foundry.main.dataset.28a75e95-…", …],
 "nextPageToken": null}
```

118 values on the test project. **This one is paginated** — `values` + `nextPageToken`.
Empty body `{}` is accepted. Requires `compass:view-project-imports`.

---

# 3. Function Registry — `/function-registry/api`

Types not published on npm; everything below is observed.

## 3.1 Function → Consuming resources

**`GET /function-registry/api/functions/{functionRid}/usageHistory`**

*What actually calls this function, and when did it last run?* The richest reverse index
found — it crosses service boundaries into Workshop and Object Sentinel and carries
timestamps.

No request body. `functionRid` is `ri.function-registry.main.function.<uuid>`.

### Real response

```json
GET /function-registry/api/functions/ri.function-registry.main.function.2e404d88-0cd0-4932-8b7f-6e8eb9e3a536/usageHistory
```
```json
200
{"usageHistory": {"functionVersionUsage": {
  "2.0.0": {"resourceUsage": {
    "ri.workshop.main.module.9d03d52a-7a0f-4c8a-9686-c6fe0fb35d36":
      {"latestExecutionTime": "2026-01-25T02:51:18.250958823Z"}}},
  "1.0.0": {"resourceUsage": {
    "ri.object-sentinel.main.monitor.f94ab2aa-98c9-46eb-9b55-53d805dc802b":
      {"latestExecutionTime": "2026-01-08T22:13:20.352510694Z"},
    "ri.workshop.main.module.9d03d52a-7a0f-4c8a-9686-c6fe0fb35d36":
      {"latestExecutionTime": "2026-01-19T03:27:52.090039018Z"}}},
  "2.2.0": {"resourceUsage": {
    "ri.workshop.main.module.9d03d52a-7a0f-4c8a-9686-c6fe0fb35d36":
      {"latestExecutionTime": "2026-01-24T22:35:31.467165016Z"}}}}}}
```

### Schema (observed)

```
usageHistory.functionVersionUsage : { [semver]: { resourceUsage: { [consumerRid]: { latestExecutionTime: <ISO-8601 nanosecond> } } } }
```

### Semantics

- Keyed **by function version first**, consumer second. A consumer appears under every
  version it has invoked, so the same Workshop module legitimately repeats across keys.
  To answer "who uses this function at all", union the consumer RIDs across versions.
- Consumer RIDs are cross-service: `ri.workshop.main.module.*`,
  `ri.object-sentinel.main.monitor.*`. Expect other services.
- Timestamps are ISO-8601 with **nanosecond** precision — `2026-01-25T02:51:18.250958823Z`.
  Python's `datetime.fromisoformat` rejects 9 fractional digits before 3.11; truncate to
  microseconds.
- This is **execution telemetry**, not static analysis. A resource that references the
  function but has never run will not appear. Absence is not proof of no dependency.
- Version keys are plain semver strings and are **not** ordered in the response.
- Not paginated.

### Enumerating functions

```json
POST /function-registry/api/functions/ontology/{ontologyRid}/specs
{"apiNames": []}
```
```json
200
{"responses": {"treatmentPlanAgent": {
  "rid": "ri.function-registry.main.function.2e404d88-0cd0-4932-8b7f-6e8eb9e3a536",
  "version": "2.2.0",
  "locator": {"type": "computeModule|logic|aipAgent", …}}}}
```

Empty `apiNames` returns all. 3 functions on this stack: `hermesFunction` (empty usage
history), `treatmentPlanAgent`, `treatmentPlanAssistant`.

> **Write endpoints — do not call.** `PUT /functions/{functionRid}/usageHistory` and
> `PUT /functions/batch/usageHistory` are `updateUsageHistory` / `batchUpdateUsageHistory`.
> Despite sitting on the same path as the `GET`, they mutate. Not exercised.

## 3.2 Function repository → Ontology references

**`GET /function-registry/api/functionsRepositories/{repositoryRid}/ontologyReferences`**

*Which ontology entities does this code repository reference?*

No request body. `repositoryRid` is a **stemma** repository RID
(`ri.stemma.main.repository.<uuid>`) — the two services share the repository namespace.

```json
GET /function-registry/api/functionsRepositories/ri.stemma.main.repository.ec46bf54-795b-4275-ae2a-c8399fa40772/ontologyReferences
```
```json
200 {"objectTypeRids": [], "linkTypeRids": []}
```

Schema: `{objectTypeRids: string[], linkTypeRids: string[]}`. Verified `200` against 3
repositories; all empty on this stack (these repos declare no ontology imports). Not
paginated.

**Deprecated upstream** — the source comment directs callers to `getRepositoryImports`.

## 3.3 Function repository → Full imports (preferred)

**`GET /function-registry/api/functionsRepositories/{repositoryRid}/repositoryImports`**

The replacement for 3.2, and a much wider dependency surface.

```json
200
{"ontologyImports": {}, "models": [], "languageModels": [], "magritteSources": [],
 "functions": [], "globalFunctions": [], "types": [], "contracts": [],
 "baseVersion": "-1807454463"}
```

Nine fields; `ontologyImports` is a map, the rest are arrays. `baseVersion` is a signed
integer **as a string** — an optimistic-concurrency token for the corresponding
`setRepositoryImports` write. Identical `baseVersion` across all three probed repositories
suggests it is a schema/global version rather than per-repository content.

Prefer this over 3.2 for new clients. Not paginated.

### Enumerating repositories

`GET /stemma/api/repos` → `200`, 33 entries, shape
`[{"rid": "ri.stemma.main.repository.…", "sourceRid": null}]`.

---

# 4. Public API worth documenting

**`GET /api/v2/ontologies/{ontologyRid}/objectTypes/{apiName}?includeDatasources=true&preview=true`**

Documented and supported, but **the repo currently marks this unsupported as CAP-14** —
that is wrong and should be corrected. It is the only *public* route to
property→column mapping.

```json
200 (datasources excerpt)
[{"rid": "ri.ontology.main.datasource.49abfd07-39de-4f72-afc1-329e60d2f659",
  "definition": {
    "type": "dataset",
    "datasetRid": "ri.foundry.main.dataset.d9069f13-21cf-4086-8dd0-231fd10a1183",
    "branch": "master",
    "propertyMapping": {
      "description": {"type": "column", "column": "description"},
      "name":        {"type": "column", "column": "name"},
      "active":      {"type": "column", "column": "active"},
      "programId":   {"type": "column", "column": "program_id"}}}}]
```

`propertyMapping` is keyed by property **apiName** and gives the physical column —
note `programId` → `program_id`, so the mapping is not derivable by naming convention and
must be read. Combined with §1.2 this closes the loop in both directions between datasets
and object types.

Enumerate object types with
`GET /api/v2/ontologies/{ontologyRid}/objectTypes?pageSize=100&preview=true` →
`{"data": [{"apiName", "rid", …}], "nextPageToken"}`. 36 on this ontology.

---

# 5. Implementation notes for a pltr-cli fork

1. **Never treat `200` as validation.** Unknown fields are ignored across every service
   here. Pin field names with a fixture that returns non-empty data, and assert on it in
   CI — otherwise a rename upstream degrades to a silently empty index.
2. **Verb is not a safety signal.** `PUT` reads exist (§1.1, §1.6). Gate writes on the
   Conjure *method name*, not the HTTP verb. The concrete hazard:
   `GET /functions/{rid}/usageHistory` reads, `PUT` on the same path writes.
3. **Trust the service over the type package.** `totalActionTypeCount` is live but absent
   from the published `.d.ts`. Parse permissively; do not use strict schema validation
   that drops unknown response fields.
4. **ID vs RID.** `actionTypes[].metadata.entities.affectedObjectTypes` returns
   ObjectTypeIds (`kid3ntuq.id-…`), not RIDs. Resolve before joining.
5. **Limits are mostly silent.** `pageSize: 5000` and 101 Compass resource RIDs both
   returned `200`. Enforce documented caps client-side: 500 datasource RIDs, 100 SPT/50
   per request as noted, 100 Compass resources, 50 object types for §1.4.
6. **Permissions produce empty, not 403.** Compass import reads filter invisible projects
   silently. Surface "0 results" as ambiguous in CLI output rather than asserting "no
   dependencies".
7. **Conjure `422`/`400` bodies are unhelpful** — `parameters` is usually `{}`. §1.6 is the
   exception and echoes the field. Bisect requests to localise failures, and log
   `errorInstanceId` for support.
8. **Pagination is inconsistent.** Only §1.1 and §2.3 paginate. Everything else batches.
   Do not write a generic paginator across these services.

---

# UNRESOLVED

Items attempted and not cracked. Each lists exactly what was tried.

### `POST /compass/api/projects/imports/{projectRid}` — `getImports` request shape

Returns `400 Default:InvalidArgument` for every body attempted. `parameters` is `{}`, so
the service gives no hint about the missing field.

Tried against project `ri.compass.main.folder.c45999f8-0659-41d5-acbb-377bffa450a2`:
`{}`, `{"importTypes": ["FILE_SYSTEM"]}`, `{"importTypes": ["EXTERNAL"]}`,
`{"importTypes": ["fileSystem"]}`, `{"importTypes": []}`, `{"importType": "FILE_SYSTEM"}`.

The enum values are certain — `ImportType.EXTERNAL = "EXTERNAL"` and
`ImportType.FILE_SYSTEM = "FILE_SYSTEM"`, recovered from
`compass-api/compass-api-project-imports/importType.js`. The **wrapper field name** is
what is missing. `@palantir/compass-api` is not on public npm (`E404`), so no `.d.ts`
exists to consult, and the source map ships only compiled `.js` with types erased.

**Mitigation:** §2.3 (`/rids`) returns the same information as RIDs with pagination and
accepts `{}`. Use it instead. Resolving `getImports` needs the compass `.d.ts` from an
internal registry, or a captured browser request from the Compass project-imports UI.

### §1.6 populated response shape — no data on this stack

`objectTypesForSharedPropertyTypes` is schema-verified and field-name-confirmed (typed
`OntologyMetadata:SharedPropertyTypesNotFound` echoing `sharedPropertyTypeRids`), but this
ontology defines **zero** shared property types, so no populated response was captured.
`loadAllSharedPropertyTypes` at version `0000001c-bc1d-a666-3827-ae12acd28336` returns
`{"sharedPropertyTypes": [], "nextPageToken": null}`. The populated shape is taken from
the `.d.ts` and, per note 3 above, may omit live-only fields. Needs a stack with SPTs.

### §3.2 / §3.3 populated shapes — no data on this stack

`ontologyReferences` and `repositoryImports` both return `200` with the full envelope, but
every array is empty across the 3 repositories probed (of 33 available from
`GET /stemma/api/repos`). Element schemas for `models`, `languageModels`,
`magritteSources`, `functions`, `globalFunctions`, `types`, `contracts` and the
`ontologyImports` map value are therefore **unknown**. Only the top-level envelope is
confirmed. Sweeping all 33 repositories may find a populated one; not attempted here.

### `baseVersion` semantics in §3.3

Observed as `"-1807454463"` — identical across all 3 repositories, which argues against a
per-repository content hash. Inferred to be an optimistic-concurrency token for
`setRepositoryImports` from naming and position alone. Not confirmed: confirming it would
require a write, which is out of scope.

### Not attempted — write-capable endpoints

Excluded by the read-only constraint, listed so a future pass does not mistake them for
gaps: `POST /functionsRepositories/{rid}/ontologyReferences/add` and `/remove`,
`POST /functionsRepositories/{rid}/repositoryImports`,
`PUT /functions/{functionRid}/usageHistory`, `PUT /functions/batch/usageHistory`,
`POST /projects/imports/{projectRid}/import`, `DELETE /projects/imports/{projectRid}/import`,
`PUT /projects/imports/{projectRid}/can-import`, `POST /ontology/ontology/modify`.

### Prior-session dead ends (not re-attempted)

From the shared endpoint audit log, for the record: `POST /build2/api/jobspecs/branches/master/downstream-jobspecs`
returned `400` on 13 attempts, and `POST /graphql-gateway/api/bulk?q=GetObjectTypeDependents`
returned `422` on 5 attempts. Both were superseded by the ontology-metadata endpoints
documented above, which answer the same questions successfully.

---

## Audit

Every request issued while producing this document is appended to
`/Users/master/.claude/jobs/b1d5cafa/tmp/endpoint_audit.log`
(tab-separated: timestamp, method, path, status). All calls were reads. No resource was
created, updated, or deleted.
