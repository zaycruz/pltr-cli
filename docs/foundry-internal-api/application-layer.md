# Application / consumer layer — turning bare dependent RIDs into named, characterised consumers

**Stack:** `https://zap.usw-18.palantirfoundry.com`  **Date verified:** 2026-07-21
**Auth:** `CredentialStorage().get_profile('zap')` → `host`, `token`; `Authorization: Bearer <token>`.
**Base-path convention:** `https://<stack>/<service-name>/api/<route>` (service name = package minus `@palantir/` and `-api`).

> **Status legend.** Everything below is VERIFIED (executed live, output captured) unless tagged **UNVERIFIED** / **NOT MOUNTED**.

## BLUF — the two questions Zay asked

- **App OSDK version pinning: READABLE (fully).** `third-party-application-service` exposes, per application, the pinned OSDK: npm package name, generator version, `applicationVersion`, `ontologyVersion`, and — critically — a **per-object-type `{oldest, latest}` generated-SDK version range** (`bulkGetEntitySdkVersions`). This is the exact property-rename impact signal: an object type whose `oldest` generated SDK predates a rename means apps on old SDKs break differently than live ones.
- **Workshop variables: NOT READABLE here.** Neither `/workshop/api/*` nor `module-group-api` is mounted on this gateway (all `Route:RouteNotMounted`). Compass names a Workshop module and gives its breadcrumb path, but exposes **no ontology bindings and no variables** (`backedObjectTypes: []`). Workshop internals are not reachable read-only on this stack.

## The universal namer — Compass resolves EVERY consumer RID type

`GET /compass/api/resources/{rid}?decoration=path` → `200`. Works for **notepad, workshop module, marketplace block-set-installation, and third-party application** RIDs (and datasets, etc.). Returns `name`, `path` (breadcrumb), `directlyTrashed`/`inTrash` (is it live?), plus rich decorations (`backedObjectTypes`, `collections`, `tags`, `contactInformation`, `markings`, `status`). This is the single fallback for any bare dependent RID that has no dedicated read service.
- Verified: notepad `01034eb1…` → `"[2] Foundation Data Index"`; workshop `77e3b22f…` → `"Reference Foundation Landing Page"` path `/ZaP-dd58fb/AIP Now Ontology/…`; block-set `9db2bc19…` → `"AIP Now Ontology"`; app `c2b28336…` → `"Learning Management System Application"` (`directlyTrashed: true`).
- Batch `POST /compass/api/batch/resources` exists (`getResources`) — field `rids` recognised but body returns `422 Conjure:UnprocessableEntity` for `{rids, decorations}`; exact shape UNVERIFIED. Use the single GET per RID (reliable).
- No dedicated read service is mounted for marketplace / notepad / workshop / quiver / slate (`/marketplace/api`, `/*-service/api` → 404). Compass is the only namer for those. `slate` mount returns the SPA HTML (frontend, not API).

## `third-party-application-service` (48 ep) — the app reverse-index + OSDK impact source

Base: `/third-party-application-service/api`.

| Route | Method | Purpose / impact signal |
|---|---|---|
| `/applications` | GET | List all apps (this stack: **6**). Each entry = `{application, metadata}`. |
| `/applications/{rid}` | GET | Full app record. |
| `/applications/{rid}/versions` | GET | Deployed version history (`applicationVersion` ints + timestamps/user). |
| `/application-sdks/{rid}` | GET | `listSdks` — pinned OSDK(s) for the app. |
| `/application-sdks/{rid}/entity-sdk-versions` | PUT | **Per-object-type `{oldest,latest}` SDK range.** Body `{"entityRids":[<otRid>…]}`. |
| `/application-sdks/for-repository/{repoRid}` | GET | Reverse: SDK/artifacts repo RID → owning app RID. |
| `/application-sdks/{rid}/repository` | GET | App → its SDK artifacts repo RID. |

**RID → named, characterised consumer (apps).** `GET /applications/{rid}` returns:
- `name`, `description`, `organizationRid`, `directlyTrashed` (via Compass).
- `clientSpecification.type` = `public` (has redirect URLs / subdomains) or `confidential` (backend, no redirects). `public.authorizationCodeGrant.redirectUrls` gives the **deployed subdomains** (e.g. `https://lms.raava.dev/auth/callback`, `https://test-xkf44ylmgjyli6rn.apps.usw-18.palantirfoundry.com/…`).
- `clientAllowedOperationsV2.operations` = granted scopes (`ONTOLOGIES_READ/WRITE`, `MEDIASETS_*`).
- **`scopes.dataScope.{objectTypes,linkTypes,actionTypes,functions}` = THE reverse index.** These are the ontology entities the app is bound to. Invert `listApplications` over `dataScope.objectTypes` → **object-type RID → which apps depend on it, by name.**

