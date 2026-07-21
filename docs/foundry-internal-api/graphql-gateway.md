# Foundry `graphql-gateway` — Undocumented Internal API

Reverse-engineered reference for the Palantir Foundry internal GraphQL gateway, for
implementation in a `pltr-cli` fork.

- **Stack under test:** `https://zap.usw-18.palantirfoundry.com`
- **Date verified:** 2026-07-21
- **Client impersonated:** `hubble/6.525.9 forge-graphql-client/0.0.0`
- **Method:** live calls with a real bearer token + GraphQL validation-error oracles
  (introspection is crippled — see below). Every query text and response sample in this
  document was executed against the live stack and captured verbatim.

> **Status legend.** Everything is VERIFIED (executed live, output captured) unless
> explicitly tagged **UNVERIFIED** or listed in [NOT WORKING / UNKNOWN](#not-working--unknown).

---

## 1. Endpoint

```
POST /graphql-gateway/api/bulk
POST /graphql-gateway/api/bulk?q=<CommaSeparatedOperationNames>
```

The `?q=` query parameter is **optional and non-authoritative**. It appears to be
telemetry/routing metadata only.

Verified: a request with **no** `q=` parameter returns `200` and correct data:

```
POST /graphql-gateway/api/bulk          -> 200
data:{"data":{"me":{"id":"9d827132-2bac-4f3e-b436-5ce8102947f2","username":"richardicruz25@gmail.com"}},"extensions":{"requestIndex":0}}
```

The `q=` value does **not** have to match the operations sent, and does not restrict
which fields resolve. Routing is driven entirely by the request body.

### Response transport

The endpoint always responds with **Server-Sent Events**, regardless of the `Accept`
header value tested. See [§6 SSE format](#6-sse-response-format).

---

## 2. Authentication

A single header is required:

```
Authorization: Bearer <FOUNDRY_TOKEN>
```

The token used was a standard Foundry API token read from the `pltr-cli` credential
store. No CSRF token, cookie, or session header was needed.

```python
from pltr.auth.storage import CredentialStorage
c = CredentialStorage().get_profile('zap')
HOST, TOK = c['host'].rstrip('/'), c['token']
```

### Full working header set

```
Authorization:     Bearer <TOK>
Accept:            text/event-stream
Content-Type:      application/json
fetch-user-agent:  hubble/6.525.9 forge-graphql-client/0.0.0
```

**Header necessity — UNVERIFIED.** Each header above was present on every successful
call. I did not run an ablation to determine whether `fetch-user-agent` or the
`Accept` value are individually required. Treat the full set as the safe default.

---

## 3. Request envelope

The body is a **batched persisted-operation envelope**, not a plain GraphQL POST.

```json
{
  "operations": {
    "<hash>": "<full GraphQL query text>"
  },
  "requests": [
    {
      "hash": "<hash>",
      "name": "<operation name>",
      "variables": { }
    }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `operations` | `map<string, string>` | Key is an arbitrary client-chosen ID. **Not** required to be `"0"` — verified working with `"a"` and `"b"`. Value is the full GraphQL document text. |
| `requests` | `array` | One entry per execution. Multiple entries may reuse the same `hash` with different `variables`. |
| `requests[].hash` | `string` | Must be a key present in `operations`, else HTTP 404. |
| `requests[].name` | `string` | Must equal the operation name inside the document text, else HTTP 500. |
| `requests[].variables` | `object` | GraphQL variables. |

### Critical constraints

1. **`name` must match the document's operation name.** A mismatch produces an
   uncaught HTTP 500, not a GraphQL error. Verified:
   ```
   operations: {"a": "query OpA { me { id } }"},  requests: [{hash:"a", name:"WrongName"}]
   -> HTTP 500 {"errorCode":"INTERNAL","errorName":"Default:Internal", ...}
   ```
2. **The document is sent in full.** Despite the "persisted operation" shape, the
   server does not require pre-registration — arbitrary query text is accepted and
   executed. This is what makes the gateway usable from a CLI.
3. **`requests` may be longer than `operations`** — batching N executions of one
   document is the normal pattern.

### Minimal Python client

```python
import urllib.request, json

def gql(host, tok, name, query, variables=None):
    payload = {"operations": {"0": query},
               "requests": [{"hash": "0", "name": name, "variables": variables or {}}]}
    r = urllib.request.Request(host + "/graphql-gateway/api/bulk",
                               data=json.dumps(payload).encode(), method="POST")
    r.add_header("Authorization", "Bearer " + tok)
    r.add_header("Accept", "text/event-stream")
    r.add_header("Content-Type", "application/json")
    r.add_header("fetch-user-agent", "hubble/6.525.9 forge-graphql-client/0.0.0")
    with urllib.request.urlopen(r, timeout=90) as x:
        body = x.read().decode()
    return [json.loads(l[5:]) for l in body.splitlines()
            if l.startswith("data:") and l[5:].strip()]
```

---

## 4. Introspection — enabled but deliberately crippled

**Introspection is NOT disabled.** It returns HTTP 200 and a valid, well-formed
`__schema` result. But the schema it returns is a **20-type stub** that does not
describe the executable schema.

The full standard `IntrospectionQuery` returns exactly these 20 types:

```
SCALAR  Any, Boolean, DateTime, Float, ID, Int, Long, PageToken, RID, String
OBJECT  Query  (fields: me)
OBJECT  User   (fields: id, username, firstName, lastName, fullName, _id)
OBJECT/ENUM  __Directive __DirectiveLocation __EnumValue __Field __InputValue
             __Schema __Type __TypeKind
```

`queryType = Query`, `mutationType = null`, and `Query` has exactly **one** field: `me`.

### Proof that introspection does not reflect the executable schema

The decisive test — request a real field and `__schema` **in the same document**:

```graphql
query GetObjectTypeDependents($rid: RID!) {
  objectTypeV2(identifier: {rid: $rid}) { _id __typename }
  __schema { types { kind name } }
}
```

Real response:

```json
{"data":{
  "objectTypeV2":{"_id":"+ROOJjpoFnh/MQkpoH9cgIyW0lXCx/1np0zbKdgfvOI=","__typename":"ObjectType"},
  "__schema":{"types":[{"kind":"SCALAR","name":"Any"}, ... 20 types ..., {"kind":"ENUM","name":"__TypeKind"}]}
 },"extensions":{"requestIndex":0}}
```

`objectTypeV2` **resolves with real data** in the very same response where `__schema`
omits it and omits every type it touches. Related confirmations:

```
__type(name:"ObjectType")        -> {"data":{"__type":null}}
__type(name:"ResourceMetadata")  -> {"data":{"__type":null}}
__type(name:"WorkshopModule")    -> {"data":{"__type":null}}
__type(name:"LinkType")          -> {"data":{"__type":null}}
__type(name:"Project")           -> {"data":{"__type":null}}
__type(name:"Folder")            -> {"data":{"__type":null}}
__type(name:"Query")             -> {"kind":"OBJECT","name":"Query","fields":[{"name":"me", ...}]}
```

**Conclusion:** the introspection resolver is bound to a minimal root/identity schema
while execution and validation run against the full stitched supergraph. This is not
routing-dependent — naming the operation `GetObjectTypeDependents`, adding `?q=`, or
co-locating with a working field all produce the same stub. **A schema dump via
introspection is not achievable.**

### The workaround — validation-error oracles

The **validator** sees the real schema even though introspection does not. Four error
classes leak schema structure, and GraphQL reports *all* validation errors for a
document at once — so ~70 candidate fields can be tested per request.

| Oracle | Trigger | Leaks |
|---|---|---|
| `FieldUndefined` | request a non-existent field | which fields do **not** exist, **and the parent type's real name** |
| `SubselectionRequired` | request a composite field as a leaf | the field's **exact type name**, including `!`/`[]` |
| `MissingFieldArgument` | omit a required argument | **required argument names** |
| `WrongType` (variable position) | declare a variable with a wrong type | the **exact expected argument type** |
| `UnknownType` / `InvalidFragmentType` | spread `... on Foo` | whether a **type name exists**, and union/interface membership |

Examples, verbatim:

```
Validation error (FieldUndefined@[objectTypeV2/zzzBogusField]) :
    Field 'zzzBogusField' in type 'ObjectType' is undefined
Validation error (SubselectionRequired@[objectTypeV2/dependents]) :
    Subselection required for type 'OntologyDependentPage!' of field 'dependents'
Validation error (MissingFieldArgument@[objectTypeV2]) :
    Missing field argument 'identifier'
Validation error (WrongType@[objectTypeV2]) :
    argument 'identifier' with value 'ObjectValue{objectFields=[ObjectField{name='zzzBogus', value=NullValue{}}]}'
    contains a field not in 'ObjectTypeIdentifier': 'zzzBogus'
Validation error (InvalidFragmentType@[search]) :
    Fragment cannot be spread here as objects of type 'SearchTitlesResult' can never be of type 'ObjectTypeVersion'
```

The variable-position oracle gives exact argument types:

```graphql
query GetObjectTypeDependents($v: Int!) { objectTypeV2(identifier: $v) { __typename } }
```
```
Variable 'v' of type 'Int!' used in position expecting type 'ObjectTypeIdentifier!'
```

Every type and argument signature in this document was recovered this way.
Working probe scripts: `walk.py`, `root.py`, `typeprobe.py`, `argprobe.py`
(in the research scratch directory, not shipped).

---

## 5. The `_id` → RID problem — SOLVED

### The problem

The hubble client's fragments select only `_id`, which returns an opaque base64 blob:

```json
{"_id": "+ROOJjpoFnh/MQkpoH9cgIyW0lXCx/1np0zbKdgfvOI=", "__typename": "ObjectType"}
```

`_id` is the **Relay/Apollo client-cache normalization key**, not a Foundry
identifier. It is not a RID and there is no client-side decoding path to one.

### The answer

**Do not decode `_id`. Just ask for `rid` — it exists and was simply never selected by
the client fragments.**

`ResourceMetadata` exposes these directly as scalars:

```
rid   name   description   path   alias   status   visibility   projectRid
```

`ObjectType` exposes `rid` and `id`. Verified field sets:

**`ObjectType`** — complete field list (probed exhaustively):

| Field | Type |
|---|---|
| `rid` | `RID` — the object-type RID |
| `id` | `String` — short id, e.g. `knbsr0em.flights` |
| `favorited` | scalar |
| `latest` | `ObjectTypeVersion` — **carries `displayName`/`apiName`** |
| `metadata` | `ResourceMetadata` |
| `ontology` | `Ontology` |
| `branch` | `ObjectTypeBranch` |
| `dependents` | `OntologyDependentPage!` |
| `permissions` | `ObjectTypePermissions!` |
| `_id`, `__typename` | meta |

**`ResourceMetadata`** — complete field list:

| Field | Type |
|---|---|
| `rid` | `RID` |
| `name` | `String` |
| `description` | `String` |
| `path` | `String` — full Compass path |
| `alias` | `String` |
| `status` | scalar |
| `visibility` | scalar, e.g. `NORMAL` |
| `projectRid` | `RID` |
| `openUrl` | scalar |
| `trashedStatus` | scalar |
| `autosaved` | scalar |
| `favorited` | scalar |
| `type` | `ResourceTypeMetadata` — `{ name description }`, e.g. `Module`, `Link type` |
| `parent` | `ResourceMetadata` |
| `ancestors` | `[ResourceAncestor!]!` |
| `children` | `ResourceMetadataPage!` |
| `project` | `Project` |
| `namespace` | `Namespace` |
| `created` / `modified` | `Attribution` — `{ time }` |
| `branch(branchName:)` | `BranchMetadata` |
| `branches` | `[BranchMetadata!]!` |
| `defaultBranch` | `BranchMetadata` |
| `resource` | `Resource` |
| `tags` | `[Tag!]!` |
| `markings` | `[MarkingInfo!]!` |
| `collections` | `[Collection!]!` |
| `classification` | `Classification!` |
| `promotion` | `PromotedApplication` |
| `permissions` | `ResourceMetadataPermissions!` |
| `roleGrants` | `[ResourceRoleGrant!]!` |
| `_id`, `__typename` | meta |

**Note:** `ResourceMetadata.name` is often the RID string itself for ontology-internal
resources (link types, object types). Use `type.name` to classify, and rely on
`ObjectType.latest.displayName` for a human label of an object type.

---

## 6. SSE response format

`Content-Type` is `text/event-stream`. The body is a sequence of `data:` lines
separated by blank lines. There is no `event:` line, no `id:` line, and **no
terminating sentinel** (no `[DONE]`); the stream ends when the connection closes.

```
data:{"data":{...},"extensions":{"requestIndex":1}}
<blank line>
data:{"data":{...},"extensions":{"requestIndex":0}}
<blank line>
```

### Correlation and ordering — critical

Each event carries `extensions.requestIndex`, a **0-based index into the `requests`
array**.

**Events arrive OUT OF ORDER.** This is not theoretical — it was observed on the first
batch test. Verified raw body from a 4-request batch:

```
data:{"data":{"resourceMetadata":{...}},"extensions":{"requestIndex":1}}

data:{"data":{"objectTypeV2":{"rid":"ri.ontology.main.object-type.1ca5ae2e-...","id":"knbsr0em.flights","__typename":"ObjectType"}},"extensions":{"requestIndex":0}}

data:{"data":{"objectTypeV2":{"rid":"ri.ontology.main.object-type.018c03f7-...","id":"kid3ntuq.program","__typename":"ObjectType"}},"extensions":{"requestIndex":2}}

data:{"data":{"objectTypeV2":null},"extensions":{"requestIndex":3}}
```

Index 1 was delivered before index 0. **Clients MUST demultiplex on
`extensions.requestIndex` and must not rely on arrival order.**

### Parsing rules

1. Split on newlines; keep lines starting with `data:`.
2. Strip the `data:` prefix (no space after the colon in observed output) and parse JSON.
3. Route each event by `extensions.requestIndex`.
4. Expect exactly one event per entry in `requests` (observed: 4 requests → 4 events).
5. A single event may contain `data`, `errors`, or both.

### Partial failure

Batching is **fully independent per request**. In the batch above, request 3 used a
non-existent object-type RID and returned `{"objectTypeV2": null}` — no error, no
effect on siblings. One failing request does not abort the batch or change the HTTP
status.

---

## 7. Error formats

Three distinct error surfaces. Note that **most GraphQL-level errors return HTTP 200**.

### 7a. GraphQL errors — HTTP 200, inside SSE

Standard GraphQL error shape with a Palantir `classification` extension.

```json
{"errors":[{"message":"Validation error (FieldUndefined@[objectTypeV2/zzzBogusField]) : Field 'zzzBogusField' in type 'ObjectType' is undefined",
            "locations":[{"line":1,"column":85}],
            "path":["objectTypeV2","zzzBogusField"],
            "extensions":{"classification":"ValidationError"}}],
 "extensions":{"requestIndex":0}}
```

Observed `classification` values: `ValidationError`, `InvalidSyntax`.

Syntax error example:

```json
{"errors":[{"message":"Invalid syntax with offending token '<EOF>' at line 1 column 18",
            "locations":[{"line":1,"column":18}],
            "extensions":{"classification":"InvalidSyntax"}}],
 "extensions":{"requestIndex":0}}
```

### 7b. Envelope errors — HTTP 4xx/5xx, Palantir Conjure JSON (NOT SSE)

These bypass SSE entirely and return a plain JSON error object.

Unknown `hash` → **HTTP 404**:
```json
{"errorCode":"NOT_FOUND","errorName":"GraphQl:OperationHashNotFound",
 "errorInstanceId":"692fdd1e-3030-49c2-a934-14aac323a9d0",
 "parameters":{"hashes":"[NOPE]"}}
```

`name` not matching the document's operation name → **HTTP 500**:
```json
{"errorCode":"INTERNAL","errorName":"Default:Internal",
 "errorInstanceId":"c89d3841-23f2-4085-8c36-8354e5b1c40b","parameters":{}}
```

### 7c. Null-on-missing

A well-formed query against a non-existent RID returns `null` for that field with **no
error**:

```json
{"data":{"objectTypeV2":null},"extensions":{"requestIndex":3}}
```

Clients must distinguish "not found" (`null`, no error) from "not permitted" — these
were not observed to differ, see [NOT WORKING / UNKNOWN](#not-working--unknown).

### Client error-handling checklist

1. Non-2xx → parse as Conjure JSON (`errorCode` / `errorName` / `errorInstanceId`), not SSE.
2. 200 → parse SSE, then check `errors` **per event**.
3. `data.<field> === null` with no `errors` → not found.
4. Never assume event order.

---

## 8. Query root — verified fields

Recovered by probing the `Query` root with the `FieldUndefined` oracle. Introspection
claims `Query` has only `me`; in reality it has at least these 21 fields, plus 12 more
confirmed from client bundles (§9).

| Field | Required args | Arg type | Returns |
|---|---|---|---|
| `me` | — | — | `User!` |
| `principal` | `id` | `String!` | `Principal` |
| `objectTypeV2` | `identifier` | `ObjectTypeIdentifier!` | `ObjectType` |
| `objectType` | `rid` | `RID!` | `ObjectType` |
| `objectTypesV2` | `pageSize` | `Int!` | `ObjectTypeVersionPage!` |
| `linkType` | `rid` | `RID!` | `LinkType` |
| `linkTypes` | `pageSize` | `Int!` | `LinkTypeVersionPage!` |
| `ontology` | `rid` | `RID!` | `Ontology` |
| `ontologyBranch` | `rid` | `RID!` | `OntologyBranch` |
| `actionType` | `rid` | `RID!` | `ActionType` |
| `actionTypes` | `pageSize` | `Int!` | `ActionTypeVersionPage!` |
| `function` | `rid` | `RID!` | `Function` |
| `functions` | `pageSize` | `Int!` | `FunctionSearchPage!` |
| `resourceMetadata` | `rid` | `RID!` | `ResourceMetadata` |
| `schedule` | `scheduleRid` | `RID!` | `Schedule` |
| `schedules` | `pageSize` | `Int!` | `ScheduleSearchPage!` |
| `build` | `buildRid` | `RID!` | `BuildReport` |
| `job` | `rid` | `RID!` | `JobReport` |
| `jobs` | `pageSize`, `filter` | `Int!`, `JobFilterInput!` | `JobSearchResultPage!` |
| `search` | `title`, `limit` | `String!`, `Int!` | `[SearchTitlesResult!]!` |
| `searchResources` | `filter`, `pageSize` | `ResourceSearchFilter!`, `Int!` | `ResourceSearchResultsPage!` |

Optional args recovered via the variable-position oracle:

```
objectTypesV2 : pageToken: String,     filter: ObjectTypeFilter
linkTypes     : pageToken: PageToken,  filter: LinkTypeFilter
actionTypes   : pageToken: String,     filter: ActionTypeFilter
functions     : pageToken: String,     filter: FunctionSearchFilter
schedules     : pageToken: String,     filter: ScheduleSearchFilter
jobs          : pageToken: String
searchResources: pageToken: String,    sort: ResourceSearchSort
```

Additional root fields confirmed to exist (from bundle mining, existence re-verified live):

```
objectTypeById   linkTypeById   globalBranch   defaultOrGlobalBranch
collections      projectPortfolio   notifications   favorites   recents
```

Confirmed **NOT** to exist on `Query`:

```
user users group groups objectTypes linkTypeV2 ontologyV2 ontologies
interfaceType interfaceTypes sharedPropertyType functionSpec resource resourceV2
resources resourceByRid resourcesByRids project projects space spaces folder folders
compass dataset datasets datasetV2 builds workshop workshopModule module modules
searchObjectTypes searchProjects searchSpaces branch branches foundryBranch
lineage impact dependents dependencies usages node nodes byRid byId entity entities
actionTypeById ontologyBranchV2 portfolio
```

### `ObjectTypeIdentifier` is a `@oneOf` input

Exactly two members, exactly one of which must be non-null:

```
ObjectTypeIdentifier { rid, id }
```

Proof:
```
{rid: null} -> "OneOf type field 'ObjectTypeIdentifier.rid' must be non-null."
{id:  null} -> "OneOf type field 'ObjectTypeIdentifier.id' must be non-null."
{apiName: null} -> "contains a field not in 'ObjectTypeIdentifier': 'apiName'"
```
Also rejected: `objectTypeId`, `ontologyRid`, `branch`, `versionId`, `name`.

---

## 9. Verified operations

Every operation below was executed live. Responses are verbatim (only truncated where
noted with `...`); nothing is redacted except tokens.

### 9.1 `GetObjectTypeDependents` — the impact-analysis query

**This is the primary find.** It returns every resource that depends on an object type
— Workshop modules and link types — with full RIDs, names, and Compass paths.

```graphql
query GetObjectTypeDependents($rid: RID!) {
  objectTypeV2(identifier: {rid: $rid}) {
    rid
    id
    latest { apiName displayName __typename }
    dependents {
      values {
        rid
        name
        path
        type { name description __typename }
        parent { rid name path __typename }
        projectRid
        __typename
      }
      nextPageToken
      __typename
    }
    __typename
  }
}
```

Variables:
```json
{"rid": "ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28"}
```

Real response (11 dependents; first 2 shown verbatim):

```json
{
  "data": {
    "objectTypeV2": {
      "rid": "ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28",
      "id": "knbsr0em.flights",
      "latest": {
        "apiName": "ExampleFlight",
        "displayName": "[Example] Flight",
        "__typename": "ObjectTypeVersion"
      },
      "dependents": {
        "values": [
          {
            "rid": "ri.workshop.main.module.d4cea2f9-d5c7-48ff-be51-0478e0dde2da",
            "name": "[Example] Filters and Complex Charts | Route Performance",
            "path": "/ZaP-dd58fb/AIP Now Ontology/Apps and Object Views/Object Views/[Example] Filters and Complex Charts | Route Performance",
            "type": { "name": "Module", "description": "Open module", "__typename": "ResourceTypeMetadata" },
            "parent": {
              "rid": "ri.compass.main.folder.d60b1796-ac43-4c56-b081-dd78162f70b5",
              "name": "Object Views",
              "path": "/ZaP-dd58fb/AIP Now Ontology/Apps and Object Views/Object Views",
              "__typename": "ResourceMetadata"
            },
            "projectRid": "ri.compass.main.folder.551e0791-eb00-48fb-a698-307adbb594d5",
            "__typename": "ResourceMetadata"
          },
          {
            "rid": "ri.ontology.main.relation.1348b10b-2569-40ae-bad2-cb7d69c9f7b0",
            "name": "ri.ontology.main.relation.1348b10b-2569-40ae-bad2-cb7d69c9f7b0",
            "path": "/ZaP-dd58fb/ri.ontology.main.ontology.d7f220ad-631f-459f-af20-963abc8e55ad/ri.ontology.main.relation.1348b10b-2569-40ae-bad2-cb7d69c9f7b0",
            "type": { "name": "Link type", "description": "", "__typename": "ResourceTypeMetadata" },
            "parent": {
              "rid": "ri.compass.main.folder.c45999f8-0659-41d5-acbb-377bffa450a2",
              "name": "ri.ontology.main.ontology.d7f220ad-631f-459f-af20-963abc8e55ad",
              "path": "/ZaP-dd58fb/ri.ontology.main.ontology.d7f220ad-631f-459f-af20-963abc8e55ad",
              "__typename": "ResourceMetadata"
            },
            "projectRid": "ri.compass.main.folder.c45999f8-0659-41d5-acbb-377bffa450a2",
            "__typename": "ResourceMetadata"
          }
        ],
        "nextPageToken": null,
        "__typename": "OntologyDependentPage"
      },
      "__typename": "ObjectType"
    }
  },
  "extensions": { "requestIndex": 0 }
}
```

Breakdown for this object type: `{"Module": 5, "Link type": 6}` — 11 total,
`nextPageToken: null`.

**`OntologyDependentPage`** has exactly: `values: [ResourceMetadata!]!`,
`nextPageToken`, `__typename`. Pagination arguments on `dependents` were **not**
found — see [NOT WORKING / UNKNOWN](#not-working--unknown).

### 9.2 `GetObjectTypeCore`

```graphql
query GetObjectTypeCore($rid: RID!) {
  objectTypeV2(identifier: {rid: $rid}) {
    rid id
    metadata { rid name path alias status visibility projectRid description __typename }
    ontology { rid apiName displayName description __typename }
    permissions { canView canEdit __typename }
    __typename
  }
}
```
Variables: `{"rid": "ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28"}`

```json
{"objectTypeV2":{
  "rid":"ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28",
  "id":"knbsr0em.flights",
  "metadata":{"rid":"ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28",
    "name":"ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28",
    "path":"/ZaP-dd58fb/ri.ontology.main.ontology.d7f220ad-631f-459f-af20-963abc8e55ad/ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28",
    "alias":"","status":null,"visibility":"NORMAL",
    "projectRid":"ri.compass.main.folder.c45999f8-0659-41d5-acbb-377bffa450a2",
    "description":"","__typename":"ResourceMetadata"},
  "ontology":{"rid":"ri.ontology.main.ontology.d7f220ad-631f-459f-af20-963abc8e55ad",
    "apiName":"ontology-a1733564-77e1-4054-8de7-4dcc87818ad9",
    "displayName":"ZaP Ontology","description":" Ontology","__typename":"Ontology"},
  "permissions":{"canView":true,"canEdit":true,"__typename":"ObjectTypePermissions"},
  "__typename":"ObjectType"}}
```

`ObjectTypePermissions` has exactly `canView`, `canEdit`, `_id`, `__typename`.

### 9.3 `ResourceMetadataByRid` — resolve any RID to metadata

Works for **any** Foundry RID, not just ontology entities.

```graphql
query ResourceMetadataByRid($rid: RID!) {
  resourceMetadata(rid: $rid) {
    rid name path alias status visibility projectRid description
    type { name description __typename }
    created { time __typename }
    modified { time __typename }
    parent { rid name path __typename }
    project { rid type __typename }
    __typename
  }
}
```

```json
{"resourceMetadata":{
  "rid":"ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28",
  "name":"ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28",
  "path":"/ZaP-dd58fb/ri.ontology.main.ontology.d7f220ad-631f-459f-af20-963abc8e55ad/ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28",
  "alias":"","status":null,"visibility":"NORMAL",
  "projectRid":"ri.compass.main.folder.c45999f8-0659-41d5-acbb-377bffa450a2","description":"",
  "type":{"name":"Object type","description":"","__typename":"ResourceTypeMetadata"},
  "created":{"time":"2024-12-20T01:01:22.978726632Z","__typename":"Attribution"},
  "modified":{"time":"2024-12-20T01:01:22.978726632Z","__typename":"Attribution"},
  "parent":{"rid":"ri.compass.main.folder.c45999f8-0659-41d5-acbb-377bffa450a2",
            "name":"ri.ontology.main.ontology.d7f220ad-631f-459f-af20-963abc8e55ad",
            "path":"/ZaP-dd58fb/ri.ontology.main.ontology.d7f220ad-631f-459f-af20-963abc8e55ad",
            "__typename":"ResourceMetadata"},
  "project":{"rid":"ri.compass.main.folder.c45999f8-0659-41d5-acbb-377bffa450a2",
             "type":"SERVICE_PROJECT","__typename":"Project"},
  "__typename":"ResourceMetadata"}}
```

`Project` exposes only `rid`, `type`, `metadata`, `_id`, `__typename`.

**`Attribution` carries the acting user.** Fields: `time`, `user: User`, `__typename`
(probes for `by`, `actor`, `principal`, `userId`, `username`, `createdBy`, `account`,
`identity`, `who`, `agent`, `timestamp` all returned `FieldUndefined`). Verified live:

```graphql
query GetAttribution($rid: RID!) {
  resourceMetadata(rid: $rid) {
    rid name
    created  { time user { id username firstName lastName fullName __typename } __typename }
    modified { time user { id username fullName __typename } __typename }
    permissions { canOpenLink canShare __typename }
    __typename
  }
}
```
```json
{"resourceMetadata":{
  "rid":"ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28",
  "name":"ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28",
  "created":{"time":"2024-12-20T01:01:22.978726632Z",
    "user":{"id":"27476c77-bb07-406a-9765-f7158cc70923","username":"ontology-metadata",
            "firstName":"ontology-metadata","lastName":null,"fullName":"ontology-metadata",
            "__typename":"User"},"__typename":"Attribution"},
  "modified":{"time":"2024-12-20T01:01:22.978726632Z",
    "user":{"id":"27476c77-bb07-406a-9765-f7158cc70923","username":"ontology-metadata",
            "fullName":"ontology-metadata","__typename":"User"},"__typename":"Attribution"},
  "permissions":{"canOpenLink":true,"canShare":true,"__typename":"ResourceMetadataPermissions"},
  "__typename":"ResourceMetadata"}}
```

**`ResourceMetadataPermissions`** has exactly `canOpenLink`, `canShare`, `_id`,
`__typename`. It does **not** have `canView`/`canEdit`/`canDelete`/`canRename`/
`canMove`/`canChangeMarkings` (all `FieldUndefined`) — those live on the entity-specific
permission types such as `ObjectTypePermissions`.

### 9.4 `ListObjectTypes` — enumerate object types with display names

```graphql
query ListObjectTypes($pageSize: Int!) {
  objectTypesV2(pageSize: $pageSize) {
    values {
      apiName displayName pluralDisplayName visibility
      objectType { rid id __typename }
      status { __typename }
      __typename
    }
    nextPageToken
    __typename
  }
}
```
Variables: `{"pageSize": 3}`

```json
{"objectTypesV2":{"values":[
 {"apiName":"Program","displayName":"Program","pluralDisplayName":"Programs","visibility":"NORMAL",
  "objectType":{"rid":"ri.ontology.main.object-type.018c03f7-9bdb-44e5-91e1-73bfcbe19f98","id":"kid3ntuq.program","__typename":"ObjectType"},
  "status":{"__typename":"ObjectTypeStatus_Experimental"},"__typename":"ObjectTypeVersion"},
 {"apiName":"HistoricalDataSpy","displayName":"Historical Data Spy","pluralDisplayName":"Historical Data Spys","visibility":"NORMAL",
  "objectType":{"rid":"ri.ontology.main.object-type.021ff592-bfcc-4fdb-be63-c53428f77af0","id":"knbsr0em.historical-data-spy","__typename":"ObjectType"},
  "status":{"__typename":"ObjectTypeStatus_Experimental"},"__typename":"ObjectTypeVersion"},
 {"apiName":"ExampleBg31documentProcess","displayName":"[Example bg31] Document Process","pluralDisplayName":"[Example bg31] Document Processes","visibility":"NORMAL",
  "objectType":{"rid":"ri.ontology.main.object-type.03193f2d-ec9d-4ac4-acd1-60d7fec0fdda","id":"knbsr0em.document-process-qsjou","__typename":"ObjectType"},
  "status":{"__typename":"ObjectTypeStatus_Example"},"__typename":"ObjectTypeVersion"}],
 "nextPageToken":"v2.eyJmb3VuZHJ5U2VhcmNoUGFnZVRva2VuIjoidjMuZXlKelpXRnlZMmhCWm5SbGNpSTZX...",
 "__typename":"ObjectTypeVersionPage"}}
```

`ObjectTypeStatus` is a union — observed members `ObjectTypeStatus_Experimental`,
`ObjectTypeStatus_Example`.

**Important:** `objectTypesV2.values` are `ObjectTypeVersion`, **not** `ObjectType`.
`rid`/`id`/`metadata` are NOT on `ObjectTypeVersion` — reach them via
`objectType { rid id }`. Verified error:
```
Field 'rid' in type 'ObjectTypeVersion' is undefined
```

### 9.5 `ListFunctions`

```graphql
query ListFunctions($pageSize: Int!) {
  functions(pageSize: $pageSize) {
    values {
      rid apiName displayName description visibility
      metadata { rid name path __typename }
      __typename
    }
    nextPageToken
    __typename
  }
}
```

```json
{"functions":{"values":[
 {"rid":"ri.function-registry.main.function.90910083-9ba0-4ef5-a1f0-eff2cfbe9aa7",
  "apiName":null,"displayName":"get_next_treatment_plan_id","description":"","visibility":"VISIBLE",
  "metadata":{"rid":"ri.function-registry.main.function.90910083-9ba0-4ef5-a1f0-eff2cfbe9aa7",
    "name":"ri.function-registry.main.function.90910083-9ba0-4ef5-a1f0-eff2cfbe9aa7",
    "path":"/Raava-fb7876/Medical AI/4. Functions/Action Backing Functions/ri.function-registry.main.function.90910083-9ba0-4ef5-a1f0-eff2cfbe9aa7",
    "__typename":"ResourceMetadata"},"__typename":"Function"},
 {"rid":"ri.function-registry.main.function.8f29afc6-b683-41e0-8387-4de1de56104e",
  "apiName":"getLLMAnswerFromDocumentPages","displayName":"Get LLM Answer From Document Pages",
  "description":"","visibility":"VISIBLE",
  "metadata":{"rid":"ri.function-registry.main.function.8f29afc6-b683-41e0-8387-4de1de56104e",
    "name":"ri.function-registry.main.function.8f29afc6-b683-41e0-8387-4de1de56104e",
    "path":"/ZaP-dd58fb/PharmaAI/2. Pipelines/datasets/Semantic Search Building Block (2025-01-20 15:00:51)/Logic/Get LLM Answer From Document Pages/ri.function-registry.main.function.8f29afc6-b683-41e0-8387-4de1de56104e",
    "__typename":"ResourceMetadata"},"__typename":"Function"}, ...],
 "nextPageToken":"...","__typename":"FunctionSearchPage"}}
```

Unlike the `*Version` page types, `functions.values` are `Function` objects that
**do** carry `rid` directly.

### 9.6 `ListLinkTypes` / `ListActionTypes`

```graphql
query ListLinkTypes($pageSize: Int!) {
  linkTypes(pageSize: $pageSize) { values { __typename } nextPageToken __typename }
}
```
```json
{"linkTypes":{"values":[{"__typename":"LinkTypeVersion"},{"__typename":"LinkTypeVersion"},{"__typename":"LinkTypeVersion"}],
 "nextPageToken":"v0.eyJmb3VuZHJ5U2VhcmNoUGFnZVRva2VuIjoidjMuZXlKelpXRnlZMmhCWm5SbGNpSTZXekl1TUN3aWNta3ViMjUwYjJ4dloza3ViV0ZwYmk1eVpXeGhkR2x2Ymk0d1pqQXpPVFU0TWkwMU0ySTBMVFEyT1RrdE9EaG1PQzB4TnprM016Um1aamcxTmpBaVhYMD0ifQ==",
 "__typename":"LinkTypeVersionPage"}}
```

```graphql
query ListActionTypes($pageSize: Int!) {
  actionTypes(pageSize: $pageSize) { values { __typename } nextPageToken __typename }
}
```
```json
{"actionTypes":{"values":[{"__typename":"ActionTypeVersion"},{"__typename":"ActionTypeVersion"},{"__typename":"ActionTypeVersion"}],
 "nextPageToken":"v0.eyJmb3VuZHJ5U2VhcmNoUGFnZVRva2VuIjoidjMuZXlKelpXRnlZMmhCWm5SbGNpSTZXekl1TUN3aWNna1lXTjBhVzl1Y3k1dFlXbHVMbUZqZEdsdmJpMTBlWEJsTGpFeE9ERTNaalpoTFdNME5tSXROREZtTXkxaE5UVTRMVGc1WTJNMllUUXlZVFkzWWlKZGZRPT0ifQ==",
 "__typename":"ActionTypeVersionPage"}}
```

### 9.7 `GetOntology`

```graphql
query GetOntology($rid: RID!) {
  ontology(rid: $rid) {
    rid apiName displayName description
    metadata { rid name path __typename }
    __typename
  }
}
```
Variables: `{"rid": "ri.ontology.main.ontology.1a944941-d587-4363-8314-d6274b7b0381"}`

```json
{"ontology":{
  "rid":"ri.ontology.main.ontology.1a944941-d587-4363-8314-d6274b7b0381",
  "apiName":"ontology-a75cc311-593f-4913-bb24-f4a2d923a7f9",
  "displayName":"Raava Ontology","description":" Ontology",
  "metadata":{"rid":"ri.ontology.main.ontology.1a944941-d587-4363-8314-d6274b7b0381",
    "name":"ri.ontology.main.ontology.1a944941-d587-4363-8314-d6274b7b0381",
    "path":"/Raava-fb7876/ri.ontology.main.ontology.1a944941-d587-4363-8314-d6274b7b0381/ri.ontology.main.ontology.1a944941-d587-4363-8314-d6274b7b0381",
    "__typename":"ResourceMetadata"},
  "__typename":"Ontology"}}
```

### 9.8 `SearchTitles` — fast title search across all resource types

`search` returns `[SearchTitlesResult!]!`, an abstract type. Verified via
`InvalidFragmentType` that `ResourceMetadata` **is** a member and `ObjectTypeVersion`
is **not**.

```graphql
query SearchTitles($t: String!, $l: Int!) {
  search(title: $t, limit: $l) {
    __typename
    ... on ResourceMetadata { rid name path type { name __typename } }
  }
}
```
Variables: `{"t": "Flight", "l": 4}`

```json
{"search":[
 {"__typename":"ResourceMetadata","rid":"ri.compass.main.folder.53a3d3a6-6047-44eb-aa1c-2e8db9f65e8d",
  "name":"Flight Sensor","path":"/ZaP-dd58fb/AIP Now Ontology/Data/Sources/Flight Sensor",
  "type":{"name":"Folder","__typename":"ResourceTypeMetadata"}},
 {"__typename":"ResourceMetadata","rid":"ri.foundry.main.dataset.39848259-b83a-49e7-92d2-15981c90e0a1",
  "name":"Flights","path":"/ZaP-dd58fb/AIP Now Ontology/Data/Ontology/Objects/Flights",
  "type":{"name":"Dataset","__typename":"ResourceTypeMetadata"}},
 {"__typename":"ResourceMetadata","rid":"ri.foundry.main.dataset.0992fd70-1a98-4541-86ac-d1da43712b38",
  "name":"[Example] BTS Flights","path":"/ZaP-dd58fb/AIP Now Ontology/Data/Sources/BTS/[Example] BTS Flights",
  "type":{"name":"Raw dataset","__typename":"ResourceTypeMetadata"}},
 {"__typename":"ResourceMetadata","rid":"ri.workshop.main.module.1c81a259-7913-4a18-bede-d67c4da3e39e",
  "name":"[Example] Linked Filters with Map and Charts | Flights Map",
  "path":"/ZaP-dd58fb/AIP Now Ontology/Apps and Object Views/Object Views/[Example] Linked Filters with Map and Charts | Flights Map",
  "type":{"name":"Module","__typename":"ResourceTypeMetadata"}}]}
```

### 9.9 `ResourceSearchPanelQuery` — full resource search with highlights

Query text taken verbatim from the hubble bundle, extended with RID fields.

```graphql
query ResourceSearchPanelQuery($filter: ResourceSearchFilter!, $sort: ResourceSearchSort) {
  searchResources(pageSize: 3, filter: $filter, sort: $sort) {
    nextPageToken
    results {
      highlights { field matches }
      resource { rid name path type { name __typename } __typename }
      __typename
    }
    __typename
  }
}
```
Variables:
```json
{"filter": {"pathStartsWith": ["/ZaP-dd58fb"]},
 "sort": {"field": "LAST_MODIFIED", "direction": "DESCENDING"}}
```

```json
{"searchResources":{
 "nextPageToken":"v1.eyJzZWFyY2hBZnRlciI6WzE3NjI5NTU2Njc2ODcsInJpLmVkZGllLm1haW4ucGlwZWxpbmUuN2E1NTgxNTItZjJkMy00ZDgxLWEyY2QtMzc0MWRkNjRiMDU3Il19",
 "results":[
  {"highlights":[{"field":"path","matches":["<b>&#x2F;ZaP-dd58fb</b>&#x2F;AIP Now Ontology&#x2F;Data"]}],
   "resource":{"rid":"ri.compass.main.folder.519d3cc1-ecaf-4254-ad45-3bae1e69428f","name":"Data",
     "path":"/ZaP-dd58fb/AIP Now Ontology/Data",
     "type":{"name":"Folder","__typename":"ResourceTypeMetadata"},"__typename":"ResourceMetadata"},
   "__typename":"ResourceSearchResult"},
  {"highlights":[{"field":"path","matches":["<b>&#x2F;ZaP-dd58fb</b>&#x2F;AIP Now Ontology&#x2F;Data&#x2F;[Example] GeoJSON Extraction Pipeline"]}],
   "resource":{"rid":"ri.eddie.main.pipeline.1ccea1df-429c-44a8-810e-0ff9a6a299f6",
     "name":"[Example] GeoJSON Extraction Pipeline",
     "path":"/ZaP-dd58fb/AIP Now Ontology/Data/[Example] GeoJSON Extraction Pipeline",
     "type":{"name":"Pipeline Builder","__typename":"ResourceTypeMetadata"},"__typename":"ResourceMetadata"},
   "__typename":"ResourceSearchResult"}, ...],
 "__typename":"ResourceSearchResultsPage"}}
```

Notes:
- `highlights[].matches` contains **HTML-escaped markup** with `<b>` tags. Strip/unescape before display.
- `ResourceSearchFilter` known members: `pathStartsWith: [String!]` (verified working).
  Others UNVERIFIED — see NOT WORKING.
- `ResourceSearchSort`: `{field: LAST_MODIFIED, direction: DESCENDING}` verified working.
  Full enum sets UNVERIFIED.

### 9.10 Batched multi-request example

Demonstrates batching, index correlation, and out-of-order delivery. Full request body:

```json
{"operations": {
   "a": "query OpA($rid: RID!) { objectTypeV2(identifier: {rid: $rid}) { rid id __typename } }",
   "b": "query OpB($rid: RID!) { resourceMetadata(rid: $rid) { rid name __typename } }"},
 "requests": [
   {"hash":"a","name":"OpA","variables":{"rid":"ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28"}},
   {"hash":"b","name":"OpB","variables":{"rid":"ri.ontology.main.object-type.1ca5ae2e-c17a-4afd-aa19-b636c9494b28"}},
   {"hash":"a","name":"OpA","variables":{"rid":"ri.ontology.main.object-type.018c03f7-9bdb-44e5-91e1-73bfcbe19f98"}},
   {"hash":"a","name":"OpA","variables":{"rid":"ri.ontology.main.object-type.00000000-0000-0000-0000-000000000000"}}]}
```

Response body is quoted verbatim in [§6](#6-sse-response-format).

---

## 10. Type reference

Recovered via oracles. `!` and `[]` are as reported by the server.

```
Query.me                       -> User!
Query.objectTypeV2(identifier: ObjectTypeIdentifier!) -> ObjectType
Query.dependents               (does NOT exist at root)

ObjectType          { rid, id, favorited, latest: ObjectTypeVersion,
                      metadata: ResourceMetadata, ontology: Ontology,
                      branch: ObjectTypeBranch, dependents: OntologyDependentPage!,
                      permissions: ObjectTypePermissions!, _id, __typename }

OntologyDependentPage { values: [ResourceMetadata!]!, nextPageToken, __typename }

ObjectTypeVersion   { apiName, displayName, description, pluralDisplayName, visibility,
                      objectType: ObjectType!, properties: [ObjectTypeProperty!]!,
                      status: ObjectTypeStatus!, titleProperty: ObjectTypeProperty!,
                      _id, __typename }

ObjectTypeProperty  { rid, id, displayName, description, apiName, visibility,
                      objectType: ObjectTypeVersion!, status: ObjectTypePropertyStatus!,
                      type: ObjectTypePropertyType!, _id, __typename }

ObjectTypePermissions { canView, canEdit, _id, __typename }
ObjectTypeBranch      { ontology: Ontology, _id, __typename }

LinkType            { rid, id, metadata: ResourceMetadata, ontology: Ontology,
                      permissions: LinkTypePermissions!, _id, __typename }
LinkTypeVersion     { description, linkType: LinkType!, status: LinkTypeStatus!, _id, __typename }
ActionTypeVersion   { apiName, displayName, description, actionType: ActionType!,
                      logic: ActionTypeLogic!, parameters: [ActionParameter!]!,
                      status: ActionTypeStatus!, _id, __typename }

Function            { rid, apiName, displayName, description, visibility,
                      metadata: ResourceMetadata, version: FunctionVersion, _id, __typename }
Schedule            { rid, name, description, version: ScheduleVersion, _id, __typename }

Ontology            { rid, apiName, displayName, description,
                      metadata: ResourceMetadata, namespace: Namespace,
                      branch: OntologyBranch, defaultBranch: OntologyBranch!,
                      linkTypes(pageSize: Int!): LinkTypeVersionPage!,
                      actionTypes(pageSize: Int!): ActionTypeVersionPage!,
                      functions(pageSize: Int!): FunctionSearchPage,
                      interfaces(pageSize: Int!): OntologyInterfaceVersionPage!,
                      permissions: OntologyPermissions!,
                      roleGrants: [OntologyRoleGrant!]!, _id, __typename }

ResourceMetadata    (see §5 for the complete field table)
Resource            { rid, metadata: ResourceMetadata, _id, __typename }
Project             { rid, type, metadata: ResourceMetadata, _id, __typename }
ResourceTypeMetadata{ name, description, __typename }     # also: iconName (from bundles, UNVERIFIED)
Attribution         { time, user: User, __typename }
User                { id, username, firstName, lastName, fullName, _id, __typename }
ResourceMetadataPermissions { canOpenLink, canShare, _id, __typename }
BranchMetadata      { rid, name, branch: Branch, resource: ResourceMetadata!, _id, __typename }
Namespace           { rid, metadata: ResourceMetadata, permissions: NamespacePermissions!, _id, __typename }
Tag                 { rid, name, created: Attribution, modified: Attribution,
                      permissions: TagPermissions!, _id, __typename }
```

### Type names confirmed to EXIST in the schema

```
WorkshopModule  Notepad  Folder  Project  Resource  ResourceMetadata
ObjectType  ObjectTypeVersion  LinkType  LinkTypeVersion  ActionType  ActionTypeVersion
Function  FunctionVersion  Ontology  OntologyBranch  Schedule  BuildReport  JobReport
Tag  Namespace  Attribution  Marking  MarkingInfo  User  Principal  Group
OntologyDependentPage  ResourceMetadataPage
ObjectTypeFilter  LinkTypeFilter  ActionTypeFilter  FunctionSearchFilter
ResourceSearchFilter  JobFilterInput  ScheduleSearchFilter  ObjectTypeIdentifier
ResourceSearchSort  Mutation  Subscription
```

### Type names confirmed NOT to exist

```
Workbook  Dataset  Datasource  Space  Build  Job  Interface  InterfaceType
SharedPropertyType  TypeGroup  ObjectView  Quiver  Slate  Fusion  Contour
Code  Repository  Pipeline  Transform  Lineage  Impact  Dependency  Usage
```

Datasets and pipelines exist as *resources* (`ri.foundry.main.dataset.*`,
`ri.eddie.main.pipeline.*`) surfaced through `ResourceMetadata.type.name`, but have no
dedicated GraphQL types on this gateway.

### Additional operations found in hubble bundles

Extracted from the 12.9 MB of frontend JS (`/assets/content-addressable-storage/frontend/<sha256>.js`,
fetched with the same bearer token). 56 operations and 196 fragments were recovered.
Query texts below are verbatim from the bundle but **NOT executed** — treat as
**UNVERIFIED** except where re-verified above.

```graphql
query ResourceChildrenPanelQuery($rid: RID!) {
  resourceMetadata(rid: $rid) { children { values { rid _id } } _id }
}

query ObjectTypeIdFromRidQuery($rid: RID!) { objectType(rid: $rid) { id _id } }
query ObjectTypeRidFromIdQuery($id: String!) { objectTypeById(id: $id) { rid _id } }

query SearchObjectTypesByTypeGroupRid($objectTypeGroupRids: [RID!]!, $pageToken: String) {
  objectTypesV2(
    filter: {clauses: {objectTypeGroupRids: $objectTypeGroupRids}, type: AND}
    pageSize: 500
    pageToken: $pageToken
  ) { values { objectType { rid _id } _id } nextPageToken }
}

query GetLinkType($linkTypeId: String!) {
  linkTypeById(id: $linkTypeId) {
    latest { definition {
      ... on LinkTypeDefinition_OneToMany  { oneSide { ...S _id } manySide { ...S _id } }
      ... on LinkTypeDefinition_ManyToMany { aSide  { ...S _id } bSide   { ...S _id } }
      ... on LinkTypeDefinition_Intermediary { aSide { ...S _id } bSide { ...S _id } }
    } _id } _id }
}
fragment S on LinkTypeSide { side objectTypeRid _id }

query OutputListQuery($buildRid: RID!, $pageToken: String!, $pageSize: Int!) {
  build(buildRid: $buildRid) {
    outputs(pageToken: $pageToken, pageSize: $pageSize) {
      values { rid ...JobOutputFragment } totalNumberOfResults }
    jobs { ...BuildOutputsJobReportFragment } _id }
}

query OntologyBranchRidForFoundryBranchRid($foundryBranchRid: RID!) {
  globalBranch(rid: $foundryBranchRid) @optional { ontologyBranchV2 { rid _id } _id }
}

query ObjectViewTabQueryGivenRid($branchIdentifier: GlobalBranchIdentifierInput!, $objectTypeRid: RID!) {
  defaultOrGlobalBranch(identifier: $branchIdentifier) {
    _id
    objectType(identifier: {rid: $objectTypeRid}) { ...BranchedObjectViewEditorTabs _id }
  }
}
```

Notable: the bundles use a custom **`@optional` directive** on fields that may fail
(e.g. `searchResources(...) @optional`, `globalBranch(rid:) @optional`). Its exact
semantics are **UNVERIFIED** — presumably "null on error instead of propagating".

`ObjectTypeFilter` supports a clause form: `{clauses: {objectTypeGroupRids: [...]}, type: AND}`.

Full extracted corpus (not shipped): `extracted_ops.txt`, `extracted_fragments.txt`.

---

## 11. Implementation notes for a `pltr-cli` fork

1. **Always select `rid`.** Never plumb `_id` into user-facing output — it is a client
   cache key and carries no Foundry meaning.
2. **Demux on `extensions.requestIndex`.** Events are out of order. Build a result
   array of length `len(requests)` and fill by index.
3. **Two error planes.** Non-2xx → Conjure JSON; 200 → per-event GraphQL `errors`.
   Surface `errorInstanceId` in bug reports; it is the Foundry support correlation ID.
4. **Batch aggressively.** One HTTP call can carry many executions. For "dependents of
   N object types", send one document and N `requests` entries.
5. **`?q=` is cosmetic** but harmless. Setting it to the comma-joined operation names
   matches hubble's behavior and is the lowest-risk choice.
6. **`*Version` page types don't carry `rid`.** For `objectTypesV2` / `linkTypes` /
   `actionTypes`, descend into `objectType { rid }` / `linkType { rid }` /
   `actionType { rid }`.
7. **Human labels:** `ObjectType.latest.displayName`, not `metadata.name` — the latter
   is frequently the RID string for ontology-internal resources.
8. **Validation errors are a feature.** The oracles in §4 remain the only way to
   explore this schema; keep a probe subcommand around for future stack versions.
9. **Pin the `fetch-user-agent`.** It costs nothing and matches a real client.

---

## NOT WORKING / UNKNOWN

### Confirmed not working

| Item | Evidence |
|---|---|
| **Full schema introspection** | Returns a 20-type stub; `objectTypeV2` resolves in the same response where `__schema` omits it. `__type(name:"ObjectType")` → `null`. Not routing-dependent. |
| **`__type` on any non-stub type** | `ObjectType`, `ResourceMetadata`, `WorkshopModule`, `LinkType`, `Project`, `Folder` all → `{"__type": null}`. |
| **Root-level `lineage` / `impact` / `dependencies` / `usages`** | All → `FieldUndefined` on `Query`. Dependency data is reachable **only** via `ObjectType.dependents`. |
| **`ResourceMetadata.dependents`** | `FieldUndefined`. Dependents hang off `ObjectType`, not off arbitrary resources. |
| **`ObjectType.latest` on `ResourceMetadata`** | `FieldUndefined` — `latest` is on `ObjectType` only. |
| **`Query.dataset` / `datasets` / `project` / `projects` / `space` / `folders`** | All `FieldUndefined`. Use `resourceMetadata(rid:)` and `searchResources`. |
| **Lineage/dependency UI code in the fetched bundles** | 0 hits for `dependents`/`lineage`/`impact` as GraphQL. The 14 fetched bundles are the Compass workspace shell only. |

### Unknown / unverified

- **Pagination of `dependents`.** `OntologyDependentPage` has `nextPageToken`, but no
  `pageSize`/`pageToken` argument was found on the `dependents` field. The test object
  type returned 11 dependents with `nextPageToken: null`, so paging was never exercised.
  **Behavior above the page limit is UNKNOWN.**
- **`Attribution.user` is resolved** (`{time, user: User}`) — see §9.3. What remains
  unknown is whether service principals vs humans are distinguishable beyond
  `username` (the test resource was attributed to the service account
  `ontology-metadata`).
- **`ResourceMetadataPermissions`** is resolved (`canOpenLink`, `canShare`) but the set
  is suspiciously small; other permission bits may exist under names not probed.
- **Input object shapes.** Only `ObjectTypeIdentifier` was fully resolved (`@oneOf`
  `{rid, id}`). `ResourceSearchFilter` is confirmed to accept `pathStartsWith`, and
  `ObjectTypeFilter` a `{clauses, type}` form, but complete member lists for
  `ResourceSearchFilter`, `ObjectTypeFilter`, `LinkTypeFilter`, `ActionTypeFilter`,
  `FunctionSearchFilter`, `JobFilterInput`, `ScheduleSearchFilter`, `ResourceSearchSort`
  are **UNKNOWN**. The bulk `{field: null}` probe is defeated because the server emits
  one whole-argument `WrongType` error rather than per-field errors; single-field probes
  work but were not run exhaustively.
- **Enum value sets.** `ResourceSearchSort.field` accepts `LAST_MODIFIED` and
  `direction` accepts `DESCENDING`; the complete enums are unknown. Same for
  `visibility` (`NORMAL`, `VISIBLE` observed), `Project.type` (`SERVICE_PROJECT`
  observed), and the `ObjectTypeStatus` union (`_Experimental`, `_Example` observed).
- **`SearchTitlesResult` membership.** `ResourceMetadata` is a member;
  `ObjectTypeVersion` is not. The complete member list is unknown.
- **`@optional` directive semantics.** Used throughout the bundles; behavior not tested.
- **Header ablation.** Whether `fetch-user-agent` and `Accept: text/event-stream` are
  individually required was not tested.
- **Permission-denied vs not-found.** Both may present as `null` with no error; not
  distinguished. Only a non-existent RID was tested (→ `null`, no error).
- **The 4 unfetched bundles.** 14 of 18 known bundle hashes were retrieved. The
  dependency-graph UI ships elsewhere and was never located.

### Deliberately not exercised (read-only constraint)

A **`Mutation` root type exists** on this schema, despite introspection reporting
`mutationType: null`. Confirmed with a side-effect-free meta-field only:

```
{"operations":{"a":"mutation OpA { __typename }"}, ...}
-> {"data":{"__typename":"Mutation"},"extensions":{"requestIndex":0}}
```

Bundle mining also revealed mutation names: `FavoriteMutation`, `UnfavoriteMutation`,
`RenameResourceMutation`, `ViewResourceMutation`, `AcknowledgeNotificationMutation`,
`RevokeNotificationMutation`, `MarkVersionedObjectSetAsViewed`. **No mutation was
executed and none should be, without an explicit write mandate.** A `Subscription` type
also exists (`ScheduleRunStatusBuildSubscription`, `SubscriptionHealthTest` in bundles);
untested.

---

## Appendix — audit trail

Every HTTP request made during this investigation is logged tab-separated
(timestamp, method, path, status) at:

```
/Users/master/.claude/jobs/b1d5cafa/tmp/endpoint_audit.log
```

Only two paths were used against the gateway itself:
`/graphql-gateway/api/bulk` and `/assets/content-addressable-storage/frontend/<sha256>.js`.
</content>
