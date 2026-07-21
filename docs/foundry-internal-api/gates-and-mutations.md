# Foundry native change gates & the agent mutation surface

Scope: does Foundry already have a native change-impact / approval gate we should hook
into rather than reinvent, and what is the full set of things an agent can *change*?
Live-tested against `zap.usw-18.palantirfoundry.com` (profile `zap`). Endpoints marked
**VERIFIED** returned a real status this session; **UNVERIFIED** = contract known from
the `@palantir/mcp` v0.397.0 source only, not live-exercised.

---

## VERDICT — hook in, don't reinvent (for the change *ledger*); build our own *enforcement*

**Foundry already ships three native change-review mechanisms and native impact
primitives. Reuse them as evidence sources; they are NOT a usable pre-mutation gate on
their own.** None of them *blocks* an agent that calls the mutation endpoint directly —
they are opt-in workflows, and the biggest mutations (dataset commits, ontology edits via
`modify`, marking/role changes) can be executed with no proposal at all. So:

- **Reuse natively** (don't rebuild impact analysis): Foundry itself computes affected
  resources. Hook these as the gate's evidence layer.
- **Build our own enforcement layer** (the actual *block*): a wrapper/policy in the
  pltr-cli fork that refuses a mutation call unless impact evidence was gathered — because
  no native mechanism is mandatory or intercepts the raw Conjure/GraphQL call.

### Native mechanisms (evidence sources to hook)
| Mechanism | Service | What it gives a gate | State on this stack |
|---|---|---|---|
| **Global Branching + Proposals** | `branch-service` | Cross-service branch; `createProposal`→`checkProposalDeployable` returns a **Merge Order + Merge Graph** (Foundry-computed affected resources); `getResourceDependencyGraph`, `checkBranchPreviewable` | **ENABLED** (`GET /branch/backend-information`→200, consumer versions for ontology/module/authoring/monitor/…); permission-gated (`branch:view-branch`→403). No *active* global-branch instances found (see below) |
| **Stemma Pull Requests** | `stemma-pull-request` + `stemma` | Code-review gate for transform repos. `getApprovalResult`/`getV2ApprovalResult` = **approval policy + which rules are satisfied**; `getMergeInfo` = mergeability; **`addRequiredChecks`/`removeRequiredChecks`/`getRequiredChecksForBranches`** = native per-branch required-check gate; `createCommitCheckResult`; `reviewPullRequest` **forbids author self-review** (native separation-of-duty) | Endpoints VERIFIED in catalogue; live PR read UNVERIFIED (`GET /pulls` needs a repo arg, timed out bare) |
| **Ontology change-preview** | `ontology-metadata` | `dryRunModifyOntology` (`/ontology/v2/modify/dry-run`), `getModifiedEntities` (`/ontology/v2/{ont}/diff`), branch `findConflicts` / `validate` / `merge/dry-run` — native diff + conflict detection before an ontology edit lands | Endpoints reachable (400 on empty body = exist, contract UNVERIFIED); ontology load VERIFIED |
| **Egress-policy approvals** | `resource-policy-manager` | `createPendingApprovalPolicy`, `getPolicyApprovalRequests`, `disapprovePolicy` — a real approval-request workflow, but only for **network-egress** policies (not resource ACLs) | catalogue only |

`pltr proposal` already models both worlds (`CODE_PR` + `GLOBAL_PROPOSAL`: create/get/
list/comment/approve/request-changes/merge/accept/close) — but its
`SDK_REACHABLE_CAPABILITIES` is an **empty frozenset**: the pinned public
`foundry-platform-sdk` exposes *none* of these. **The real proposal API is the internal
Conjure `branch-service` + `stemma-pull-request` services** — the fork must call those.

### Global branch determination (task Q1)
Global Branching is **enabled but unused**. Evidence: `branch-service` answers and enforces
perms; all three ontologies load with `defaultBranchRid` whose `branchDetails.type ==
"default"` (no proposal/non-default branches); there is **no "list branches/proposals"
endpoint** (load-by-rid only), and probing an arbitrary rid yields `branch:view-branch`
403, not data. So `getResourceDependencyGraph`'s **response shape stays UNVERIFIED** (never
got a 200 — no live global branch to load). GraphQL `globalBranch(rid:)` /
`defaultOrGlobalBranch(identifier:)` exist on `Query` (`GlobalBranchIdentifierInput` is a
strict OneOf) but resolve nothing without a real global-branch rid.

### GraphQL Mutation root (task Q4)
**The `Mutation` root EXISTS** — `mutation { __typename }` → `"Mutation"` **VERIFIED**, despite
introspection reporting `mutationType: null`. But it is a **thin Compass-navigation surface
only.** Enumerated live via the FieldUndefined oracle, the *entire* real field set is:
`createProject`, `createProjectFromTemplate`, `favorite`, `unfavorite`, `renameResource`,
`moveResources`, `trashResources`. Every heavier candidate (ontology/dataset/transform/
function/module/marking/role mutation) returned `FieldUndefined`. **A gate at the GraphQL
layer would miss ~everything** — all semantic mutations go through internal Conjure REST.