**6 apps on this stack** (name · client · #objectTypes): `Student Management System`·public·16 · `Customer Onboarding Backend`·confidential·7 · `Customer Onboarding`·public·7 · `Medical Frontend`·public·9 · `ZapGroupSandbox`·public·3 · `Learning Management System Application`·public·14.

**OSDK pinning sample** (real, `Student Management System`):
```
listSdks → sdks[0] = {
  repositoryRid: ri.artifacts.main.repository.b5f2e4b6-…,
  version: "0.6.0",
  inputs: { ontologyVersion:"000…000", applicationVersion:5, applicationVersionV2:{version:5,branch:{type:"default"}} },
  npm: { npmPackageName:"@student-management-system/sdk", npmGeneratorVersion:"2.43.0",
         status:{type:"success",success:{timestamp:"2026-07-20T21:53:26Z"}} } }
```
`entity-sdk-versions` (real, same app) → `{"sdkVersions":{ "ri.ontology.main.object-type.16bd6f2a-…":{"oldest":"0.4.0","latest":"0.6.0"}, "ri.ontology.main.object-type.9ba1a1dd-…":{"oldest":"0.1.0","latest":"0.6.0"}, … }}`. **Impact read:** a property rename on `16bd6f2a` breaks any SMS client generated on 0.4.0–0.5.x; an OT with `latest < current` means the app hasn't regenerated and is on a stale contract. Apps with `listSdks: []` (e.g. LMS) pin no generated SDK → break live, not via stale bundle.

## `object-set-service` (7 ep) — MOUNTED, query-executor only

Base: `/object-set-service/api` (mounted — routes return `400 InvalidArgument`, not RouteNotMounted). Routes are object materialisers (`getTopObjectsInitialPage/…Next`, `getAllObjects…`, `loadObjects`, `getLinks`, `getBulkLinksPage`) — **not** a name-a-saved-set API. Request wants an `objectSet` graph keyed by an OSS-internal object-type id; RID-shaped `objectTypeId`/`objectTypeRid` bodies all `400` (needs the ontology-gateway int id — UNVERIFIED). Impact-gate takeaway: a saved object set that appears as a dependent is a **Compass resource → name it via the universal namer**; its stored query definition (which the rename actually breaks) is not exposed read-only here.

## `module-group-api` (34 ep) — NOT MOUNTED on this gateway

`deployed-apps/*` + `module-group-multiplexer/*` (compute-module / deployed-app runtime). Swept `module-group`, `module-group-api`, `module-groups`, `modulegroup`, `compute-module(s)`, `skylab`, `magritte`, `deployed-apps`, `functions`, `multiplexer` — all `Route:RouteNotMounted`. Not reachable read-only on `zap`. No module→ontology binding obtainable here.

## Workshop internals — NOT MOUNTED

`/workshop/api/*` (tried `/workshop`, `/workshop/api/modules/{rid}`, `/module/{rid}/current`, `/state/{rid}`, `/v2/workshop/{rid}`) → all `Route:RouteNotMounted`. **Workshop ontology bindings and variables are not readable on this stack.** Best available: Compass name + path for the module RID (above). If Workshop bindings are ever needed, they must come from the GraphQL supergraph or a non-gateway route — not found here.

## Marketplace block-sets, Notepad, Quiver, Slate — name via Compass only

No dedicated read service mounted. `GET /compass/api/resources/{rid}?decoration=path` names and locates them (block-set `9db2bc19…` = `"AIP Now Ontology"`, path `/ZaP-dd58fb/AIP Now Ontology/AIP Now Ontology`; full resource shape carries `status`, `branches`, `backedObjectTypes`, `collections`, `tags`). Installed-product version / manifest for a block-set is **not** exposed on this gateway (no marketplace API). Characterisation for these = name + path + trashed-state + tags/collections.

## GraphQL depth (point 7)

Consumer-relevant root fields `objectTypeById`, `collections`, `favorites`, `searchResources`/`ResourceSearchPanelQuery` are **already live-verified** in `graphql-gateway.md` (§8–9) — not duplicated here. For the impact gate, GraphQL `objectType(rid).dependents` remains the discovery mechanism; **this doc supplies the naming layer** the gateway does not: TPAS for apps + OSDK pinning, Compass for every other consumer RID.

## How the gate should wire this

1. `dependents` returns bare RIDs → branch on RID namespace.
2. `ri.third-party-applications.main.application.*` → TPAS `getApplication` (name, subdomains, scopes) + `listSdks` + `entity-sdk-versions` for the changed object type → **"breaks app X (on OSDK 0.4.0, subdomain lms.raava.dev)"**.
3. Any other consumer RID (`notepad`/`workshop`/`marketplace`/`quiver`/`slate`/object-set) → Compass `resources/{rid}?decoration=path` → name + breadcrumb + live/trashed. Workshop variables & object-set query defs are **not** recoverable read-only on this stack (documented gaps).
