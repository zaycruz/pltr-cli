# Foundry internal API surface

Reference documentation for **undocumented** Palantir Foundry internal APIs, written so
`pltr-cli` can implement full-lifecycle change-impact analysis.

Discovered and verified 2026-07-21 against `zap.usw-18.palantirfoundry.com`.

> **Stability warning.** Nothing in this directory is a public or supported Palantir API.
> Palantir staff stated publicly on 2026-06-23 that "Workflow Lineage doesn't expose public
> APIs for getting the lineage of resources." These endpoints can change or disappear without
> notice. Any code built on them must **pin, shape-check, and degrade loudly** — never silently.

---

## Why this exists

The public `foundry-platform-sdk` is **forward-declarative**: a resource can tell you what it
depends on, but nothing tells you what depends on *it*.

```
grep -rniE "(usages|used_by|dependents|referencedBy|consumers|reverse_)" foundry_sdk/
→ zero matches across the entire package
```

The only reverse index in the public SDK is `Dataset.get_schedules`, whose own docstring
admits up to one hour of staleness.

This led the repo's capability ledger to record `CAP-06`, `CAP-10`, and `CAP-16` — declaring
Workshop and function wiring permanently undiscoverable. **That conclusion is wrong.** It holds
only under a public-SDK-only constraint. The platform exposes all of it.

## Three distinct surfaces

| Surface | Transport | Use for |
|---|---|---|
| Public platform API (`/api/v2/…`) | REST, documented | Object types, links, actions, **datasources** |
| Conjure internal services (`/<service>/api/…`) | REST, undocumented | Lineage (monocle), reverse indexes, builds, repos |
| GraphQL gateway (`/graphql-gateway/api/bulk`) | GraphQL over SSE | Ontology Manager's own dependents query |

Base-path convention for Conjure services (verified from `@palantir/mcp`
`getApiPath`): `https://<stack>/<service-name-with-dashes>/api`.

## Documents

| File | Covers |
|---|---|
| `graphql-gateway.md` | `/graphql-gateway/api/bulk`, `GetObjectTypeDependents`, SSE format, introspection bypass |
| `lineage-monocle-build2.md` | Monocle link graph (target **V3**), build2 jobspecs, code→dataset lineage |
| `reverse-indexes.md` | ontology-metadata, function-registry, compass, stemma reverse lookups |
| `gates-and-mutations.md` | Native change-review mechanisms + the 303-endpoint mutation surface |
| `application-layer.md` | Naming consumers; third-party-app OSDK version pinning; universal Compass namer |
| `ingestion-and-health.md` | Connections/webhooks; per-dataset health checks as impact vocabulary |
| `platform-sweep.md` | Remaining services, other GraphQL surfaces, batching + pagination limits |

## Design-defining conclusions

Six parallel sweeps (2,000+ live endpoint calls) settled the questions that block the spec:

1. **Foundry has no enforced gate.** Global Branching, ontology proposals, and stemma PRs
   exist, but **none is mandatory and none intercepts a raw Conjure/GraphQL mutation** — an
   agent can `modifyOntology` or `commitTransaction` with no proposal. So the *enforcement*
   must be ours; the *impact analysis* should reuse Foundry's primitives (Merge Graph,
   `getModifiedEntities`, `dryRunModifyOntology`). Don't rebuild lineage.
2. **Intercept at the Conjure/CLI layer, not GraphQL.** The GraphQL `Mutation` root is a thin
   Compass surface (createProject/rename/move/trash). Every *semantic* mutation
   (`modifyOntology`, `commitTransaction`, markings) is internal Conjure REST. A GraphQL-layer
   gate misses ~everything. Classify mutations on **method-name tokens, not HTTP verb** (66
   read-PUTs + ~220 read-POSTs would false-positive).
3. **The full lifecycle is reachable** — code line → transform → dataset → property/column →
   object type → actions / functions / Workshop modules / applications, both directions.
   **Gap:** Workshop *variables* are not readable on this stack (`Route:RouteNotMounted`).

## The through-line: these APIs fail toward false safety

Every sweep found another instance of the same failure mode. A change-impact gate's central
job is to refuse it:

- `monocle --direction downstream` drops all `adjacent` links → **zero impacts, blast radius 0**
  for a change with 10 real dependents.
- `ontology-metadata` endpoints **silently ignore unknown request fields** → a typo returns
  `200` with empty results, reading as "nothing depends on this".
- `objectTypeV2.dependents` has **no page argument and truncates silently**, while the
  identical-looking `actionType.dependents` paginates → under-reports object-type impact.

Rule for the gate: **empty or truncated is `inconclusive`, never `safe`.** Carry a positive
control per endpoint. Treat object-type dependents beyond page 1 as inconclusive.

## Provenance

Endpoint paths were **not guessed**. They were read from Palantir's own MCP server, which
ships unminified with sourcemaps containing original TypeScript:

```
~/.npm/_npx/63a3fbe1b43eb419/node_modules/@palantir/mcp   (v0.397.0)
  dist/index.mjs        bundled clients
  dist/index.mjs.map    3568 sources + sourcesContent (original TS)
```

831 endpoints across 27 services were extracted to `/tmp/foundry_endpoints.tsv`.

Every claim in these documents is live-verified against the stack unless explicitly marked
`UNVERIFIED`. Endpoints touched during discovery are logged in the session audit trail.

## Corrections to the existing capability ledger

| Entry | Status | Correction |
|---|---|---|
| `CAP-14` — property→dataset-column unsupported | **STALE** | `GET /api/v2/ontologies/{ont}/objectTypes/{apiName}?includeDatasources=true` returns `propertyMapping`. This is a **public** API, added after SDK 1.95.0. |
| `CAP-10` — Workshop wiring undiscoverable | **WRONG** | Monocle and `GetObjectTypeDependents` both return `WorkshopModule` dependents. |
| `CAP-06`/`CAP-16` — function reverse-wiring undiscoverable | **WRONG** | `function-registry` exposes ontology bindings, versions, and locator types incl. `logic` (AIP Logic) and `aipAgent`. |

## Known-false parity claim

`src/pltr/capabilities.py` (parity worktree) maps the MCP tool `get_resource_graph` to
`filesystem.Resource.get + Folder.children + Project.Reference.list`. That is **folder
containment, not lineage**. The real tool calls `POST /monocle/api/links/graphV2`. The current
mapping returns a confident, wrong graph and must be corrected before anything gates on it.

## Implementation guidance for `pltr-cli`

1. **Pin the contract.** Record `@palantir/mcp` version alongside each internal endpoint spec.
2. **Shape-check every response.** On mismatch, emit an explicit coverage gap — never an empty
   result. An empty result is indistinguishable from "no impact" and that is the one failure
   mode a change-impact gate must never have.
3. **Never let an internal-API failure read as "safe."** If `impacts == 0` while the graph has
   edges, the correct status is `inconclusive`, not a zero score.
4. **Prefer the public API where it covers the edge** — currently object types, link types,
   action types, and datasources.