---

## Mutation surface catalogue — every way an agent changes something that matters

**Size: 303 of 832 endpoints (~36%) are mutations** the gate must intercept (POST 183 ·
PUT 83 · DELETE 37); 529 are reads. Classify on **method-name tokens, not HTTP verb** —
Conjure overloads verbs, so 66 read-PUTs (`getBranchesForResources` PUT, `checkProposalDeployable`
PUT) and ~220 read-POSTs would false-positive on verb alone. First camelCase token
`get/load/check/search/validate/list/find/query/resolve/is/exists/can/dryRun/download` ⇒ READ
even on POST/PUT — EXCEPT `getOrCreate*`/`findOrCreate*` (writes). Verb-overloaded writes the
name is the only signal for: `openFile`, `completeDeletion`, `touch`, `discardChangesOnBranch`,
`makeApplicationUnscoped`, `storeToken`, `duplicateWebhook`, `snoozeChecks`. Token-boundary
match, not substring (`Dataset`≠`set`, `cancel`≠`can`).

Grouped by blast-radius class, with the impact analysis a gate must gather first. Paths are
relative to each service's Conjure base `/<service-with-dashes>/api`.

**A. Ontology / semantic model** — `ontology-metadata`
`OntologyModificationService.modifyOntology POST /ontology/v2/modify` (the core edit: object/
link/action/interface/shared-property types), `createOntology`/`updateOntology`/
`deleteOntology`, `importSharedPropertyTypes`; branch: `createOntologyBranch`,
`mergeOntologyServiceBranch`, `setOntologyBranchLock`, `discardChangesOnBranch`,
`setOntologyBranchOrganizationMarkings`. *Gate needs:* objects/actions/functions referencing
the type, datasources backing it, downstream Workshop/queries — use native `dryRunModifyOntology`
+ `getModifiedEntities` diff + branch `findConflicts`.

**B. Dataset / schema / data** — `foundry-catalog`, `foundry-metadata`
`createDataset`/`createDataset2`/`createDatasetWithParent`, `deleteDataset`, dataset branches
(`createBranch2`/`updateBranch2`/`deleteBranch`); **transactions** `startTransaction`→
`setTransactionType`→`commitTransaction`/`abortTransaction` (**the actual data-write path**);
`updateDatasetBackingFilesystem`. Metadata/schema: `putDatasetMetadata`, `putTransactionMetadata`,
`putDatasetViewMetadata`, `delete*Metadata`. *Gate needs:* downstream build DAG + Ontology
object types backed by this dataset + datahealth checks on it.

**C. Transforms + code** — `stemma`, `build2`
`stemma`: `createRepository`/`deleteRepository`, `createFork`, `createRef`/`deleteRef`,
`addRequiredChecks`/`removeRequiredChecks`, `createCommitCheckResult`. `build2` (transform
DAG): `putJobSpecs`/`putJobSpecs2`/`putJobSpecsOnBehalfOf`, `removeJobSpecs`/`removeJobSpecs2`.
*Gate needs:* downstream datasets/schedules — **build2 gives it natively**: `getDownstreamJobSpecs`,
`getUpstreamJobSpecs`, plus pre-flight `canPutJobSpecs`/`canRunJobSpecs`/`canRemoveJobSpecs`.

**D. Application layer** — `module-group`, `function-registry`, `third-party-application`,
`webhooks`
Modules/compute: `createDeployedApp`/`updateDeployedApp`, `setApplicationPermissionCredentials`,
`executeOnDeployedApp`/`executeOnComputeModule`, `cancel*`. Functions:
`createFunction`/`deleteFunction`, `registerFunctionSpec`/`batchRegisterFunctionSpecs`,
`batchAppendOntologyBinding`, `setFunctionVisibility`. Webhooks: `createWebhook`,
`publishWebhookVersion`, `deleteWebhook`, `createFunctionForWebhook`. *Gate needs:* which
Workshop apps / action types / ontology bindings invoke this function or webhook.

**E. Permissions / policy / markings** — `compass`, `control-panel`, `resource-policy-manager`,
`third-party-application`
**Highest-sensitivity, no native impact analysis; require human approval, not just evidence.**
`compass`: `updateResourceMarkings POST /markings/{rid}` (classification/CBAC — changes *who
can see data*), `updateResourceRoles`/`updateResourceRolesV2`/`updateResourcesRoles` (bulk),
`removeAllDisableInheritedPermissions`. `control-panel`: `createOrganizationInEnrollment`,
`createInternalGroupWithAdministrativePermissions` (privilege escalation),
`updateEnrollment`/`updateEnrollmentHosts`, **`registerEnrollmentForDeletion`** (tenant-wide
destruction). `resource-policy-manager`: network-egress `createPolicy`/`updatePolicyPermissions`/
`updateRevocation`/`forceDeletePolicy` (its own approval-request workflow — data-exfil boundary).
OAuth apps (`third-party-application`): `updateApplicationScopes` (`/applications/{rid}/scoping`),
`makeApplicationUnscoped`, `setApplicationPermissionCredentials` — change an app/module's data
access. *Gate needs:* effective-grant delta (who gains/loses access) — read
`getEffectiveRoleGrants`/`getAllPrincipalsWithRoleOnResources` before & after.

**F. Resource-tree lifecycle** — `compass`
`moveResources`, `setName`/`renameResource`, `TrashService.addToTrash`→`deletePermanently`
(irreversible), `restore`, `registerProjectForDeletion`, `createResourceFrom`, `postResource`.
Also GraphQL: `trashResources`/`moveResources`/`renameResource`/`createProject`. *Gate needs:*
children/descendants of the resource + inbound references (imports, ontology datasources).

**G. Data-connection / sync** — `magritte-coordinator`
`addSourceV2`/`addSourceV3`, `updateSourceConfig`, `deleteSource`, `setRuntimePlatform`,
`deleteAgentAssignment`. *Gate needs:* datasets/syncs bound to the source + credential blast
radius. (Note `getSourceConfigWithPlaintextSecretValues` = secret-exposure read.)

**H. Health / monitoring / webhook config** — `foundry-datahealth`, `webhooks`
`createCheck`/`updateCheck`/`deleteCheck`/`deleteChecks`, **`pauseCheck`/`pauseAll`/`snoozeChecks`**
(monitoring-blinding — silent bad data downstream), `createCheckGroup`, `add/removeChecksToCheckGroup`,
`setPermissionCheckGroup`; webhooks `createWebhook`/`publishWebhookVersion` (side-effecting on
ontology events — treat like enabling an automation)/`deleteWebhook`/`createFunctionForWebhook`.
*Gate needs:* which datasets/schedules the check guards, and what a webhook fires.

**I. AIP indexes / secrets / collaboration** — `language-model-service`, `authoring-server`, `comments`
Semantic/RAG indexes: `createIndex`/`createIndexV2`/`createIndexAndResource`, `deleteIndex`,
`updateDocuments(WithEmbeddings)` (`/semantics/v1/...`) — feeds AIP/RAG surfaces. Secrets:
**`storeToken`/`storeProjectToken`** (`/security/token/store*`) — treat as secret-write, gate + audit.
Comments/reactions/log ingest = negligible blast radius; allow-list to cut gate noise.

### Top blast-radius endpoints a gate MUST intercept
1. `compass updateResourceMarkings POST /markings/{rid}` — data-access classification
2. `compass updateResourceRoles(V2)/updateResourcesRoles POST /roles/**` — ACLs
3. `ontology modifyOntology POST /ontology/v2/modify` — semantic model
4. `foundry-catalog commitTransaction POST /catalog/datasets/{rid}/transactions/{txn}/commit` — data write
5. `foundry-catalog deleteDataset DELETE /catalog/datasets`
6. `compass TrashService.deletePermanently POST /trash/delete` — irreversible
7. `build2 putJobSpecs POST /jobspecs/put-jobspecs` — transform DAG
8. `stemma deleteRepository DELETE /repos/{rid}` + `removeRequiredChecks` — removing the code gate
9. `control-panel createInternalGroupWithAdministrativePermissions` — privilege escalation
10. `magritte deleteSource / updateSourceConfig` — data-connection integrity
11. `ontology-metadata mergeOntologyServiceBranch` / `branch-service deployProposal` — landing a branch
12. `function-registry deleteFunction` / `registerFunctionSpec` — logic behind actions/apps
13. `webhooks deleteWebhook / publishWebhookVersion`
14. `foundry-datahealth deleteCheck / updateCheck` — weakening quality gates
15. GraphQL `mutation { trashResources }` — the one Compass mutation that also lives on GraphQL

---

## Recommendation for the pltr-cli fork
1. **Enforce our own pre-mutation gate** in the fork: intercept any call whose method name is
   a write verb (§A–H) and require an impact-evidence artifact first. Nothing native is
   mandatory, so the block must be ours.
2. **Populate that evidence from native primitives** (don't rebuild lineage): build2
   up/downstream jobspecs + `can*` checks, ontology `dryRunModify`/`diff`/`findConflicts`,
   branch-service `checkProposalDeployable` Merge Graph, compass effective-role-grant deltas.
3. **Prefer the native proposal path where it exists** (transform code → stemma PR + required
   checks; cross-service semantic change → global branch + proposal) so the change carries a
   Foundry-side review record — but never *rely* on it as the enforcement point.
