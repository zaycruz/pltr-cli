"""Bounded, evidence-backed Foundry dependency graph discovery.

The graph deliberately stores intrinsic relations independently from the
direction in which a particular root traverses them.  Every SDK read is also
recorded as immutable operation provenance; collectors never infer coverage
from an empty graph alone.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from time import monotonic
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from foundry_sdk import FoundryClient
from foundry_sdk import _errors as sdk_errors

from .base import BaseService
from .dataset import DatasetService
from .orchestration import OrchestrationService


SDK_PACKAGE = "foundry-platform-sdk"
METADATA_WALK_MAX_DEPTH = 64
CAPABILITY_IDS = frozenset(f"CAP-{number:02d}" for number in range(1, 17))
ACTION_LOGIC_RULE_TYPES = frozenset(
    {
        "modifyObject",
        "deleteLink",
        "modifyInterface",
        "createOrModifyObject",
        "createObject",
        "createLink",
        "batchedFunction",
        "createOrModifyObjectV2",
        "deleteInterfaceLink",
        "deleteObject",
        "function",
        "createInterfaceLink",
        "createInterface",
        "applyScenario",
    }
)
QUERY_DATA_TYPE_TYPES = frozenset(
    {
        "array",
        "attachment",
        "boolean",
        "date",
        "double",
        "entrySet",
        "float",
        "integer",
        "interfaceObject",
        "interfaceObjectSet",
        "long",
        "mediaReference",
        "null",
        "object",
        "objectSet",
        "set",
        "string",
        "struct",
        "threeDimensionalAggregation",
        "timestamp",
        "twoDimensionalAggregation",
        "typeReference",
        "union",
        "unsupported",
        "void",
    }
)


@dataclass(frozen=True)
class SDKOperationSpec:
    capability_ids: tuple[str, ...]
    branch: bool
    preview: bool
    namespace: str
    method: str

    def __post_init__(self) -> None:
        if not self.capability_ids or not set(self.capability_ids) <= CAPABILITY_IDS:
            raise ValueError("SDK operation must reference valid capability IDs")


SDK_OPERATION_SPECS: dict[str, SDKOperationSpec] = {
    "object-type.get-full-metadata": SDKOperationSpec(("CAP-01", "CAP-16"), True, True, "client.ontologies.Ontology.ObjectType", "get_full_metadata"),
    "object-type.get-outgoing-link-type": SDKOperationSpec(("CAP-01", "CAP-16"), True, False, "client.ontologies.Ontology.ObjectType", "get_outgoing_link_type"),
    "action-type.get-full-metadata": SDKOperationSpec(("CAP-02", "CAP-16"), True, True, "client.ontologies.ActionTypeFullMetadata", "get"),
    "action-type.list-full-metadata": SDKOperationSpec(("CAP-02", "CAP-03", "CAP-13", "CAP-16"), True, True, "client.ontologies.ActionTypeFullMetadata", "list"),
    "query-type.list": SDKOperationSpec(("CAP-04", "CAP-05", "CAP-13", "CAP-16"), True, False, "client.ontologies.Ontology.QueryType", "list"),
    "dataset.get-schedules": SDKOperationSpec(("CAP-06", "CAP-13", "CAP-16"), True, False, "client.datasets.Dataset", "get_schedules"),
    "schedule.get": SDKOperationSpec(("CAP-07", "CAP-16"), False, True, "client.orchestration.Schedule", "get"),
    "schedule.get-affected-resources": SDKOperationSpec(("CAP-07", "CAP-16"), False, True, "client.orchestration.Schedule", "get_affected_resources"),
    "schedule.runs": SDKOperationSpec(("CAP-07", "CAP-13", "CAP-16"), False, False, "client.orchestration.Schedule", "runs"),
    "build.get": SDKOperationSpec(("CAP-08", "CAP-09", "CAP-16"), False, False, "client.orchestration.Build", "get"),
    "build.jobs": SDKOperationSpec(("CAP-08", "CAP-09", "CAP-13", "CAP-16"), False, False, "client.orchestration.Build", "jobs"),
    "filesystem.resource.get": SDKOperationSpec(("CAP-10", "CAP-16"), False, False, "client.filesystem.Resource", "get"),
    "third-party-application.get": SDKOperationSpec(("CAP-10", "CAP-16"), False, True, "client.third_party_applications.ThirdPartyApplication", "get"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_id(prefix: str, *values: Any) -> str:
    material = "\x1f".join(str(value) for value in values)
    return f"{prefix}_{sha256(material.encode()).hexdigest()[:24]}"


def _sdk_version() -> str:
    try:
        return version(SDK_PACKAGE)
    except PackageNotFoundError:
        return "unknown"


@dataclass(frozen=True)
class ArgumentObservation:
    mode: str
    value: Optional[str | bool] = None

    def __post_init__(self) -> None:
        if self.mode not in {"explicit", "server-default", "not-applicable"}:
            raise ValueError(f"invalid argument observation mode: {self.mode}")
        if self.mode != "explicit" and self.value is not None:
            raise ValueError("only explicit arguments may carry values")


@dataclass(frozen=True)
class OperationProvenance:
    id: str
    read_context_id: str
    sdk_namespace: str
    sdk_method: str
    capability_ids: tuple[str, ...]
    invocation_sdk_version: str
    invoked_at: str
    observed_at: str
    branch_argument: ArgumentObservation
    preview_argument: ArgumentObservation
    request_timeout_seconds: int
    known_limitations: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ReadContext:
    id: str
    profile: str
    host_fingerprint: str
    invocation_sdk_package: str
    invocation_sdk_version: str
    observed_at: str
    ontology_rid: Optional[str] = None
    requested_branch: Optional[str] = None
    dataset_branch: Optional[str] = None


@dataclass(frozen=True)
class DependencyTarget:
    kind: str
    identifiers: dict[str, str]
    display_name: Optional[str] = None
    node_id: Optional[str] = None


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    display_name: str
    identifiers: dict[str, str]
    read_context_id: str
    is_target: bool = False


@dataclass(frozen=True)
class Evidence:
    id: str
    operation_provenance_id: str
    locator: str
    field_path: str
    raw_type: str
    response_discriminator: Optional[str] = None


@dataclass(frozen=True)
class Edge:
    id: str
    source: str
    target: str
    relation_kind: str
    traversal_class: str
    intrinsic_orientation: str
    evidence_ids: tuple[str, ...]
    coverage: str = "verified"


@dataclass(frozen=True)
class PathStep:
    edge_id: str
    from_node: str
    to_node: str
    traversal_direction: str
    evidence_ids: tuple[str, ...]


@dataclass
class CoverageRecord:
    target_kind: str
    surface: str
    subject_node_id: str
    read_context_id: str
    parent_record_id: Optional[str] = None
    applicability_evidence_id: Optional[str] = None
    status: Optional[str] = None
    attempted: bool = False
    complete: bool = False
    evidence_ids: list[str] = field(default_factory=list)
    error_id: Optional[str] = None
    reason: Optional[str] = None

    @property
    def id(self) -> str:
        return _stable_id("coverage", self.subject_node_id, self.surface, self.read_context_id)


@dataclass(frozen=True)
class CoverageGap:
    surface: str
    target: str
    coverage: str
    reason_code: str
    message: str
    read_context_id: str
    retryable: bool
    operation: Optional[str] = None
    budget_snapshot: Optional[dict[str, Any]] = None
    locator: Optional[str] = None

    @property
    def id(self) -> str:
        # The same failure class may occur at several independently actionable
        # locations in one response.  Message is part of the fallback identity
        # for older callers that do not yet provide a structured locator.
        return _stable_id(
            "gap",
            self.target,
            self.surface,
            self.reason_code,
            self.locator or self.message,
            self.read_context_id,
        )


@dataclass(frozen=True)
class ClassifiedFailure:
    error_class: str
    coverage: str
    retryable: bool


class DependencyFatalError(RuntimeError):
    def __init__(self, error_class: str, target: str, operation: str, message: str, retryable: bool, read_context_id: str):
        super().__init__(message)
        self.error_class = error_class
        self.target = target
        self.operation = operation
        self.retryable = retryable
        self.read_context_id = read_context_id

    def to_dict(self) -> dict[str, Any]:
        return {"error_class": self.error_class, "target": self.target, "operation": self.operation, "message": str(self), "retryable": self.retryable, "read_context_id": self.read_context_id}


class BudgetExhausted(RuntimeError):
    def __init__(self, dimension: str, snapshot: dict[str, Any]):
        super().__init__(f"discovery {dimension} budget exhausted")
        self.dimension = dimension
        self.snapshot = snapshot
        self.partial_action_references: list[tuple[str, str, str, str]] = []
        self.partial_query_leaves: list[tuple[str, str, str]] = []


@dataclass
class DiscoveryBudget:
    max_requests: int = 200
    max_pages: int = 100
    max_items: int = 10_000
    max_nodes: int = 150
    max_depth: int = 2
    time_budget_seconds: float = 60.0
    used_requests: int = 0
    used_pages: int = 0
    used_items: int = 0
    used_nodes: int = 0
    _started_at: float = field(default_factory=monotonic, repr=False)

    HARD_CEILINGS = {"max_requests": 1000, "max_pages": 500, "max_items": 100_000, "max_nodes": 1000, "max_depth": 10, "time_budget_seconds": 600}

    def __post_init__(self) -> None:
        for name, ceiling in self.HARD_CEILINGS.items():
            value = getattr(self, name)
            if value <= 0 or value > ceiling:
                raise ValueError(f"{name} must be between 1 and {ceiling}")

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, monotonic() - self._started_at)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.time_budget_seconds - self.elapsed_seconds)

    def request_timeout(self, configured: float) -> int:
        self._check_time()
        timeout = int(min(float(configured), self.remaining_seconds))
        if timeout < 1:
            raise BudgetExhausted("time", self.snapshot())
        return timeout

    def charge(self, dimension: str, amount: int = 1) -> None:
        self._check_time()
        used_name = f"used_{dimension}"
        limit_name = f"max_{dimension}"
        if not hasattr(self, used_name) or not hasattr(self, limit_name):
            raise ValueError(f"unknown budget dimension: {dimension}")
        if getattr(self, used_name) + amount > getattr(self, limit_name):
            raise BudgetExhausted(dimension, self.snapshot())
        setattr(self, used_name, getattr(self, used_name) + amount)

    def check_depth(self, depth: int) -> None:
        self._check_time()
        if depth > self.max_depth:
            raise BudgetExhausted("depth", self.snapshot())

    def check_metadata_depth(self, depth: int) -> None:
        """Bound nested metadata independently from graph-hop depth."""
        self._check_time()
        if depth > METADATA_WALK_MAX_DEPTH:
            snapshot = self.snapshot()
            snapshot["limits"]["metadata_depth"] = METADATA_WALK_MAX_DEPTH
            raise BudgetExhausted("metadata_depth", snapshot)

    def reserve_page(self, requested_size: int) -> int:
        """Reserve a remote page read and cap it to remaining item capacity."""
        self._check_time()
        # Preserve request-budget precedence from the invocation boundary while
        # ensuring a failed request preflight does not consume a page.
        if self.used_requests >= self.max_requests:
            raise BudgetExhausted("requests", self.snapshot())
        if self.used_pages >= self.max_pages:
            raise BudgetExhausted("pages", self.snapshot())
        remaining_items = self.max_items - self.used_items
        if remaining_items <= 0:
            raise BudgetExhausted("items", self.snapshot())
        self.charge("pages")
        return min(requested_size, remaining_items)

    def _check_time(self) -> None:
        if self.elapsed_seconds >= self.time_budget_seconds:
            raise BudgetExhausted("time", self.snapshot())

    def snapshot(self) -> dict[str, Any]:
        return {
            "used": {"requests": self.used_requests, "pages": self.used_pages, "items": self.used_items, "nodes": self.used_nodes, "elapsed_seconds": round(self.elapsed_seconds, 6)},
            "limits": {"requests": self.max_requests, "pages": self.max_pages, "items": self.max_items, "nodes": self.max_nodes, "depth": self.max_depth, "time_budget_seconds": self.time_budget_seconds},
        }


@dataclass
class AnalysisContext:
    read_context: ReadContext
    budget: DiscoveryBudget
    configured_request_timeout_seconds: float = 30.0
    operation_provenance: dict[str, OperationProvenance] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[str, Edge] = field(default_factory=dict)
    coverage_records: dict[str, CoverageRecord] = field(default_factory=dict)
    gaps: dict[str, CoverageGap] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    caches: dict[tuple[Any, ...], Any] = field(default_factory=dict)

    @classmethod
    def create(cls, profile: str = "default", host: str = "", ontology_rid: Optional[str] = None, requested_branch: Optional[str] = None, dataset_branch: Optional[str] = None, budget: Optional[DiscoveryBudget] = None, request_timeout_seconds: float = 30.0) -> "AnalysisContext":
        sdk_version = _sdk_version()
        host_fingerprint = sha256(host.rstrip("/").lower().encode()).hexdigest()[:16] if host else "unknown"
        observed_at = _utc_now()
        context_id = _stable_id("readctx", profile, host_fingerprint, ontology_rid, requested_branch, dataset_branch, SDK_PACKAGE, sdk_version)
        read_context = ReadContext(context_id, profile, host_fingerprint, SDK_PACKAGE, sdk_version, observed_at, ontology_rid, requested_branch, dataset_branch)
        return cls(read_context, budget or DiscoveryBudget(), request_timeout_seconds)


RELATION_KINDS: dict[str, tuple[str, str]] = {
    "action-affects-object": ("dependency-flow", "source_to_target"),
    "action-uses-function": ("dependency-flow", "source_to_target"),
    "query-returns-object": ("dependency-flow", "source_to_target"),
    "query-accepts-object": ("dependency-flow", "source_to_target"),
    "schedule-consumes-resource": ("dependency-flow", "source_to_target"),
    "schedule-produces-resource": ("dependency-flow", "source_to_target"),
    "schedule-triggered-by-resource": ("dependency-flow", "source_to_target"),
    "run-submitted-build": ("dependency-flow", "source_to_target"),
    "schedule-run": ("dependency-flow", "source_to_target"),
    "build-produced-output": ("dependency-flow", "source_to_target"),
    "declared-link": ("adjacent-structural", "declared_source_to_target"),
    "container-member": ("adjacent-structural", "container_to_member"),
    "peer": ("adjacent-structural", "peer_canonical"),
    "project-scope": ("adjacent-structural", "container_to_member"),
    "build-co-output": ("adjacent-structural", "peer_canonical"),
}


STATIC_SURFACES = ("ontology-structure-backing", "full-action-metadata", "query-related-function-metadata", "dataset-orchestration", "application-internals", "workshop-internals", "compass-metadata")
MATRIX_GAPS: dict[str, dict[str, str]] = {
    "object-type": {"dataset-orchestration": "unsupported-dataset-orchestration", "application-internals": "unsupported-application-internals", "workshop-internals": "unsupported-workshop-internals", "compass-metadata": "ontology-compass-mapping-unavailable"},
    "property": {"dataset-orchestration": "dataset-column-lineage-unavailable", "application-internals": "unsupported-application-internals", "workshop-internals": "unsupported-workshop-internals", "compass-metadata": "ontology-compass-mapping-unavailable"},
    "link-type": {"dataset-orchestration": "unsupported-dataset-orchestration", "application-internals": "unsupported-application-internals", "workshop-internals": "unsupported-workshop-internals", "compass-metadata": "ontology-compass-mapping-unavailable"},
    "action-type": {"dataset-orchestration": "unsupported-dataset-orchestration", "application-internals": "unsupported-application-internals", "workshop-internals": "unsupported-workshop-internals", "compass-metadata": "ontology-compass-mapping-unavailable"},
    "query-type": {"dataset-orchestration": "unsupported-dataset-orchestration", "application-internals": "unsupported-application-internals", "workshop-internals": "unsupported-workshop-internals", "compass-metadata": "ontology-compass-mapping-unavailable"},
    "dataset": {"ontology-structure-backing": "ontology-backing-mapping-unavailable", "full-action-metadata": "reverse-action-mapping-unavailable", "query-related-function-metadata": "reverse-query-mapping-unavailable", "application-internals": "unsupported-application-internals", "workshop-internals": "unsupported-workshop-internals"},
    "third-party-application": {surface: "unsupported-application-internals" if surface == "application-internals" else "unsupported-resource-surface" for surface in STATIC_SURFACES if surface != "compass-metadata"},
    "workshop-resource": {surface: "unsupported-workshop-internals" if surface == "workshop-internals" else "unsupported-resource-surface" for surface in STATIC_SURFACES if surface != "compass-metadata"},
    "generic-resource": {surface: "unknown-resource-capabilities" for surface in STATIC_SURFACES if surface != "compass-metadata"},
}


def classify_exception(error: BaseException) -> ClassifiedFailure:
    chain: list[BaseException] = []
    current: Optional[BaseException] = error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    for candidate in chain:
        if getattr(candidate, "name", None) == "BranchNotFound":
            return ClassifiedFailure("branch-not-found", "unresolved", False)
        mapping = (
            ((sdk_errors.UnauthorizedError, sdk_errors.NotAuthenticated), ClassifiedFailure("authentication", "inaccessible", False)),
            ((sdk_errors.PermissionDeniedError,), ClassifiedFailure("permission-denied", "inaccessible", False)),
            ((sdk_errors.NotFoundError,), ClassifiedFailure("not-found", "unresolved", False)),
            ((sdk_errors.ApiNotFoundError,), ClassifiedFailure("unsupported", "unsupported", False)),
            ((sdk_errors.RateLimitError, sdk_errors.PalantirQoSException), ClassifiedFailure("rate-limited", "partial", True)),
            ((sdk_errors.TimeoutError,), ClassifiedFailure("timeout", "partial", True)),
            ((sdk_errors.ConnectionError, sdk_errors.ProxyError), ClassifiedFailure("connection", "partial", True)),
            ((sdk_errors.BadRequestError, sdk_errors.UnprocessableEntityError), ClassifiedFailure("invalid-request", "unresolved", False)),
            ((sdk_errors.InternalServerError,), ClassifiedFailure("internal", "partial", True)),
        )
        for classes, result in mapping:
            if isinstance(candidate, classes):
                return result
        if isinstance(candidate, sdk_errors.PalantirRPCException):
            return ClassifiedFailure("connection", "partial", True)
        if isinstance(candidate, BudgetExhausted):
            return ClassifiedFailure("budget-exhausted", "budget-exhausted", True)
    return ClassifiedFailure("unknown", "unresolved", False)


def _is_expected_collection_failure(error: BaseException) -> bool:
    """Return whether a failed read is safe to represent as a coverage gap.

    SDK/API failures and an exhausted discovery budget are expected operational
    outcomes.  Python errors are implementation or response-shape invariants;
    allowing those to become an ``unknown`` gap would make a broken collector
    look like a successful partial analysis.
    """

    return classify_exception(error).error_class != "unknown"


class DependencyGraphService(BaseService):
    """Resolve targets and compose bounded collectors into one canonical graph."""

    def __init__(self, profile: Optional[str] = None, client: Optional[FoundryClient] = None):
        super().__init__(profile)
        if client is not None:
            self._client = client

    def _get_service(self) -> FoundryClient:
        return self.client

    def create_context(self, *, host: str = "", ontology_rid: Optional[str] = None, requested_branch: Optional[str] = None, dataset_branch: Optional[str] = None, budget: Optional[DiscoveryBudget] = None, request_timeout_seconds: float = 30.0) -> AnalysisContext:
        return AnalysisContext.create(self.profile or "default", host, ontology_rid, requested_branch, dataset_branch, budget, request_timeout_seconds)

    def resolve_object_type(self, context: AnalysisContext, ontology_rid: str, object_type: str) -> DependencyTarget:
        return self._resolve_ontology_target(context, "object-type", ontology_rid, object_type)

    def resolve_property(self, context: AnalysisContext, ontology_rid: str, object_type: str, property_name: str) -> DependencyTarget:
        target = self._resolve_ontology_target(context, "property", ontology_rid, object_type, property_name)
        return target

    def resolve_link_type(self, context: AnalysisContext, ontology_rid: str, object_type: str, link_type: str) -> DependencyTarget:
        target = self._resolve_ontology_target(context, "link-type", ontology_rid, object_type, link_type)
        return target

    def resolve_action_type(self, context: AnalysisContext, ontology_rid: str, action_type: str) -> DependencyTarget:
        kwargs = {"ontology": ontology_rid, "action_type": action_type, "preview": True}
        if context.read_context.requested_branch is not None:
            kwargs["branch"] = context.read_context.requested_branch
        metadata, operation_id = self._invoke_sdk(context, "action-type.get-full-metadata", self.client.ontologies.ActionTypeFullMetadata.get, kwargs, target=action_type, fatal=True)
        node = self._add_node(context, "action-type", action_type, {"ontology_rid": ontology_rid, "action_type": action_type}, True)
        context.caches[("action-metadata", ontology_rid, context.read_context.requested_branch, action_type)] = (metadata, operation_id)
        return DependencyTarget("action-type", node.identifiers, node.display_name, node.id)

    def resolve_query_type(self, context: AnalysisContext, ontology_rid: str, query_type: str) -> DependencyTarget:
        try:
            index = self._get_query_index(context, ontology_rid)
        except Exception as error:
            if not _is_expected_collection_failure(error):
                raise
            classified = classify_exception(error)
            raise DependencyFatalError(classified.error_class, query_type, "query-type.list", str(error), classified.retryable, context.read_context.id) from error
        if query_type not in index["by_name"] and index.get("incomplete_error") is not None:
            error = index["incomplete_error"]
            classified = classify_exception(error)
            raise DependencyFatalError(
                classified.error_class,
                query_type,
                "query-type.list",
                str(error),
                classified.retryable,
                context.read_context.id,
            ) from error
        if query_type not in index["by_name"]:
            raise DependencyFatalError("not-found", query_type, "query-type.list", f"Query type {query_type} was not found", False, context.read_context.id)
        query, operation_id = index["by_name"][query_type]
        node = self._add_node(context, "query-type", query_type, {"ontology_rid": ontology_rid, "query_type": query_type}, True)
        context.caches[("query-metadata", ontology_rid, context.read_context.requested_branch, query_type)] = (query, operation_id)
        return DependencyTarget("query-type", node.identifiers, node.display_name, node.id)

    def resolve_resource(self, context: AnalysisContext, resource_rid: str) -> DependencyTarget:
        if not resource_rid.startswith("ri."):
            raise DependencyFatalError("unsupported-addressability", resource_rid, "filesystem.resource.get", "resource targets must be resolvable Foundry RIDs", False, context.read_context.id)
        resource, operation_id = self._invoke_sdk(context, "filesystem.resource.get", self.client.filesystem.Resource.get, {"resource_rid": resource_rid}, target=resource_rid, fatal=True)
        data = self._model_dict(resource)
        resource_type = self._resource_type(data)
        kind = self._resource_kind(resource_type)
        display_name = str(data.get("name") or data.get("display_name") or resource_rid)
        node = self._add_node(context, kind, display_name, {"resource_rid": resource_rid, "resource_type": resource_type or "unknown"}, True)
        self._add_evidence(context, operation_id, "resource", "resource", resource)
        context.caches[("resource", resource_rid)] = data
        return DependencyTarget(kind, node.identifiers, node.display_name, node.id)

    def analyze(self, target: DependencyTarget | Mapping[str, Any], context: AnalysisContext, direction: str = "both", change: Optional[str] = None) -> dict[str, Any]:
        if direction not in {"both", "upstream", "downstream", "adjacent"}:
            raise ValueError("direction must be both, upstream, downstream, or adjacent")
        target = self._coerce_target(target)
        if target.node_id is None:
            node = self._add_node(context, target.kind, target.display_name or self._target_label(target), target.identifiers, True)
            target = replace(target, node_id=node.id)
        self._initialize_matrix(context, target)
        self._discover_bfs(target, context, direction)
        self._complete_coverage(context)
        paths = self._derive_paths(context, target.node_id, direction)
        ranked = self._rank_paths(context, paths, change)
        result: dict[str, Any] = {
            "target": asdict(target),
            "read_contexts": [self._serialize(context.read_context)],
            "operation_provenance": [self._serialize(value) for _, value in sorted(context.operation_provenance.items())],
            "evidence": [self._serialize(value) for _, value in sorted(context.evidence.items())],
            "graph": {"nodes": [self._serialize(value) for _, value in sorted(context.nodes.items())], "edges": [self._serialize(value) for _, value in sorted(context.edges.items())]},
            "paths": paths,
            "ranked_relationships": ranked,
            "coverage": [self._serialize(value) for _, value in sorted(context.coverage_records.items())],
            "gaps": [dict(self._serialize(value), id=value.id) for _, value in sorted(context.gaps.items())],
            "errors": sorted(context.errors, key=lambda value: str(value)),
            "budget": context.budget.snapshot(),
            "summary": {"node_count": len(context.nodes), "edge_count": len(context.edges), "path_count": len(paths), "gap_count": len(context.gaps)},
        }
        if change is not None:
            result["change_assessment"] = self._assess_change(change, ranked, context)
        return result

    def _discover_bfs(self, target: DependencyTarget, context: AnalysisContext, direction: str) -> None:
        assert target.node_id is not None
        queue: deque[tuple[DependencyTarget, int, tuple[str, ...]]] = deque([(target, 0, ())])
        visited: set[str] = set()
        while queue:
            current, depth, directions = queue.popleft()
            assert current.node_id is not None
            if current.node_id in visited:
                continue
            try:
                context.budget.check_depth(depth)
                self._prepare_frontier_target(current, context)
                self._collect_target(current, context)
            except BudgetExhausted as error:
                context.caches[("budget-exhausted",)] = error
                self._budget_gap(context, current.node_id, "graph-discovery", error)
                self._terminalize_frontier_budget(context, current, error)
                break
            except Exception as error:
                record = self._coverage_record(context, current.kind, "frontier-collection", current.node_id)
                self._record_failure(context, record, error, "frontier-collection")
            visited.add(current.node_id)
            if depth >= context.budget.max_depth:
                undispatched: list[DependencyTarget] = []
                for edge in sorted(context.edges.values(), key=lambda value: value.id):
                    if current.node_id not in {edge.source, edge.target}:
                        continue
                    neighbor_id = edge.target if current.node_id == edge.source else edge.source
                    if neighbor_id in visited:
                        continue
                    neighbor = context.nodes[neighbor_id]
                    if neighbor.kind in {"object-type", "property", "link-type", "action-type", "query-type", "dataset", "third-party-application", "workshop-resource", "generic-resource"}:
                        undispatched.append(DependencyTarget(neighbor.kind, neighbor.identifiers, neighbor.display_name, neighbor.id))
                if undispatched:
                    error = BudgetExhausted("depth", context.budget.snapshot())
                    context.caches[("budget-exhausted",)] = error
                    self._budget_gap(context, current.node_id, "graph-discovery", error)
                    for frontier_target in undispatched:
                        self._terminalize_frontier_budget(context, frontier_target, error)
                continue
            candidates: list[tuple[str, DependencyTarget, tuple[str, ...]]] = []
            for edge in sorted(context.edges.values(), key=lambda value: value.id):
                if current.node_id not in {edge.source, edge.target}:
                    continue
                neighbor_id = edge.target if current.node_id == edge.source else edge.source
                if neighbor_id in visited:
                    continue
                step_direction = self._traversal_direction(edge, current.node_id)
                next_directions = directions + (step_direction,)
                if direction in {"upstream", "downstream"} and self._overall_direction_values(next_directions) != direction:
                    continue
                neighbor = context.nodes[neighbor_id]
                if neighbor.kind in {"object-type", "property", "link-type", "action-type", "query-type", "dataset", "third-party-application", "workshop-resource", "generic-resource"}:
                    candidates.append((neighbor.id, DependencyTarget(neighbor.kind, neighbor.identifiers, neighbor.display_name, neighbor.id), next_directions))
            for _, next_target, next_directions in sorted(candidates, key=lambda value: value[0]):
                queue.append((next_target, depth + 1, next_directions))

    def _prepare_frontier_target(self, target: DependencyTarget, context: AnalysisContext) -> None:
        if target.kind == "action-type":
            key = ("action-metadata", target.identifiers["ontology_rid"], context.read_context.requested_branch, target.identifiers["action_type"])
            if key not in context.caches:
                index = self._get_action_index(context, target.identifiers["ontology_rid"])
                if target.identifiers["action_type"] not in index["by_name"]:
                    if index.get("incomplete_error") is not None:
                        raise index["incomplete_error"]
                    raise KeyError(target.identifiers["action_type"])
                context.caches[key] = index["by_name"][target.identifiers["action_type"]]
        elif target.kind == "query-type":
            key = ("query-metadata", target.identifiers["ontology_rid"], context.read_context.requested_branch, target.identifiers["query_type"])
            if key not in context.caches:
                index = self._get_query_index(context, target.identifiers["ontology_rid"])
                if target.identifiers["query_type"] not in index["by_name"]:
                    if index.get("incomplete_error") is not None:
                        raise index["incomplete_error"]
                    raise KeyError(target.identifiers["query_type"])
                context.caches[key] = index["by_name"][target.identifiers["query_type"]]
        elif target.kind in {"object-type", "property", "link-type"}:
            key = ("object-metadata", target.identifiers["ontology_rid"], target.identifiers["object_type"])
            if key not in context.caches:
                kwargs: dict[str, Any] = {"ontology": target.identifiers["ontology_rid"], "object_type": target.identifiers["object_type"], "preview": True}
                if context.read_context.requested_branch is not None:
                    kwargs["branch"] = context.read_context.requested_branch
                context.caches[key] = self._invoke_sdk(context, "object-type.get-full-metadata", self.client.ontologies.Ontology.ObjectType.get_full_metadata, kwargs, target=target.identifiers["object_type"])
            if target.kind == "link-type":
                link_key = ("link-metadata", target.identifiers["ontology_rid"], target.identifiers["object_type"], target.identifiers["link_type"])
                if link_key not in context.caches:
                    link_kwargs: dict[str, Any] = {"ontology": target.identifiers["ontology_rid"], "object_type": target.identifiers["object_type"], "link_type": target.identifiers["link_type"]}
                    if context.read_context.requested_branch is not None:
                        link_kwargs["branch"] = context.read_context.requested_branch
                    context.caches[link_key] = self._invoke_sdk(context, "object-type.get-outgoing-link-type", self.client.ontologies.Ontology.ObjectType.get_outgoing_link_type, link_kwargs, target=target.identifiers["link_type"])

    def _terminalize_frontier_budget(self, context: AnalysisContext, target: DependencyTarget, error: BudgetExhausted) -> None:
        self._initialize_matrix(context, target)
        for record in sorted(context.coverage_records.values(), key=lambda value: value.id):
            if record.subject_node_id != target.node_id or record.complete:
                continue
            self._finish_coverage(record, "budget-exhausted", reason="budget-exhausted", attempted=False)
            self._add_gap(context, target.node_id or "", record.surface, "budget-exhausted", "budget-exhausted", str(error), retryable=True, budget_snapshot=error.snapshot)

    def _invoke_sdk(
        self,
        context: AnalysisContext,
        operation: str,
        call: Callable[..., Any],
        kwargs: Mapping[str, Any],
        *,
        target: str,
        fatal: bool = False,
        known_limitations: Sequence[dict[str, Any]] = (),
    ) -> tuple[Any, str]:
        spec = SDK_OPERATION_SPECS.get(operation)
        if spec is None:
            raise ValueError(f"unregistered SDK operation: {operation}")
        supplied = dict(kwargs)
        if "branch" in supplied and not spec.branch:
            raise ValueError(f"{operation} does not accept branch")
        if "preview" in supplied and not spec.preview:
            raise ValueError(f"{operation} does not accept preview")
        timeout = context.budget.request_timeout(
            context.configured_request_timeout_seconds
        )
        context.budget.charge("requests")
        supplied["request_timeout"] = timeout
        branch = self._argument_observation(spec.branch, supplied, "branch")
        preview = self._argument_observation(spec.preview, supplied, "preview")
        invoked_at = _utc_now()
        operation_id = _stable_id(
            "operation",
            context.read_context.id,
            operation,
            len(context.operation_provenance),
            invoked_at,
        )
        limitations = tuple(dict(value) for value in known_limitations)
        try:
            response = call(**supplied)
        except Exception as error:
            observed_at = _utc_now()
            context.operation_provenance[operation_id] = OperationProvenance(
                operation_id,
                context.read_context.id,
                spec.namespace,
                spec.method,
                spec.capability_ids,
                context.read_context.invocation_sdk_version,
                invoked_at,
                observed_at,
                branch,
                preview,
                timeout,
                limitations,
            )
            classified = classify_exception(error)
            if fatal:
                if not _is_expected_collection_failure(error):
                    raise
                raise DependencyFatalError(
                    classified.error_class,
                    target,
                    operation,
                    str(error),
                    classified.retryable,
                    context.read_context.id,
                ) from error
            raise
        context.operation_provenance[operation_id] = OperationProvenance(
            operation_id,
            context.read_context.id,
            spec.namespace,
            spec.method,
            spec.capability_ids,
            context.read_context.invocation_sdk_version,
            invoked_at,
            _utc_now(),
            branch,
            preview,
            timeout,
            limitations,
        )
        return response, operation_id

    @staticmethod
    def _argument_observation(
        supported: bool, kwargs: Mapping[str, Any], name: str
    ) -> ArgumentObservation:
        if not supported:
            return ArgumentObservation("not-applicable")
        argument_name = (
            "branch_name" if name == "branch" and "branch_name" in kwargs else name
        )
        if argument_name not in kwargs or kwargs[argument_name] is None:
            return ArgumentObservation("server-default")
        return ArgumentObservation("explicit", kwargs[argument_name])

    def _resolve_ontology_target(
        self,
        context: AnalysisContext,
        kind: str,
        ontology_rid: str,
        object_type: str,
        member: Optional[str] = None,
    ) -> DependencyTarget:
        kwargs: dict[str, Any] = {
            "ontology": ontology_rid,
            "object_type": object_type,
            "preview": True,
        }
        if context.read_context.requested_branch is not None:
            kwargs["branch"] = context.read_context.requested_branch
        metadata, operation_id = self._invoke_sdk(
            context,
            "object-type.get-full-metadata",
            self.client.ontologies.Ontology.ObjectType.get_full_metadata,
            kwargs,
            target=member or object_type,
            fatal=True,
        )
        identifiers = {"ontology_rid": ontology_rid, "object_type": object_type}
        label = object_type
        if kind == "property":
            object_model = getattr(metadata, "object_type", None)
            properties = getattr(object_model, "properties", {})
            if member not in properties:
                raise DependencyFatalError(
                    "not-found",
                    member or "",
                    "object-type.get-full-metadata",
                    f"Property {member} was not found on {object_type}",
                    False,
                    context.read_context.id,
                )
            identifiers["property"] = member or ""
            label = f"{object_type}.{member}"
        elif kind == "link-type":
            link_kwargs: dict[str, Any] = {
                "ontology": ontology_rid,
                "object_type": object_type,
                "link_type": member,
            }
            if context.read_context.requested_branch is not None:
                link_kwargs["branch"] = context.read_context.requested_branch
            link, link_operation_id = self._invoke_sdk(
                context,
                "object-type.get-outgoing-link-type",
                self.client.ontologies.Ontology.ObjectType.get_outgoing_link_type,
                link_kwargs,
                target=member or "",
                fatal=True,
            )
            context.caches[("link-metadata", ontology_rid, object_type, member)] = (
                link,
                link_operation_id,
            )
            identifiers["link_type"] = member or ""
            label = member or ""
        node = self._add_node(context, kind, label, identifiers, True)
        context.caches[("object-metadata", ontology_rid, object_type)] = (
            metadata,
            operation_id,
        )
        return DependencyTarget(kind, node.identifiers, node.display_name, node.id)

    @staticmethod
    def _coerce_target(target: DependencyTarget | Mapping[str, Any]) -> DependencyTarget:
        if isinstance(target, DependencyTarget):
            return target
        identifiers = target.get("identifiers", {})
        return DependencyTarget(
            str(target["kind"]),
            {str(key): str(value) for key, value in identifiers.items()},
            target.get("display_name"),
            target.get("node_id"),
        )

    @staticmethod
    def _target_label(target: DependencyTarget) -> str:
        return next(reversed(target.identifiers.values()), target.kind)

    def _add_node(
        self,
        context: AnalysisContext,
        kind: str,
        display_name: str,
        identifiers: Mapping[str, str],
        is_target: bool = False,
    ) -> Node:
        normalized = {str(key): str(value) for key, value in sorted(identifiers.items())}
        identity = normalized
        if "resource_rid" in normalized:
            identity = {"resource_rid": normalized["resource_rid"]}
        node_id = _stable_id("node", kind, *[f"{key}={value}" for key, value in identity.items()])
        existing = context.nodes.get(node_id)
        if existing is not None:
            if is_target and not existing.is_target:
                existing = replace(existing, is_target=True)
                context.nodes[node_id] = existing
            return existing
        context.budget.charge("nodes")
        node = Node(
            node_id,
            kind,
            str(display_name),
            normalized,
            context.read_context.id,
            is_target,
        )
        context.nodes[node_id] = node
        return node

    def _add_evidence(
        self,
        context: AnalysisContext,
        operation_id: str,
        locator: str,
        field_path: str,
        raw_value: Any,
        discriminator: Optional[str] = None,
    ) -> Evidence:
        raw_type = type(raw_value).__name__
        discriminator = discriminator or getattr(raw_value, "type", None)
        evidence_id = _stable_id(
            "evidence", operation_id, locator, field_path, raw_type, discriminator
        )
        evidence = Evidence(
            evidence_id,
            operation_id,
            locator,
            field_path,
            raw_type,
            str(discriminator) if discriminator is not None else None,
        )
        context.evidence[evidence_id] = evidence
        return evidence

    def _add_edge(
        self,
        context: AnalysisContext,
        source: str,
        target: str,
        relation_kind: str,
        evidence_ids: Iterable[str],
        coverage: str = "verified",
    ) -> Edge:
        if relation_kind not in RELATION_KINDS:
            raise ValueError(f"unregistered relation kind: {relation_kind}")
        traversal_class, orientation = RELATION_KINDS[relation_kind]
        if orientation == "peer_canonical" and target < source:
            source, target = target, source
        edge_id = _stable_id("edge", source, target, relation_kind, orientation)
        evidence = tuple(sorted(set(evidence_ids)))
        existing = context.edges.get(edge_id)
        if existing is not None:
            if (
                existing.traversal_class != traversal_class
                or existing.intrinsic_orientation != orientation
            ):
                raise ValueError("conflicting intrinsic relation definition")
            evidence = tuple(sorted(set(existing.evidence_ids) | set(evidence)))
        edge = Edge(
            edge_id,
            source,
            target,
            relation_kind,
            traversal_class,
            orientation,
            evidence,
            coverage,
        )
        context.edges[edge_id] = edge
        return edge

    def _coverage_record(
        self,
        context: AnalysisContext,
        target_kind: str,
        surface: str,
        subject_node_id: str,
        *,
        parent_record_id: Optional[str] = None,
        applicability_evidence_id: Optional[str] = None,
    ) -> CoverageRecord:
        record = CoverageRecord(
            target_kind,
            surface,
            subject_node_id,
            context.read_context.id,
            parent_record_id,
            applicability_evidence_id,
        )
        existing = context.coverage_records.get(record.id)
        if existing is not None:
            return existing
        context.coverage_records[record.id] = record
        return record

    def _finish_coverage(
        self,
        record: CoverageRecord,
        status: str,
        *,
        evidence_ids: Iterable[str] = (),
        reason: Optional[str] = None,
        attempted: bool = True,
    ) -> None:
        if status not in {
            "covered",
            "covered-empty",
            "partial",
            "inaccessible",
            "unsupported",
            "unresolved",
            "budget-exhausted",
        }:
            raise ValueError(f"invalid coverage status: {status}")
        record.status = status
        record.attempted = attempted
        record.complete = True
        record.evidence_ids = sorted(set(record.evidence_ids) | set(evidence_ids))
        record.reason = reason

    def _add_gap(
        self,
        context: AnalysisContext,
        subject_node_id: str,
        surface: str,
        coverage: str,
        reason_code: str,
        message: str,
        *,
        retryable: bool = False,
        operation: Optional[str] = None,
        budget_snapshot: Optional[dict[str, Any]] = None,
        locator: Optional[str] = None,
    ) -> CoverageGap:
        gap = CoverageGap(
            surface,
            subject_node_id,
            coverage,
            reason_code,
            message,
            context.read_context.id,
            retryable,
            operation,
            budget_snapshot,
            locator,
        )
        context.gaps[gap.id] = gap
        return gap

    def _initialize_matrix(
        self, context: AnalysisContext, target: DependencyTarget
    ) -> None:
        assert target.node_id is not None
        surfaces = STATIC_SURFACES
        if target.kind == "dataset":
            surfaces = tuple(
                surface for surface in STATIC_SURFACES if surface != "dataset-orchestration"
            )
            self._coverage_record(
                context, target.kind, "schedule-reverse-index", target.node_id
            )
        for surface in surfaces:
            record = self._coverage_record(
                context, target.kind, surface, target.node_id
            )
            reason = MATRIX_GAPS.get(target.kind, {}).get(surface)
            if reason:
                self._finish_coverage(
                    record,
                    "unsupported",
                    reason=reason,
                    attempted=False,
                )
                self._add_gap(
                    context,
                    target.node_id,
                    surface,
                    "unsupported",
                    reason,
                    self._gap_message(reason),
                )
            elif not record.complete:
                self._add_gap(
                    context,
                    target.node_id,
                    surface,
                    "unresolved",
                    "collector-did-not-report",
                    f"The applicable {surface} collector did not report a terminal outcome",
                )

    def _complete_coverage(self, context: AnalysisContext) -> None:
        budget_error = context.caches.get(("budget-exhausted",))
        for record_id, record in sorted(context.coverage_records.items()):
            if record.complete and record.status is not None:
                if record.reason != "collector-did-not-report":
                    self._remove_gaps(
                        context,
                        record.subject_node_id,
                        record.surface,
                        "collector-did-not-report",
                    )
                continue
            status = "budget-exhausted" if budget_error is not None else "unresolved"
            reason = "budget-exhausted" if budget_error is not None else "collector-did-not-report"
            self._finish_coverage(record, status, reason=reason, attempted=record.attempted)
            if reason != "collector-did-not-report":
                self._remove_gaps(
                    context,
                    record.subject_node_id,
                    record.surface,
                    "collector-did-not-report",
                )
            self._add_gap(
                context,
                record.subject_node_id,
                record.surface,
                status,
                reason,
                str(budget_error) if budget_error is not None else f"The applicable {record.surface} collector did not report a terminal outcome",
                retryable=budget_error is not None,
                budget_snapshot=budget_error.snapshot if isinstance(budget_error, BudgetExhausted) else None,
            )

    @staticmethod
    def _remove_gaps(
        context: AnalysisContext,
        subject_node_id: str,
        surface: str,
        reason_code: str,
    ) -> None:
        for gap_id, gap in list(context.gaps.items()):
            if (
                gap.target == subject_node_id
                and gap.surface == surface
                and gap.reason_code == reason_code
            ):
                context.gaps.pop(gap_id, None)

    @staticmethod
    def _has_reported_gap(
        context: AnalysisContext, subject_node_id: str, surface: str
    ) -> bool:
        return any(
            gap.target == subject_node_id
            and gap.surface == surface
            and gap.reason_code != "collector-did-not-report"
            for gap in context.gaps.values()
        )

    def _budget_gap(
        self,
        context: AnalysisContext,
        node_id: str,
        surface: str,
        error: BudgetExhausted,
    ) -> None:
        record = self._coverage_record(
            context, context.nodes.get(node_id, Node(node_id, "transit", node_id, {}, context.read_context.id)).kind, surface, node_id
        )
        self._finish_coverage(
            record, "budget-exhausted", reason="budget-exhausted"
        )
        self._add_gap(
            context,
            node_id,
            surface,
            "budget-exhausted",
            "budget-exhausted",
            str(error),
            retryable=True,
            budget_snapshot=error.snapshot,
        )

    @staticmethod
    def _gap_message(reason: str) -> str:
        return reason.replace("-", " ").capitalize()

    def _collect_target(
        self, target: DependencyTarget, context: AnalysisContext
    ) -> None:
        if target.kind in {"object-type", "property", "link-type"}:
            self._collect_ontology_target(target, context)
        elif target.kind == "action-type":
            self._collect_action_target(target, context)
        elif target.kind == "query-type":
            self._collect_query_target(target, context)
        elif target.kind == "dataset":
            self._collect_dataset(target, context)
        elif target.kind in {
            "third-party-application",
            "workshop-resource",
            "generic-resource",
        }:
            self._collect_resource(target, context)
        else:
            raise DependencyFatalError(
                "unsupported-addressability",
                self._target_label(target),
                "target-resolution",
                f"Unsupported dependency target kind: {target.kind}",
                False,
                context.read_context.id,
            )

    def _collect_resource(
        self, target: DependencyTarget, context: AnalysisContext
    ) -> None:
        assert target.node_id is not None
        record = self._coverage_record(
            context, target.kind, "compass-metadata", target.node_id
        )
        evidence = [
            value.id
            for value in context.evidence.values()
            if value.locator == "resource"
        ]
        if target.kind == "third-party-application":
            application_rid = target.identifiers["resource_rid"]
            try:
                application, operation_id = self._invoke_sdk(
                    context,
                    "third-party-application.get",
                    self.client.third_party_applications.ThirdPartyApplication.get,
                    {"third_party_application_rid": application_rid, "preview": True},
                    target=application_rid,
                )
            except Exception as error:
                self._record_failure(
                    context, record, error, "third-party-application.get"
                )
                return
            application_evidence = self._add_evidence(
                context,
                operation_id,
                "thirdPartyApplication",
                "third_party_application",
                application,
            )
            evidence.append(application_evidence.id)
        self._finish_coverage(record, "covered", evidence_ids=evidence)

    def _get_action_index(
        self, context: AnalysisContext, ontology_rid: str
    ) -> dict[str, Any]:
        key = (
            "action-index",
            context.read_context.profile,
            ontology_rid,
            context.read_context.requested_branch,
        )
        if key in context.caches:
            return context.caches[key]
        entries: list[tuple[Any, str]] = []
        page_token: Optional[str] = None
        incomplete_error: Optional[BaseException] = None
        try:
            while True:
                page_size = context.budget.reserve_page(500)
                kwargs: dict[str, Any] = {
                    "ontology": ontology_rid,
                    "preview": True,
                    "page_size": page_size,
                }
                if context.read_context.requested_branch is not None:
                    kwargs["branch"] = context.read_context.requested_branch
                if page_token is not None:
                    kwargs["page_token"] = page_token
                page, operation_id = self._invoke_sdk(
                    context,
                    "action-type.list-full-metadata",
                    self.client.ontologies.ActionTypeFullMetadata.list,
                    kwargs,
                    target=ontology_rid,
                )
                data = list(getattr(page, "data", []))
                for item in data:
                    context.budget.charge("items")
                    entries.append((item, operation_id))
                page_token = getattr(page, "next_page_token", None)
                if not page_token:
                    break
        except Exception as error:
            if not _is_expected_collection_failure(error):
                raise
            incomplete_error = error
        index = {
            "entries": entries,
            "by_name": {
                str(getattr(item.action_type, "api_name", "")): (item, operation_id)
                for item, operation_id in entries
            },
            "incomplete_error": incomplete_error,
        }
        context.caches[key] = index
        return index

    def _get_query_index(
        self, context: AnalysisContext, ontology_rid: str
    ) -> dict[str, Any]:
        key = (
            "query-index",
            context.read_context.profile,
            ontology_rid,
            context.read_context.requested_branch,
        )
        if key in context.caches:
            return context.caches[key]
        entries: list[tuple[Any, str]] = []
        page_token: Optional[str] = None
        incomplete_error: Optional[BaseException] = None
        try:
            while True:
                page_size = context.budget.reserve_page(100)
                kwargs: dict[str, Any] = {
                    "ontology": ontology_rid,
                    "page_size": page_size,
                }
                if context.read_context.requested_branch is not None:
                    kwargs["branch"] = context.read_context.requested_branch
                if page_token is not None:
                    kwargs["page_token"] = page_token
                page, operation_id = self._invoke_sdk(
                    context,
                    "query-type.list",
                    self.client.ontologies.Ontology.QueryType.list,
                    kwargs,
                    target=ontology_rid,
                )
                data = list(getattr(page, "data", []))
                for item in data:
                    context.budget.charge("items")
                    entries.append((item, operation_id))
                page_token = getattr(page, "next_page_token", None)
                if not page_token:
                    break
        except Exception as error:
            if not _is_expected_collection_failure(error):
                raise
            incomplete_error = error
        index = {
            "entries": entries,
            "by_name": {
                str(getattr(item, "api_name", "")): (item, operation_id)
                for item, operation_id in entries
            },
            "incomplete_error": incomplete_error,
        }
        context.caches[key] = index
        return index

    def _collect_ontology_target(
        self, target: DependencyTarget, context: AnalysisContext
    ) -> None:
        assert target.node_id is not None
        ontology_rid = target.identifiers["ontology_rid"]
        object_type = target.identifiers["object_type"]
        metadata, operation_id = context.caches[
            ("object-metadata", ontology_rid, object_type)
        ]
        structure = self._coverage_record(
            context, target.kind, "ontology-structure-backing", target.node_id
        )
        evidence_ids: list[str] = []
        object_node = self._add_node(
            context,
            "object-type",
            object_type,
            {"ontology_rid": ontology_rid, "object_type": object_type},
            target.kind == "object-type",
        )
        if target.kind == "property":
            evidence = self._add_evidence(
                context,
                operation_id,
                f"objectType.properties.{target.identifiers['property']}",
                f"object_type.properties.{target.identifiers['property']}",
                getattr(metadata.object_type, "properties", {})[
                    target.identifiers["property"]
                ],
            )
            evidence_ids.append(evidence.id)
            self._add_edge(
                context,
                object_node.id,
                target.node_id,
                "container-member",
                [evidence.id],
            )
        elif target.kind == "link-type":
            link, link_operation = context.caches[
                (
                    "link-metadata",
                    ontology_rid,
                    object_type,
                    target.identifiers["link_type"],
                )
            ]
            linked_type = str(getattr(link, "object_type_api_name", ""))
            evidence = self._add_evidence(
                context,
                link_operation,
                "linkType.objectTypeApiName",
                "object_type_api_name",
                link,
            )
            evidence_ids.append(evidence.id)
            if linked_type:
                linked_node = self._add_node(
                    context,
                    "object-type",
                    linked_type,
                    {"ontology_rid": ontology_rid, "object_type": linked_type},
                )
                self._add_edge(
                    context,
                    object_node.id,
                    linked_node.id,
                    "declared-link",
                    [evidence.id],
                )
        else:
            for index, interface_name in enumerate(
                getattr(metadata, "implements_interfaces", []) or []
            ):
                context.budget.charge("items")
                evidence = self._add_evidence(
                    context,
                    operation_id,
                    f"implementsInterfaces[{index}]",
                    f"implements_interfaces[{index}]",
                    interface_name,
                )
                evidence_ids.append(evidence.id)
                interface_node = self._add_node(
                    context,
                    "interface-type",
                    str(interface_name),
                    {
                        "ontology_rid": ontology_rid,
                        "interface_type": str(interface_name),
                    },
                )
                self._add_edge(
                    context,
                    interface_node.id,
                    object_node.id,
                    "container-member",
                    [evidence.id],
                )
            for index, link in enumerate(getattr(metadata, "link_types", []) or []):
                context.budget.charge("items")
                linked_name = str(getattr(link, "object_type_api_name", ""))
                if not linked_name:
                    continue
                evidence = self._add_evidence(
                    context,
                    operation_id,
                    f"linkTypes[{index}].objectTypeApiName",
                    f"link_types[{index}].object_type_api_name",
                    link,
                )
                evidence_ids.append(evidence.id)
                linked_node = self._add_node(
                    context,
                    "object-type",
                    linked_name,
                    {"ontology_rid": ontology_rid, "object_type": linked_name},
                )
                self._add_edge(
                    context,
                    object_node.id,
                    linked_node.id,
                    "declared-link",
                    [evidence.id],
                )
        if target.kind in {"object-type", "property"}:
            evidence_ids.extend(
                self._collect_object_interface_mappings(
                    context,
                    target,
                    object_node,
                    metadata,
                    operation_id,
                    ontology_rid,
                    object_type,
                )
            )
        if target.kind in {"object-type", "property"}:
            reason = (
                "object-backing-dataset-mapping-unavailable"
                if target.kind == "object-type"
                else "dataset-column-lineage-unavailable"
            )
            self._finish_coverage(
                structure, "partial", evidence_ids=evidence_ids, reason=reason
            )
            self._add_gap(
                context,
                target.node_id,
                structure.surface,
                "partial",
                reason,
                self._gap_message(reason),
            )
        else:
            self._finish_coverage(
                structure,
                "covered" if evidence_ids else "covered-empty",
                evidence_ids=evidence_ids,
            )
        self._collect_reverse_actions(target, context, ontology_rid)
        self._collect_reverse_queries(target, context, ontology_rid)

    def _collect_object_interface_mappings(
        self,
        context: AnalysisContext,
        target: DependencyTarget,
        object_node: Node,
        metadata: Any,
        operation_id: str,
        ontology_rid: str,
        object_type: str,
    ) -> list[str]:
        """Collect the richer interface and shared-property metadata surfaces."""

        assert target.node_id is not None
        evidence_ids: list[str] = []
        implementations = getattr(metadata, "implements_interfaces2", None)
        shared_mapping = getattr(metadata, "shared_property_type_mapping", None)
        for field_name, value in (
            ("implementsInterfaces2", implementations),
            ("sharedPropertyTypeMapping", shared_mapping),
        ):
            if not isinstance(value, Mapping):
                self._add_gap(
                    context,
                    target.node_id,
                    "ontology-structure-backing",
                    "unresolved",
                    f"invalid-{field_name}",
                    f"Object metadata field {field_name} is absent or malformed",
                    locator=field_name,
                )

        if isinstance(implementations, Mapping):
            for interface_name, implementation in sorted(
                implementations.items(), key=lambda pair: str(pair[0])
            ):
                context.budget.charge("items")
                locator = f"implementsInterfaces2.{interface_name}"
                evidence = self._add_evidence(
                    context, operation_id, locator, locator, implementation
                )
                evidence_ids.append(evidence.id)
                interface_node = self._add_node(
                    context,
                    "interface-type",
                    str(interface_name),
                    {
                        "ontology_rid": ontology_rid,
                        "interface_type": str(interface_name),
                    },
                )
                self._add_edge(
                    context,
                    interface_node.id,
                    object_node.id,
                    "container-member",
                    [evidence.id],
                )
                properties = getattr(implementation, "properties", None)
                properties_v2 = getattr(implementation, "properties_v2", None)
                links = getattr(implementation, "links", None)
                if not isinstance(properties, Mapping):
                    self._add_gap(
                        context,
                        target.node_id,
                        "ontology-structure-backing",
                        "unresolved",
                        "invalid-interface-property-mapping",
                        f"Interface property mapping is malformed at {locator}.properties",
                        locator=f"{locator}.properties",
                    )
                else:
                    for shared_name, property_name in sorted(
                        properties.items(), key=lambda pair: str(pair[0])
                    ):
                        context.budget.charge("items")
                        mapping_locator = f"{locator}.properties.{shared_name}"
                        mapping_evidence = self._add_evidence(
                            context,
                            operation_id,
                            mapping_locator,
                            mapping_locator,
                            property_name,
                        )
                        evidence_ids.append(mapping_evidence.id)
                        self._add_property_mapping_edge(
                            context,
                            ontology_rid,
                            object_type,
                            str(shared_name),
                            str(property_name),
                            mapping_evidence.id,
                        )
                if not isinstance(properties_v2, Mapping):
                    self._add_gap(
                        context,
                        target.node_id,
                        "ontology-structure-backing",
                        "unresolved",
                        "invalid-interface-property-v2-mapping",
                        f"Interface property V2 mapping is malformed at {locator}.propertiesV2",
                        locator=f"{locator}.propertiesV2",
                    )
                else:
                    for interface_property, property_implementation in sorted(
                        properties_v2.items(), key=lambda pair: str(pair[0])
                    ):
                        context.budget.charge("items")
                        evidence_ids.extend(
                            self._collect_interface_property_v2_implementation(
                                context,
                                target.node_id,
                                operation_id,
                                ontology_rid,
                                object_type,
                                str(interface_name),
                                str(interface_property),
                                property_implementation,
                                f"{locator}.propertiesV2.{interface_property}",
                            )
                        )
                if not isinstance(links, Mapping):
                    self._add_gap(
                        context,
                        target.node_id,
                        "ontology-structure-backing",
                        "unresolved",
                        "invalid-interface-link-mapping",
                        f"Interface link mapping is malformed at {locator}.links",
                        locator=f"{locator}.links",
                    )
                else:
                    for interface_link, local_links in sorted(
                        links.items(), key=lambda pair: str(pair[0])
                    ):
                        context.budget.charge("items")
                        if not isinstance(local_links, Sequence) or isinstance(
                            local_links, (str, bytes)
                        ):
                            self._add_gap(
                                context,
                                target.node_id,
                                "ontology-structure-backing",
                                "unresolved",
                                "invalid-interface-link-mapping",
                                f"Interface link mapping is malformed at {locator}.links.{interface_link}",
                                locator=f"{locator}.links.{interface_link}",
                            )
                            continue
                        interface_link_node = self._add_node(
                            context,
                            "interface-link-type",
                            str(interface_link),
                            {
                                "ontology_rid": ontology_rid,
                                "interface_type": str(interface_name),
                                "interface_link_type": str(interface_link),
                            },
                        )
                        for index, local_link in enumerate(local_links):
                            context.budget.charge("items")
                            link_locator = (
                                f"{locator}.links.{interface_link}[{index}]"
                            )
                            link_evidence = self._add_evidence(
                                context,
                                operation_id,
                                link_locator,
                                link_locator,
                                local_link,
                            )
                            evidence_ids.append(link_evidence.id)
                            local_link_node = self._add_node(
                                context,
                                "link-type",
                                str(local_link),
                                {
                                    "ontology_rid": ontology_rid,
                                    "object_type": object_type,
                                    "link_type": str(local_link),
                                },
                            )
                            self._add_edge(
                                context,
                                interface_link_node.id,
                                local_link_node.id,
                                "container-member",
                                [link_evidence.id],
                            )

        if isinstance(shared_mapping, Mapping):
            for shared_name, property_name in sorted(
                shared_mapping.items(), key=lambda pair: str(pair[0])
            ):
                context.budget.charge("items")
                locator = f"sharedPropertyTypeMapping.{shared_name}"
                evidence = self._add_evidence(
                    context, operation_id, locator, locator, property_name
                )
                evidence_ids.append(evidence.id)
                self._add_property_mapping_edge(
                    context,
                    ontology_rid,
                    object_type,
                    str(shared_name),
                    str(property_name),
                    evidence.id,
                )
        return evidence_ids

    def _collect_interface_property_v2_implementation(
        self,
        context: AnalysisContext,
        target_node_id: str,
        operation_id: str,
        ontology_rid: str,
        object_type: str,
        interface_type: str,
        interface_property: str,
        implementation: Any,
        locator: str,
        *,
        _seen: Optional[set[int]] = None,
    ) -> list[str]:
        """Materialize every supported local property reachable from propertiesV2."""

        seen = _seen if _seen is not None else set()
        identity = id(implementation)
        if identity in seen:
            self._add_gap(
                context,
                target_node_id,
                "ontology-structure-backing",
                "unresolved",
                "cyclic-interface-property-v2-implementation",
                f"Interface property implementation is cyclic at {locator}",
                locator=locator,
            )
            return []
        seen.add(identity)

        def field(name: str) -> Any:
            if isinstance(implementation, Mapping):
                return implementation.get(name)
            return getattr(implementation, name, None)

        discriminator = field("type")
        evidence_ids: list[str] = []

        def emit_property(property_name: Any, evidence_locator: str, raw: Any) -> None:
            if property_name is None or str(property_name) == "":
                self._add_gap(
                    context,
                    target_node_id,
                    "ontology-structure-backing",
                    "unresolved",
                    "invalid-interface-property-v2-implementation",
                    f"Interface property implementation has no backing property at {evidence_locator}",
                    locator=evidence_locator,
                )
                return
            evidence = self._add_evidence(
                context,
                operation_id,
                evidence_locator,
                evidence_locator,
                raw,
                str(discriminator) if discriminator is not None else None,
            )
            evidence_ids.append(evidence.id)
            self._add_interface_property_mapping_edge(
                context,
                ontology_rid,
                object_type,
                interface_type,
                interface_property,
                str(property_name),
                evidence.id,
            )

        if discriminator == "localPropertyImplementation":
            emit_property(field("property_api_name"), f"{locator}.propertyApiName", implementation)
        elif discriminator == "structFieldImplementation":
            struct_field = field("struct_field_of_property")
            property_name = (
                struct_field.get("property_api_name")
                if isinstance(struct_field, Mapping)
                else getattr(struct_field, "property_api_name", None)
            )
            struct_field_name = (
                struct_field.get("struct_field_api_name")
                if isinstance(struct_field, Mapping)
                else getattr(struct_field, "struct_field_api_name", None)
            )
            if not struct_field_name:
                self._add_gap(
                    context,
                    target_node_id,
                    "ontology-structure-backing",
                    "unresolved",
                    "invalid-interface-property-v2-implementation",
                    f"Struct field implementation is malformed at {locator}.structFieldOfProperty",
                    locator=f"{locator}.structFieldOfProperty",
                )
            emit_property(
                property_name,
                f"{locator}.structFieldOfProperty.{struct_field_name or 'unknown'}",
                struct_field,
            )
        elif discriminator == "structImplementation":
            mapping = field("mapping")
            if not isinstance(mapping, Mapping):
                self._add_gap(
                    context,
                    target_node_id,
                    "ontology-structure-backing",
                    "unresolved",
                    "invalid-interface-property-v2-implementation",
                    f"Struct implementation mapping is malformed at {locator}.mapping",
                    locator=f"{locator}.mapping",
                )
            else:
                for interface_field, local_implementation in sorted(
                    mapping.items(), key=lambda pair: str(pair[0])
                ):
                    context.budget.charge("items")
                    local_type = (
                        local_implementation.get("type")
                        if isinstance(local_implementation, Mapping)
                        else getattr(local_implementation, "type", None)
                    )
                    local_property = (
                        local_implementation.get("property_api_name")
                        if isinstance(local_implementation, Mapping)
                        else getattr(local_implementation, "property_api_name", None)
                    )
                    local_field = (
                        local_implementation.get("struct_field_api_name")
                        if isinstance(local_implementation, Mapping)
                        else getattr(local_implementation, "struct_field_api_name", None)
                    )
                    field_locator = f"{locator}.mapping.{interface_field}"
                    if local_type not in {"property", "structFieldOfProperty"}:
                        self._add_gap(
                            context,
                            target_node_id,
                            "ontology-structure-backing",
                            "unresolved",
                            f"unknown-interface-property-struct-mapping:{local_type or 'missing'}",
                            f"Unknown struct mapping implementation at {field_locator}",
                            locator=field_locator,
                        )
                        continue
                    if local_type == "structFieldOfProperty" and not local_field:
                        self._add_gap(
                            context,
                            target_node_id,
                            "ontology-structure-backing",
                            "unresolved",
                            "invalid-interface-property-v2-implementation",
                            f"Struct mapping field is missing at {field_locator}",
                            locator=field_locator,
                        )
                    emit_property(
                        local_property,
                        f"{field_locator}.{local_field or 'property'}",
                        local_implementation,
                    )
        elif discriminator == "reducedPropertyImplementation":
            nested = field("implementation")
            if nested is None:
                self._add_gap(
                    context,
                    target_node_id,
                    "ontology-structure-backing",
                    "unresolved",
                    "invalid-interface-property-v2-implementation",
                    f"Reduced property implementation is missing its implementation at {locator}",
                    locator=f"{locator}.implementation",
                )
            else:
                context.budget.charge("items")
                evidence_ids.extend(
                    self._collect_interface_property_v2_implementation(
                        context,
                        target_node_id,
                        operation_id,
                        ontology_rid,
                        object_type,
                        interface_type,
                        interface_property,
                        nested,
                        f"{locator}.implementation",
                        _seen=seen,
                    )
                )
        else:
            self._add_gap(
                context,
                target_node_id,
                "ontology-structure-backing",
                "unresolved",
                f"unknown-interface-property-v2-implementation:{discriminator or 'missing'}",
                f"Unknown interface property implementation at {locator}",
                locator=locator,
            )
        return evidence_ids

    def _add_interface_property_mapping_edge(
        self,
        context: AnalysisContext,
        ontology_rid: str,
        object_type: str,
        interface_type: str,
        interface_property: str,
        property_name: str,
        evidence_id: str,
    ) -> None:
        interface_property_node = self._add_node(
            context,
            "interface-property-type",
            f"{interface_type}.{interface_property}",
            {
                "ontology_rid": ontology_rid,
                "interface_type": interface_type,
                "interface_property_type": interface_property,
            },
        )
        property_node = self._add_node(
            context,
            "property",
            f"{object_type}.{property_name}",
            {
                "ontology_rid": ontology_rid,
                "object_type": object_type,
                "property": property_name,
            },
        )
        self._add_edge(
            context,
            interface_property_node.id,
            property_node.id,
            "container-member",
            [evidence_id],
        )

    def _add_property_mapping_edge(
        self,
        context: AnalysisContext,
        ontology_rid: str,
        object_type: str,
        shared_name: str,
        property_name: str,
        evidence_id: str,
    ) -> None:
        shared_node = self._add_node(
            context,
            "shared-property-type",
            shared_name,
            {"ontology_rid": ontology_rid, "shared_property_type": shared_name},
        )
        property_node = self._add_node(
            context,
            "property",
            f"{object_type}.{property_name}",
            {
                "ontology_rid": ontology_rid,
                "object_type": object_type,
                "property": property_name,
            },
        )
        self._add_edge(
            context,
            shared_node.id,
            property_node.id,
            "container-member",
            [evidence_id],
        )

    def _collect_reverse_actions(
        self,
        target: DependencyTarget,
        context: AnalysisContext,
        ontology_rid: str,
    ) -> None:
        assert target.node_id is not None
        record = self._coverage_record(
            context, target.kind, "full-action-metadata", target.node_id
        )
        evidence_ids: list[str] = []
        index = self._get_action_index(context, ontology_rid)
        entries = index["entries"]
        has_gaps = False
        for metadata, operation_id in entries:
            action_name = str(getattr(metadata.action_type, "api_name", ""))
            action_node: Optional[Node] = None
            try:
                references = self._extract_action_references(
                    metadata, ontology_rid, context, operation_id
                )
            except BudgetExhausted as error:
                partial_evidence = [
                    reference[3]
                    for reference in error.partial_action_references
                    if self._reference_matches_target(reference, target)
                ]
                self._record_failure(
                    context,
                    record,
                    error,
                    "action-type.list-full-metadata",
                    evidence_ids=[*evidence_ids, *partial_evidence],
                )
                return
            has_gaps = has_gaps or self._has_reported_gap(
                context,
                self._action_node_id(metadata, ontology_rid),
                record.surface,
            )
            for reference in references:
                if not self._reference_matches_target(reference, target):
                    continue
                if action_node is None:
                    action_node = self._add_node(
                        context,
                        "action-type",
                        action_name,
                        {"ontology_rid": ontology_rid, "action_type": action_name},
                    )
                evidence_ids.append(reference[3])
                self._add_edge(
                    context,
                    action_node.id,
                    target.node_id,
                    "action-affects-object",
                    [reference[3]],
                )
        if index.get("incomplete_error") is not None:
            self._record_failure(
                context,
                record,
                index["incomplete_error"],
                "action-type.list-full-metadata",
                evidence_ids=evidence_ids,
            )
            return
        self._finish_coverage(
            record,
            "partial" if has_gaps else ("covered" if evidence_ids else "covered-empty"),
            evidence_ids=evidence_ids,
            reason="action-metadata-shape-gap" if has_gaps else None,
        )

    def _collect_reverse_queries(
        self,
        target: DependencyTarget,
        context: AnalysisContext,
        ontology_rid: str,
    ) -> None:
        assert target.node_id is not None
        record = self._coverage_record(
            context,
            target.kind,
            "query-related-function-metadata",
            target.node_id,
        )
        evidence_ids: list[str] = []
        index = self._get_query_index(context, ontology_rid)
        entries = index["entries"]
        has_gaps = False
        for query, operation_id in entries:
            roots = {
                f"parameters.{name}.dataType": parameter.data_type
                for name, parameter in sorted(query.parameters.items())
            }
            roots["output"] = query.output
            try:
                closure = self._build_query_reference_closure(query, roots, context)
            except BudgetExhausted as error:
                for kind, name, locator in error.partial_query_leaves:
                    if not self._reference_matches_target(
                        (kind, name, locator, ""), target
                    ):
                        continue
                    evidence = self._add_evidence(
                        context, operation_id, locator, locator, name
                    )
                    evidence_ids.append(evidence.id)
                self._record_failure(
                    context,
                    record,
                    error,
                    "query-metadata-walk",
                    evidence_ids=evidence_ids,
                )
                return
            for root_name, result in sorted(closure.items()):
                for gap in result["gaps"]:
                    has_gaps = True
                    self._add_gap(
                        context,
                        target.node_id,
                        record.surface,
                        "unsupported"
                        if gap["reason_code"] == "unsupported-query-data-type"
                        else "unresolved",
                        gap["reason_code"],
                        f"{gap['message']} at {gap['locator']}",
                        locator=gap["locator"],
                    )
                for leaf in result["leaves"]:
                    reference = (leaf["kind"], leaf["name"], leaf["locator"], "")
                    if not self._reference_matches_target(reference, target):
                        continue
                    evidence = self._add_evidence(
                        context,
                        operation_id,
                        leaf["locator"],
                        leaf["locator"],
                        leaf["name"],
                    )
                    evidence_ids.append(evidence.id)
                    query_node = self._add_node(
                        context,
                        "query-type",
                        str(query.api_name),
                        {
                            "ontology_rid": ontology_rid,
                            "query_type": str(query.api_name),
                        },
                    )
                    relation = (
                        "query-returns-object"
                        if root_name == "output"
                        else "query-accepts-object"
                    )
                    source, edge_target = (
                        (query_node.id, target.node_id)
                        if root_name == "output"
                        else (target.node_id, query_node.id)
                    )
                    self._add_edge(context, source, edge_target, relation, [evidence.id])
        if index.get("incomplete_error") is not None:
            self._record_failure(
                context,
                record,
                index["incomplete_error"],
                "query-type.list",
                evidence_ids=evidence_ids,
            )
            return
        self._finish_coverage(
            record,
            "partial" if has_gaps else ("covered" if evidence_ids else "covered-empty"),
            evidence_ids=evidence_ids,
            reason="query-metadata-shape-gap" if has_gaps else None,
        )

    def _collect_action_target(
        self, target: DependencyTarget, context: AnalysisContext
    ) -> None:
        assert target.node_id is not None
        ontology_rid = target.identifiers["ontology_rid"]
        metadata, operation_id = context.caches[
            (
                "action-metadata",
                ontology_rid,
                context.read_context.requested_branch,
                target.identifiers["action_type"],
            )
        ]
        evidence_ids: list[str] = []
        structure_record = self._coverage_record(
            context, target.kind, "ontology-structure-backing", target.node_id
        )
        action_record = self._coverage_record(
            context, target.kind, "full-action-metadata", target.node_id
        )
        try:
            references = self._extract_action_references(
                metadata, ontology_rid, context, operation_id
            )
        except BudgetExhausted as error:
            partial_evidence = [
                reference[3] for reference in error.partial_action_references
            ]
            self._finish_coverage(
                structure_record,
                "partial" if partial_evidence else "covered-empty",
                evidence_ids=partial_evidence,
                reason="action-metadata-walk-incomplete",
            )
            self._record_failure(
                context,
                action_record,
                error,
                "action-metadata-walk",
                evidence_ids=partial_evidence,
            )
            return
        global_owners = sorted({name for kind, name, _, _ in references if kind == "object-type"})
        for kind, name, locator, evidence_id in references:
            evidence_ids.append(evidence_id)
            identifiers = {"ontology_rid": ontology_rid, kind.replace("-", "_"): name}
            if kind == "function":
                identifiers = {"function_rid": name}
            related_kind = kind
            if kind in {"property", "link-type"}:
                prefix = locator.split(".", 1)[0]
                local_owners = sorted({owner for owner_kind, owner, owner_locator, _ in references if owner_kind == "object-type" and owner_locator.split(".", 1)[0] == prefix})
                owners = local_owners or global_owners
                if len(owners) == 1:
                    identifiers["object_type"] = owners[0]
                else:
                    related_kind = f"unresolved-{kind}-reference"
                    self._add_gap(context, target.node_id, "full-action-metadata", "unresolved", f"ambiguous-{kind}-owner", f"Cannot derive one concrete owning object type for {kind} {name} at {locator}")
            related = self._add_node(context, related_kind, name, identifiers)
            relation = "action-uses-function" if kind == "function" else "action-affects-object"
            self._add_edge(
                context, target.node_id, related.id, relation, [evidence_id]
            )
        for surface, record in (
            ("ontology-structure-backing", structure_record),
            ("full-action-metadata", action_record),
        ):
            has_gaps = self._has_reported_gap(context, target.node_id, surface)
            self._finish_coverage(
                record,
                "partial" if has_gaps else ("covered" if evidence_ids else "covered-empty"),
                evidence_ids=evidence_ids,
                reason="action-metadata-shape-gap" if has_gaps else None,
            )
        query_record = self._coverage_record(
            context,
            target.kind,
            "query-related-function-metadata",
            target.node_id,
        )
        function_evidence = [
            reference[3] for reference in references if reference[0] == "function"
        ]
        if function_evidence:
            self._finish_coverage(
                query_record,
                "partial",
                evidence_ids=function_evidence,
                reason="function-internals-unavailable",
            )
            self._add_gap(
                context,
                target.node_id,
                query_record.surface,
                "partial",
                "function-internals-unavailable",
                "Function RIDs are observable but function bodies and reverse consumers are unavailable",
            )
        else:
            self._finish_coverage(query_record, "covered-empty")

    def _extract_action_references(
        self,
        metadata: Any,
        ontology_rid: str,
        context: AnalysisContext,
        operation_id: str,
    ) -> list[tuple[str, str, str, str]]:
        references: list[tuple[str, str, str, str]] = []
        parameters = getattr(metadata.action_type, "parameters", {}) or {}

        def emit(kind: str, name: Any, locator: str, raw: Any) -> None:
            if name is None or str(name) == "":
                return
            evidence = self._add_evidence(
                context, operation_id, locator, locator, raw
            )
            references.append((kind, str(name), locator, evidence.id))

        work: list[tuple[Any, str, int]] = [
            (parameter.data_type, f"parameters.{parameter_id}.dataType", 0)
            for parameter_id, parameter in reversed(sorted(parameters.items()))
        ]
        # Full logic rules are the dependency contract.  Shallow operations are
        # intentionally neither traversed nor position-correlated.
        for index, rule in enumerate(getattr(metadata, "full_logic_rules", []) or []):
            discriminator = (
                rule.get("type") if isinstance(rule, Mapping) else getattr(rule, "type", None)
            )
            if discriminator not in ACTION_LOGIC_RULE_TYPES:
                self._add_gap(
                    context,
                    self._action_node_id(metadata, ontology_rid),
                    "full-action-metadata",
                    "unresolved",
                    f"unknown-action-logic-rule:{discriminator or 'missing'}",
                    f"Unknown action logic rule at fullLogicRules[{index}]",
                    locator=f"fullLogicRules[{index}]",
                )
            work.append((rule, f"fullLogicRules[{index}]", 0))

        parameter_fields = {
            "parameter_id",
            "scenario_parameter",
            "object_to_modify",
            "object_to_delete",
            "interface_object_to_modify",
            "source_object",
            "target_object",
        }
        try:
            while work:
                value, locator, depth = work.pop()
                context.budget.check_metadata_depth(depth)
                context.budget.charge("items")
                if value is None or isinstance(value, (str, bool, int, float)):
                    continue
                if isinstance(value, Mapping):
                    children = sorted(value.items(), key=lambda pair: str(pair[0]))
                    for key, item in reversed(children):
                        path = f"{locator}.{key}"
                        if locator.endswith(("propertyArguments", "sharedPropertyArguments")):
                            emit("property", key, path, item)
                        work.append((item, path, depth + 1))
                    continue
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                    for index in range(len(value) - 1, -1, -1):
                        work.append((value[index], f"{locator}[{index}]", depth + 1))
                    continue
                fields = self._declared_fields(value)
                discriminator = getattr(value, "type", None)
                for name, item in reversed(fields):
                    path = f"{locator}.{self._field_alias(value, name)}"
                    if name in {"object_type_api_name", "object_api_name", "a_side_object_type_api_name", "b_side_object_type_api_name"}:
                        emit("object-type", item, path, value)
                    elif name == "interface_type_api_name":
                        emit("interface-type", item, path, value)
                    elif "link_type_api_name" in name:
                        emit("link-type", item, path, value)
                    elif name == "function_rid":
                        emit("function", item, path, value)
                    elif name == "property_type_api_name":
                        emit("property", item, path, value)
                    elif name == "shared_property_type_rid":
                        emit("shared-property-type", item, path, value)
                    elif name == "object_type_api_names" and isinstance(item, Sequence):
                        for item_index, object_name in enumerate(item):
                            emit("object-type", object_name, f"{path}[{item_index}]", value)
                    elif name == "link_types" and isinstance(item, Sequence):
                        for item_index in range(len(item) - 1, -1, -1):
                            link_name = item[item_index]
                            if isinstance(link_name, str):
                                emit("link-type", link_name, f"{path}[{item_index}]", value)
                            else:
                                work.append((link_name, f"{path}[{item_index}]", depth + 1))
                    elif name == "object_type" and isinstance(item, str):
                        if type(value).__name__ == "CreateInterfaceLogicRule":
                            parameter = parameters.get(item)
                            if parameter is None:
                                self._add_gap(context, self._action_node_id(metadata, ontology_rid), "full-action-metadata", "unresolved", "unresolved-action-parameter", f"Rule references missing parameter {item} at {path}", locator=path)
                            else:
                                work.append((parameter.data_type, f"{path}->parameters.{item}.dataType", depth + 1))
                        else:
                            emit("object-type", item, path, value)
                    elif name in parameter_fields and isinstance(item, str):
                        parameter = parameters.get(item)
                        if parameter is None:
                            self._add_gap(context, self._action_node_id(metadata, ontology_rid), "full-action-metadata", "unresolved", "unresolved-action-parameter", f"Rule references missing parameter {item} at {path}", locator=path)
                        else:
                            work.append((parameter.data_type, f"{path}->parameters.{item}.dataType", depth + 1))
                    elif name != "type":
                        work.append((item, path, depth + 1))
                if discriminator == "objectType" and not any(name in {"object_type_api_name", "object_api_name"} for name, _ in fields):
                    self._add_gap(context, self._action_node_id(metadata, ontology_rid), "full-action-metadata", "unresolved", "unresolved-action-parameter-type", f"Generic objectType parameter at {locator} does not identify a concrete type", locator=locator)
        except BudgetExhausted as error:
            unique = {reference[:3]: reference for reference in references}
            error.partial_action_references = [
                unique[key] for key in sorted(unique)
            ]
            raise
        unique = {reference[:3]: reference for reference in references}
        return [unique[key] for key in sorted(unique)]

    @staticmethod
    def _action_node_id(metadata: Any, ontology_rid: str) -> str:
        return _stable_id(
            "node",
            "action-type",
            f"action_type={metadata.action_type.api_name}",
            f"ontology_rid={ontology_rid}",
        )

    @staticmethod
    def _reference_matches_target(
        reference: tuple[str, str, str, str], target: DependencyTarget
    ) -> bool:
        kind, name = reference[0], reference[1]
        if target.kind == "object-type":
            return kind == "object-type" and name == target.identifiers["object_type"]
        if target.kind == "property":
            return kind == "property" and name == target.identifiers["property"]
        if target.kind == "link-type":
            return kind == "link-type" and name == target.identifiers["link_type"]
        return False

    def _collect_query_target(
        self, target: DependencyTarget, context: AnalysisContext
    ) -> None:
        assert target.node_id is not None
        ontology_rid = target.identifiers["ontology_rid"]
        query, operation_id = context.caches[
            (
                "query-metadata",
                ontology_rid,
                context.read_context.requested_branch,
                target.identifiers["query_type"],
            )
        ]
        roots = {
            f"parameters.{name}.dataType": parameter.data_type
            for name, parameter in sorted(query.parameters.items())
        }
        roots["output"] = query.output
        structure_record = self._coverage_record(
            context, target.kind, "ontology-structure-backing", target.node_id
        )
        query_record = self._coverage_record(
            context,
            target.kind,
            "query-related-function-metadata",
            target.node_id,
        )
        try:
            closure = self._build_query_reference_closure(query, roots, context)
        except BudgetExhausted as error:
            partial_evidence = [
                self._add_evidence(
                    context, operation_id, locator, locator, name
                ).id
                for _, name, locator in getattr(error, "partial_query_leaves", ())
            ]
            self._finish_coverage(
                structure_record,
                "partial" if partial_evidence else "covered-empty",
                evidence_ids=partial_evidence,
                reason="query-metadata-walk-incomplete",
            )
            self._record_failure(
                context,
                query_record,
                error,
                "query-metadata-walk",
                evidence_ids=partial_evidence,
            )
            return
        evidence_ids: list[str] = []
        has_gap = False
        for root_name, result in sorted(closure.items()):
            for leaf in result["leaves"]:
                evidence = self._add_evidence(
                    context,
                    operation_id,
                    leaf["locator"],
                    leaf["locator"],
                    leaf["name"],
                )
                evidence_ids.append(evidence.id)
                identifiers = {
                    "ontology_rid": ontology_rid,
                    leaf["kind"].replace("-", "_"): leaf["name"],
                }
                related = self._add_node(
                    context, leaf["kind"], leaf["name"], identifiers
                )
                relation = (
                    "query-returns-object"
                    if root_name == "output"
                    else "query-accepts-object"
                )
                source, edge_target = (
                    (target.node_id, related.id)
                    if root_name == "output"
                    else (related.id, target.node_id)
                )
                self._add_edge(context, source, edge_target, relation, [evidence.id])
            for gap in result["gaps"]:
                has_gap = True
                coverage = "unsupported" if gap["reason_code"] == "unsupported-query-data-type" else "unresolved"
                self._add_gap(
                    context,
                    target.node_id,
                    "query-related-function-metadata",
                    coverage,
                    gap["reason_code"],
                    f"{gap['message']} at {gap['locator']}",
                    locator=gap["locator"],
                )
        for surface, record in (
            ("ontology-structure-backing", structure_record),
            ("query-related-function-metadata", query_record),
        ):
            self._finish_coverage(
                record,
                "partial"
                if has_gap
                else ("covered" if evidence_ids else "covered-empty"),
                evidence_ids=evidence_ids,
                reason="query-type-reference-gap" if has_gap else None,
            )
        self._collect_reverse_actions(target, context, ontology_rid)

    def _build_query_reference_closure(
        self, query: Any, roots: Mapping[str, Any], context: AnalysisContext
    ) -> dict[str, dict[str, Any]]:
        """Return a complete root-specific closure over recursive type references.

        Reachable definitions are scanned lazily, SCCs are found iteratively,
        and root locators are rebuilt from cached direct scans with a bounded
        witness walk.  Unreachable definitions are never inspected.
        """
        definitions = getattr(query, "type_references", {}) or {}
        reference_graph: dict[str, set[str]] = defaultdict(set)
        direct_leaves: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        direct_gaps: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        direct_references: dict[str, set[tuple[str, str]]] = defaultdict(set)

        def inspect(
            value: Any, locator: str
        ) -> tuple[
            set[tuple[str, str, str]],
            set[tuple[str, str, str]],
            set[tuple[str, str]],
        ]:
            leaves: set[tuple[str, str, str]] = set()
            gaps: set[tuple[str, str, str]] = set()
            references: set[tuple[str, str]] = set()
            work: list[tuple[Any, str, int]] = [(value, locator, 0)]
            try:
                while work:
                    current, current_locator, depth = work.pop()
                    context.budget.check_metadata_depth(depth)
                    context.budget.charge("items")
                    discriminator = getattr(current, "type", None)
                    if discriminator == "typeReference":
                        reference_id = getattr(current, "type_id", None)
                        if reference_id is None:
                            gaps.add(("invalid-response", current_locator, "Missing query type reference ID"))
                        else:
                            references.add((str(reference_id), current_locator))
                        continue
                    if discriminator in {"object", "objectSet"}:
                        name = getattr(current, "object_type_api_name", None) or getattr(current, "object_api_name", None)
                        if name:
                            leaves.add(("object-type", str(name), current_locator))
                        else:
                            gaps.add(("invalid-response", current_locator, "Object query type has no concrete API name"))
                        continue
                    if discriminator in {"interfaceObject", "interfaceObjectSet"}:
                        name = getattr(current, "interface_type_api_name", None)
                        if name:
                            leaves.add(("interface-type", str(name), current_locator))
                        else:
                            gaps.add(("invalid-response", current_locator, "Interface query type has no concrete API name"))
                        continue
                    if discriminator == "unsupported" or type(current).__name__ == "UnsupportedType":
                        gaps.add(("unsupported-query-data-type", current_locator, "Reachable query data type is unsupported by the SDK"))
                        continue
                    children: list[tuple[str, Any]] = []
                    if discriminator in {"array", "set"}:
                        children.append(("subType", getattr(current, "sub_type", None)))
                    elif discriminator == "union":
                        children.extend((f"unionTypes[{index}]", item) for index, item in enumerate(getattr(current, "union_types", []) or []))
                    elif discriminator == "struct":
                        children.extend((f"fields[{index}].fieldType", getattr(item, "field_type", None)) for index, item in enumerate(getattr(current, "fields", []) or []))
                    elif discriminator in {"entrySet", "twoDimensionalAggregation", "threeDimensionalAggregation"}:
                        children.extend((("keyType", getattr(current, "key_type", None)), ("valueType", getattr(current, "value_type", None))))
                    elif discriminator not in QUERY_DATA_TYPE_TYPES:
                        gaps.add(("invalid-response", current_locator, f"Unknown reachable query data type variant: {discriminator or type(current).__name__}"))
                    for child_name, child in reversed(children):
                        child_locator = f"{current_locator}.{child_name}"
                        if child is None:
                            gaps.add(("invalid-response", child_locator, "Missing query data type child"))
                        else:
                            work.append((child, child_locator, depth + 1))
            except BudgetExhausted as error:
                partial = list(getattr(error, "partial_query_leaves", ()))
                partial.extend(sorted(leaves))
                error.partial_query_leaves = partial
                raise
            return leaves, gaps, references

        root_scans: dict[str, tuple[set[Any], set[Any], set[Any]]] = {}
        for name, value in sorted(roots.items()):
            try:
                root_scans[name] = inspect(value, name)
            except BudgetExhausted as error:
                prior_leaves = {
                    leaf
                    for root_leaves, _, _ in root_scans.values()
                    for leaf in root_leaves
                }
                error.partial_query_leaves.extend(
                    leaf
                    for leaf in sorted(prior_leaves)
                    if leaf not in error.partial_query_leaves
                )
                raise

        def preserve_partial(error: BudgetExhausted) -> None:
            leaves = {
                leaf
                for root_leaves, _, _ in root_scans.values()
                for leaf in root_leaves
            }
            for definition_leaves in direct_leaves.values():
                leaves.update(definition_leaves)
            error.partial_query_leaves.extend(
                leaf for leaf in sorted(leaves) if leaf not in error.partial_query_leaves
            )

        reachable: set[str] = set()
        frontier: deque[tuple[str, int]] = deque((reference_id, 0) for reference_id in sorted({reference_id for _, _, references in root_scans.values() for reference_id, _ in references}))
        while frontier:
            reference_id, depth = frontier.popleft()
            try:
                context.budget.check_metadata_depth(depth)
                context.budget.charge("items")
            except BudgetExhausted as error:
                preserve_partial(error)
                raise
            if reference_id in reachable:
                continue
            reachable.add(reference_id)
            if reference_id not in definitions:
                direct_gaps[reference_id].add(
                    ("invalid-response", f"typeReferences.{reference_id}", f"Missing reachable type reference {reference_id}")
                )
                continue
            base = f"typeReferences.{reference_id}"
            try:
                leaves, gaps, references = inspect(definitions[reference_id], base)
            except BudgetExhausted as error:
                preserve_partial(error)
                raise
            direct_leaves[reference_id].update(leaves)
            direct_gaps[reference_id].update(gaps)
            direct_references[reference_id].update(references)
            reference_graph[reference_id].update(child for child, _ in references)
            for child in sorted(reference_graph[reference_id]):
                if child not in reachable:
                    frontier.append((child, depth + 1))

        reachable_graph: dict[str, set[str]] = {
            node: set(reference_graph.get(node, ())).intersection(reachable)
            for node in reachable
        }
        try:
            components = self._tarjan_scc(reachable_graph, context)
        except BudgetExhausted as error:
            preserve_partial(error)
            raise

        results: dict[str, dict[str, Any]] = {}
        for root_name, (root_leaves, root_gaps, root_references) in root_scans.items():
            leaves = set(root_leaves)
            gaps = set(root_gaps)
            queue: deque[tuple[str, str]] = deque(
                sorted(root_references, key=lambda value: (value[1], value[0]))
            )
            visited: set[str] = set()
            while queue:
                reference_id, witness = queue.popleft()
                try:
                    context.budget.charge("items")
                except BudgetExhausted as error:
                    preserve_partial(error)
                    raise
                if reference_id in visited:
                    continue
                visited.add(reference_id)
                if reference_id not in definitions:
                    gaps.add(("invalid-response", witness, f"Missing reachable type reference {reference_id}"))
                    continue
                base = f"typeReferences.{reference_id}"
                for kind, name, locator in direct_leaves[reference_id]:
                    suffix = locator[len(base) :]
                    leaves.add((kind, name, f"{witness}->{base}{suffix}"))
                for reason, locator, message in direct_gaps[reference_id]:
                    suffix = locator[len(base) :] if locator.startswith(base) else ""
                    gaps.add((reason, f"{witness}->{base}{suffix}", message))
                for child, child_locator in sorted(direct_references[reference_id]):
                    suffix = child_locator[len(base) :]
                    queue.append((child, f"{witness}->{base}{suffix}"))
            results[root_name] = {
                "leaves": [
                    {"kind": kind, "name": name, "locator": locator}
                    for kind, name, locator in sorted(leaves)
                ],
                "gaps": [
                    {"reason_code": reason, "locator": locator, "message": message}
                    for reason, locator, message in sorted(gaps)
                ],
                "sccs": [list(component) for component in components],
            }
        return results

    @staticmethod
    def _tarjan_scc(
        graph: Mapping[str, set[str]], context: Optional[AnalysisContext] = None
    ) -> list[tuple[str, ...]]:
        visited: set[str] = set()
        order: list[str] = []
        for start in sorted(graph):
            if start in visited:
                continue
            work: list[tuple[str, bool]] = [(start, False)]
            while work:
                if context is not None:
                    context.budget.charge("items")
                node, expanded = work.pop()
                if expanded:
                    order.append(node)
                    continue
                if node in visited:
                    continue
                visited.add(node)
                work.append((node, True))
                for child in reversed(sorted(graph.get(node, ()))):
                    if child in graph and child not in visited:
                        work.append((child, False))
        reverse_graph: dict[str, set[str]] = {node: set() for node in graph}
        for node, children in graph.items():
            for child in children:
                if child in reverse_graph:
                    reverse_graph[child].add(node)
        components: list[tuple[str, ...]] = []
        assigned: set[str] = set()
        for start in reversed(order):
            if start in assigned:
                continue
            component: list[str] = []
            work = [(start, False)]
            assigned.add(start)
            while work:
                if context is not None:
                    context.budget.charge("items")
                node, _ = work.pop()
                component.append(node)
                for parent in reversed(sorted(reverse_graph[node])):
                    if parent not in assigned:
                        assigned.add(parent)
                        work.append((parent, False))
            components.append(tuple(sorted(component)))
        return sorted(components, key=lambda component: component[0])

    def _collect_dataset(
        self, target: DependencyTarget, context: AnalysisContext
    ) -> None:
        assert target.node_id is not None
        dataset_rid = target.identifiers["resource_rid"]
        dataset_service = DatasetService(self.profile)
        dataset_service._client = self.client
        orchestration = OrchestrationService(self.profile)
        orchestration._client = self.client
        record = self._coverage_record(
            context, "dataset", "schedule-reverse-index", target.node_id
        )
        compass = self._coverage_record(context, target.kind, "compass-metadata", target.node_id)
        resource_evidence = [evidence.id for evidence in context.evidence.values() if evidence.locator == "resource"]
        self._finish_coverage(compass, "covered", evidence_ids=resource_evidence)
        page_token: Optional[str] = None
        reverse_evidence: list[str] = []
        schedule_work: list[tuple[Node, Mapping[str, CoverageRecord]]] = []
        staleness = {
            "reason_code": "schedule-index-may-be-stale",
            "message": "Results may lag recent schedule changes by up to one hour",
            "max_lag_seconds": 3600,
        }
        try:
            while True:
                page_size = context.budget.reserve_page(100)
                kwargs: dict[str, Any] = {
                    "dataset_rid": dataset_rid,
                    "page_size": page_size,
                }
                branch = context.read_context.dataset_branch or context.read_context.requested_branch
                if branch is not None:
                    kwargs["branch_name"] = branch
                if page_token is not None:
                    kwargs["page_token"] = page_token
                page, operation_id = self._invoke_sdk(
                    context,
                    "dataset.get-schedules",
                    dataset_service.get_schedule_rids_page,
                    kwargs,
                    target=dataset_rid,
                    known_limitations=(staleness,),
                )
                schedule_rids = list(page.get("schedule_rids", []))
                for index, schedule_rid in enumerate(schedule_rids):
                    context.budget.charge("items")
                    evidence = self._add_evidence(
                        context,
                        operation_id,
                        f"scheduleRids[{index}]",
                        f"schedule_rids[{index}]",
                        schedule_rid,
                    )
                    reverse_evidence.append(evidence.id)
                    schedule_node = self._add_node(
                        context,
                        "schedule",
                        str(schedule_rid),
                        {"schedule_rid": str(schedule_rid)},
                    )
                    self._add_edge(
                        context,
                        schedule_node.id,
                        target.node_id,
                        "schedule-produces-resource",
                        [evidence.id],
                    )
                    child_records = {
                        surface: self._coverage_record(
                            context,
                            "schedule",
                            surface,
                            schedule_node.id,
                            parent_record_id=record.id,
                            applicability_evidence_id=evidence.id,
                        )
                        for surface in (
                            "schedule-detail-action",
                            "schedule-trigger",
                            "schedule-affected-resources",
                            "schedule-runs",
                        )
                    }
                    schedule_work.append((schedule_node, child_records))
                page_token = page.get("next_page_token")
                if not page_token:
                    break
        except Exception as error:
            self._record_failure(
                context,
                record,
                error,
                "dataset.get-schedules",
                evidence_ids=reverse_evidence,
            )
            return
        self._finish_coverage(
            record,
            "partial",
            evidence_ids=reverse_evidence,
            reason="schedule-index-may-be-stale",
        )
        self._add_gap(
            context,
            target.node_id,
            record.surface,
            "partial",
            "schedule-index-may-be-stale",
            "Returned schedule RIDs are verified, but results may lag recent schedule changes by up to one hour",
            retryable=True,
            operation="dataset.get-schedules",
        )
        for schedule_node, child_records in schedule_work:
            self._collect_schedule(
                context,
                target,
                schedule_node,
                orchestration,
                child_records,
            )

    def _collect_schedule(
        self,
        context: AnalysisContext,
        root: DependencyTarget,
        schedule_node: Node,
        orchestration: OrchestrationService,
        records: Mapping[str, CoverageRecord],
    ) -> None:
        try:
            self._collect_schedule_impl(
                context, root, schedule_node, orchestration, records
            )
        except BudgetExhausted as error:
            record = next(
                (
                    records[surface]
                    for surface in (
                        "schedule-detail-action",
                        "schedule-trigger",
                        "schedule-affected-resources",
                        "schedule-runs",
                    )
                    if not records[surface].complete
                ),
                records["schedule-runs"],
            )
            prefixes = {
                "schedule-detail-action": ("action.", "scope_mode."),
                "schedule-trigger": ("trigger",),
                "schedule-affected-resources": ("affectedResources[",),
                "schedule-runs": ("runs[",),
            }
            subject_evidence = {
                evidence_id
                for edge in context.edges.values()
                if schedule_node.id in {edge.source, edge.target}
                for evidence_id in edge.evidence_ids
            }
            partial_evidence = [
                evidence.id
                for evidence in context.evidence.values()
                if evidence.id in subject_evidence
                and evidence.locator.startswith(prefixes.get(record.surface, ()))
            ]
            self._record_failure(
                context,
                record,
                error,
                "schedule-metadata-walk",
                evidence_ids=[*record.evidence_ids, *partial_evidence],
            )

    def _collect_schedule_impl(
        self,
        context: AnalysisContext,
        root: DependencyTarget,
        schedule_node: Node,
        orchestration: OrchestrationService,
        records: Mapping[str, CoverageRecord],
    ) -> None:
        schedule_rid = schedule_node.identifiers["schedule_rid"]
        detail_evidence: list[str] = []
        try:
            detail, operation_id = self._invoke_sdk(
                context,
                "schedule.get",
                orchestration.get_schedule,
                {"schedule_rid": schedule_rid, "preview": True},
                target=schedule_rid,
            )
        except Exception as error:
            self._record_failure(
                context, records["schedule-detail-action"], error, "schedule.get"
            )
            self._record_failure(
                context, records["schedule-trigger"], error, "schedule.get"
            )
        else:
            action = detail.get("action") or {}
            target_value = action.get("target") or {}
            target_type = target_value.get("type")
            detail_status = "covered"
            detail_reason: Optional[str] = None
            if not action or not target_value or target_type not in {
                "manual",
                "upstream",
                "connecting",
            }:
                detail_status = "unresolved"
                detail_reason = "invalid-schedule-action-target"
                self._add_gap(
                    context,
                    schedule_node.id,
                    "schedule-detail-action",
                    "unresolved",
                    "invalid-schedule-action-target",
                    "Schedule.action.target is missing, malformed, or has an unknown discriminator",
                )
            for field_name, relation, source_is_resource in (
                ("input_rids", "schedule-consumes-resource", True),
                ("target_rids", "schedule-produces-resource", False),
            ):
                for index, resource_rid in enumerate(target_value.get(field_name, []) or []):
                    context.budget.charge("items")
                    locator = f"action.target.{field_name}[{index}]"
                    evidence = self._add_evidence(
                        context, operation_id, locator, locator, resource_rid, target_type
                    )
                    detail_evidence.append(evidence.id)
                    resource_node = self._add_node(
                        context,
                        self._kind_from_rid(str(resource_rid)),
                        str(resource_rid),
                        {"resource_rid": str(resource_rid)},
                    )
                    source, edge_target = (
                        (resource_node.id, schedule_node.id)
                        if source_is_resource
                        else (schedule_node.id, resource_node.id)
                    )
                    self._add_edge(
                        context, source, edge_target, relation, [evidence.id]
                    )
            if target_type == "upstream":
                if detail_status != "unresolved":
                    detail_status = "partial"
                    detail_reason = "dataset-resolved-upstream-lineage"
                self._add_gap(
                    context,
                    schedule_node.id,
                    "schedule-detail-action",
                    "partial",
                    "dataset-resolved-upstream-lineage",
                    "The configured upstream target does not enumerate its dynamically resolved upstream closure",
                )
            scope = detail.get("scope_mode")
            if scope:
                scope_type = scope.get("type")
                if scope_type == "project":
                    for index, project_rid in enumerate(scope.get("project_rids", []) or []):
                        context.budget.charge("items")
                        locator = f"scope_mode.project_rids[{index}]"
                        evidence = self._add_evidence(
                            context, operation_id, locator, locator, project_rid, scope_type
                        )
                        detail_evidence.append(evidence.id)
                        project = self._add_node(
                            context,
                            "project",
                            str(project_rid),
                            {"project_rid": str(project_rid)},
                        )
                        self._add_edge(
                            context,
                            project.id,
                            schedule_node.id,
                            "project-scope",
                            [evidence.id],
                        )
                elif scope_type != "user":
                    detail_status = "unresolved"
                    detail_reason = "unknown-schedule-scope-variant"
                    self._add_gap(
                        context,
                        schedule_node.id,
                        "schedule-detail-action",
                        "unresolved",
                        f"unknown-schedule-scope-variant:{scope_type or 'missing'}",
                        "Schedule scope has an unknown or malformed discriminator",
                    )
            else:
                detail_status = "unresolved"
                detail_reason = "unknown-schedule-scope-variant"
                self._add_gap(
                    context,
                    schedule_node.id,
                    "schedule-detail-action",
                    "unresolved",
                    "unknown-schedule-scope-variant:missing",
                    "Schedule scope is missing or malformed at scope_mode",
                )
            self._finish_coverage(
                records["schedule-detail-action"],
                detail_status
                if detail_status != "covered"
                else ("covered" if detail_evidence else "covered-empty"),
                evidence_ids=detail_evidence,
                reason=detail_reason,
            )
            trigger_evidence, trigger_gap = self._collect_trigger(
                context,
                schedule_node,
                operation_id,
                detail.get("trigger"),
                "trigger",
            )
            if trigger_gap:
                self._finish_coverage(
                    records["schedule-trigger"],
                    trigger_gap.coverage,
                    evidence_ids=trigger_evidence,
                    reason=trigger_gap.reason_code,
                )
            else:
                self._finish_coverage(
                    records["schedule-trigger"],
                    "covered" if trigger_evidence else "covered-empty",
                    evidence_ids=trigger_evidence,
                )
        self._collect_affected_resources(
            context, schedule_node, orchestration, records["schedule-affected-resources"]
        )
        self._collect_runs(
            context, schedule_node, orchestration, records["schedule-runs"]
        )

    def _collect_trigger(
        self,
        context: AnalysisContext,
        schedule_node: Node,
        operation_id: str,
        trigger: Optional[Mapping[str, Any]],
        locator: str,
        depth: int = 0,
    ) -> tuple[list[str], Optional[CoverageGap]]:
        evidence_ids: list[str] = []
        context.budget.check_metadata_depth(depth)
        context.budget.charge("items")
        if trigger is None:
            gap = self._add_gap(
                context,
                schedule_node.id,
                "schedule-trigger",
                "unresolved",
                "schedule-trigger-unobservable",
                f"Schedule trigger at {locator} may be omitted by permissions",
            )
            return evidence_ids, gap
        trigger_type = trigger.get("type")
        if trigger_type in {"and", "or"}:
            children = trigger.get("triggers")
            if not isinstance(children, list):
                gap = self._add_gap(
                    context,
                    schedule_node.id,
                    "schedule-trigger",
                    "unresolved",
                    "invalid-schedule-trigger",
                    f"Known trigger variant {trigger_type} is malformed at {locator}.triggers",
                )
                return evidence_ids, gap
            first_gap: Optional[CoverageGap] = None
            for index, child in enumerate(children):
                child_evidence, child_gap = self._collect_trigger(
                    context,
                    schedule_node,
                    operation_id,
                    child,
                    f"{locator}.triggers[{index}]",
                    depth + 1,
                )
                evidence_ids.extend(child_evidence)
                first_gap = first_gap or child_gap
            return evidence_ids, first_gap
        field_by_type = {
            "datasetUpdated": ("dataset_rid", "dataset"),
            "jobSucceeded": ("dataset_rid", "dataset"),
            "newLogic": ("dataset_rid", "dataset"),
            "tableUpdated": ("table_rid", "table"),
            "scheduleSucceeded": ("schedule_rid", "schedule"),
            "mediaSetUpdated": ("media_set_rid", "media-set"),
        }
        if trigger_type in {"manual", "time"}:
            return evidence_ids, None
        if trigger_type not in field_by_type:
            gap = self._add_gap(
                context,
                schedule_node.id,
                "schedule-trigger",
                "unresolved",
                f"unknown-schedule-trigger-variant:{trigger_type or 'missing'}",
                f"Unknown schedule trigger variant at {locator}",
            )
            return evidence_ids, gap
        field_name, kind = field_by_type[trigger_type]
        resource_rid = trigger.get(field_name)
        if not resource_rid:
            gap = self._add_gap(
                context,
                schedule_node.id,
                "schedule-trigger",
                "unresolved",
                "invalid-schedule-trigger",
                f"Trigger {trigger_type} is missing {field_name} at {locator}.{field_name}",
            )
            return evidence_ids, gap
        field_locator = f"{locator}.{field_name}"
        evidence = self._add_evidence(
            context,
            operation_id,
            field_locator,
            field_locator,
            resource_rid,
            str(trigger_type),
        )
        evidence_ids.append(evidence.id)
        resource = self._add_node(
            context, kind, str(resource_rid), {"resource_rid": str(resource_rid)}
        )
        self._add_edge(
            context,
            resource.id,
            schedule_node.id,
            "schedule-triggered-by-resource",
            [evidence.id],
        )
        return evidence_ids, None

    def _collect_affected_resources(
        self,
        context: AnalysisContext,
        schedule_node: Node,
        orchestration: OrchestrationService,
        record: CoverageRecord,
    ) -> None:
        try:
            response, operation_id = self._invoke_sdk(
                context,
                "schedule.get-affected-resources",
                orchestration.get_schedule_affected_resources,
                {"schedule_rid": schedule_node.identifiers["schedule_rid"], "preview": True},
                target=schedule_node.identifiers["schedule_rid"],
            )
        except Exception as error:
            self._record_failure(
                context, record, error, "schedule.get-affected-resources"
            )
            return
        evidence_ids: list[str] = []
        for index, resource_rid in enumerate(response.get("affected_resources", []) or []):
            context.budget.charge("items")
            locator = f"affectedResources[{index}]"
            evidence = self._add_evidence(
                context, operation_id, locator, locator, resource_rid
            )
            evidence_ids.append(evidence.id)
            resource = self._add_node(
                context,
                self._kind_from_rid(str(resource_rid)),
                str(resource_rid),
                {"resource_rid": str(resource_rid)},
            )
            self._add_edge(
                context,
                schedule_node.id,
                resource.id,
                "schedule-produces-resource",
                [evidence.id],
            )
        self._finish_coverage(
            record,
            "covered" if evidence_ids else "covered-empty",
            evidence_ids=evidence_ids,
        )

    def _collect_runs(
        self,
        context: AnalysisContext,
        schedule_node: Node,
        orchestration: OrchestrationService,
        record: CoverageRecord,
    ) -> None:
        page_token: Optional[str] = None
        run_evidence: list[str] = []
        build_work: list[tuple[Node, CoverageRecord]] = []
        try:
            while True:
                page_size = context.budget.reserve_page(100)
                kwargs: dict[str, Any] = {
                    "schedule_rid": schedule_node.identifiers["schedule_rid"],
                    "page_size": page_size,
                }
                if page_token is not None:
                    kwargs["page_token"] = page_token
                response, operation_id = self._invoke_sdk(
                    context,
                    "schedule.runs",
                    orchestration.get_schedule_runs,
                    kwargs,
                    target=schedule_node.identifiers["schedule_rid"],
                )
                runs = list(response.get("runs", []) or [])
                for index, run in enumerate(runs):
                    context.budget.charge("items")
                    result = run.get("result") or {}
                    run_rid = str(run.get("rid") or f"{schedule_node.identifiers['schedule_rid']}#run-{index}")
                    evidence = self._add_evidence(
                        context,
                        operation_id,
                        f"runs[{index}].result",
                        f"runs[{index}].result",
                        result,
                        result.get("type"),
                    )
                    run_evidence.append(evidence.id)
                    run_node = self._add_node(
                        context, "schedule-run", run_rid, {"run_rid": run_rid}
                    )
                    self._add_edge(
                        context,
                        schedule_node.id,
                        run_node.id,
                        "schedule-run",
                        [evidence.id],
                    )
                    if result.get("type") == "submitted" and result.get("build_rid"):
                        build_rid = str(result["build_rid"])
                        build_evidence = self._add_evidence(
                            context,
                            operation_id,
                            f"runs[{index}].result.buildRid",
                            f"runs[{index}].result.build_rid",
                            build_rid,
                            "submitted",
                        )
                        run_evidence.append(build_evidence.id)
                        build_node = self._add_node(
                            context, "build", build_rid, {"build_rid": build_rid}
                        )
                        self._add_edge(
                            context,
                            run_node.id,
                            build_node.id,
                            "run-submitted-build",
                            [build_evidence.id],
                        )
                        build_record = self._coverage_record(
                            context,
                            "build",
                            "submitted-build",
                            build_node.id,
                            parent_record_id=record.id,
                            applicability_evidence_id=build_evidence.id,
                        )
                        build_work.append((build_node, build_record))
                page_token = response.get("next_page_token")
                if not page_token:
                    break
        except Exception as error:
            self._record_failure(
                context,
                record,
                error,
                "schedule.runs",
                evidence_ids=run_evidence,
            )
            return
        self._finish_coverage(
            record,
            "covered" if run_evidence else "covered-empty",
            evidence_ids=run_evidence,
        )
        for build_node, build_record in build_work:
            self._collect_build(context, build_node, orchestration, build_record)

    def _collect_build(
        self,
        context: AnalysisContext,
        build_node: Node,
        orchestration: OrchestrationService,
        record: CoverageRecord,
    ) -> None:
        build_rid = build_node.identifiers["build_rid"]
        try:
            build, operation_id = self._invoke_sdk(
                context,
                "build.get",
                orchestration.get_build,
                {"build_rid": build_rid},
                target=build_rid,
            )
        except Exception as error:
            self._record_failure(context, record, error, "build.get")
            return
        if "target" in build:
            raise ValueError("Build response wrapper must not expose a target field")
        evidence = self._add_evidence(
            context, operation_id, "build.rid", "rid", build.get("rid", build_rid)
        )
        self._finish_coverage(record, "covered", evidence_ids=[evidence.id])
        jobs_record = self._coverage_record(
            context,
            "build",
            "build-jobs",
            build_node.id,
            parent_record_id=record.id,
            applicability_evidence_id=evidence.id,
        )
        self._collect_jobs(context, build_node, orchestration, jobs_record)

    def _collect_jobs(
        self,
        context: AnalysisContext,
        build_node: Node,
        orchestration: OrchestrationService,
        record: CoverageRecord,
    ) -> None:
        page_token: Optional[str] = None
        job_evidence: list[str] = []
        output_work: list[
            tuple[Node, Mapping[str, Any], str, int, CoverageRecord]
        ] = []
        try:
            while True:
                page_size = context.budget.reserve_page(100)
                kwargs: dict[str, Any] = {
                    "build_rid": build_node.identifiers["build_rid"],
                    "page_size": page_size,
                }
                if page_token is not None:
                    kwargs["page_token"] = page_token
                response, operation_id = self._invoke_sdk(
                    context,
                    "build.jobs",
                    orchestration.get_build_jobs,
                    kwargs,
                    target=build_node.identifiers["build_rid"],
                )
                jobs = list(response.get("jobs", []) or [])
                for index, job in enumerate(jobs):
                    context.budget.charge("items")
                    job_rid = str(job.get("rid") or f"{build_node.identifiers['build_rid']}#job-{index}")
                    evidence = self._add_evidence(
                        context,
                        operation_id,
                        f"jobs[{index}].rid",
                        f"jobs[{index}].rid",
                        job_rid,
                    )
                    job_evidence.append(evidence.id)
                    job_node = self._add_node(
                        context, "job", job_rid, {"job_rid": job_rid}
                    )
                    self._add_edge(
                        context,
                        build_node.id,
                        job_node.id,
                        "container-member",
                        [evidence.id],
                    )
                    output_record = self._coverage_record(
                        context,
                        "job",
                        "typed-outputs",
                        job_node.id,
                        parent_record_id=record.id,
                        applicability_evidence_id=evidence.id,
                    )
                    output_work.append(
                        (job_node, job, operation_id, index, output_record)
                    )
                page_token = response.get("next_page_token")
                if not page_token:
                    break
        except Exception as error:
            self._record_failure(
                context,
                record,
                error,
                "build.jobs",
                evidence_ids=job_evidence,
            )
            return
        self._finish_coverage(
            record,
            "covered" if job_evidence else "covered-empty",
            evidence_ids=job_evidence,
        )
        for job_node, job, operation_id, job_index, output_record in output_work:
            self._collect_outputs(
                context,
                build_node,
                job_node,
                job,
                operation_id,
                job_index,
                output_record,
            )

    def _collect_outputs(
        self,
        context: AnalysisContext,
        build_node: Node,
        job_node: Node,
        job: Mapping[str, Any],
        operation_id: str,
        job_index: int,
        record: CoverageRecord,
    ) -> None:
        try:
            self._collect_outputs_impl(
                context,
                build_node,
                job_node,
                job,
                operation_id,
                job_index,
                record,
            )
        except BudgetExhausted as error:
            locator_prefix = f"jobs[{job_index}].outputs["
            partial_evidence = [
                evidence.id
                for evidence in context.evidence.values()
                if evidence.operation_provenance_id == operation_id
                and evidence.locator.startswith(locator_prefix)
            ]
            self._record_failure(
                context,
                record,
                error,
                "job-output-metadata-walk",
                evidence_ids=partial_evidence,
            )

    def _collect_outputs_impl(
        self,
        context: AnalysisContext,
        build_node: Node,
        job_node: Node,
        job: Mapping[str, Any],
        operation_id: str,
        job_index: int,
        record: CoverageRecord,
    ) -> None:
        evidence_ids: list[str] = []
        output_nodes: dict[str, tuple[Node, list[str]]] = {}
        outputs = job.get("outputs", []) or []
        for output_index, output in enumerate(outputs):
            context.budget.charge("items")
            output_type = output.get("type")
            if output_type == "datasetJobOutput":
                field_name, kind = "dataset_rid", "dataset"
            elif output_type == "transactionalMediaSetJobOutput":
                field_name, kind = "media_set_rid", "media-set"
            else:
                self._add_gap(
                    context,
                    job_node.id,
                    "typed-outputs",
                    "unresolved",
                    f"unknown-job-output-variant:{output_type or 'missing'}",
                    f"Unknown job output variant at jobs[{job_index}].outputs[{output_index}]",
                    locator=f"jobs[{job_index}].outputs[{output_index}]",
                )
                continue
            resource_rid = output.get(field_name)
            if not resource_rid:
                self._add_gap(
                    context,
                    job_node.id,
                    "typed-outputs",
                    "unresolved",
                    "invalid-response",
                    f"Typed output is missing {field_name} at jobs[{job_index}].outputs[{output_index}]",
                    locator=f"jobs[{job_index}].outputs[{output_index}].{field_name}",
                )
                continue
            locator = f"jobs[{job_index}].outputs[{output_index}].{field_name}"
            evidence = self._add_evidence(
                context,
                operation_id,
                locator,
                locator,
                resource_rid,
                output_type,
            )
            evidence_ids.append(evidence.id)
            resource = self._add_node(
                context, kind, str(resource_rid), {"resource_rid": str(resource_rid)}
            )
            existing = output_nodes.get(resource.id)
            if existing is None:
                output_nodes[resource.id] = (resource, [evidence.id])
            else:
                existing[1].append(evidence.id)
            self._add_edge(
                context,
                job_node.id,
                resource.id,
                "build-produced-output",
                [evidence.id],
            )
        # A canonical star retains co-output reachability in linear space.  A
        # clique is redundant with the shared job parent and permits one raw
        # response to create quadratic local work and artifact size.
        unique_outputs = [output_nodes[key] for key in sorted(output_nodes)]
        if unique_outputs:
            anchor, anchor_evidence = unique_outputs[0]
            for related, related_evidence in unique_outputs[1:]:
                self._add_edge(
                    context,
                    anchor.id,
                    related.id,
                    "build-co-output",
                    [*anchor_evidence, *related_evidence],
                )
        self._finish_coverage(
            record,
            "partial",
            evidence_ids=evidence_ids,
            reason="unsupported-output-kinds",
        )
        self._add_gap(
            context,
            job_node.id,
            "typed-outputs",
            "partial",
            "unsupported-output-kinds",
            "The API omits unsupported job output kinds, so typed output coverage is partial",
        )

    def _record_failure(
        self,
        context: AnalysisContext,
        record: CoverageRecord,
        error: BaseException,
        operation: str,
        *,
        evidence_ids: Sequence[str] = (),
    ) -> None:
        if not _is_expected_collection_failure(error):
            raise error
        classified = classify_exception(error)
        status = (
            "budget-exhausted"
            if classified.error_class == "budget-exhausted"
            else classified.coverage
        )
        if isinstance(error, BudgetExhausted):
            context.caches[("budget-exhausted",)] = error
        self._finish_coverage(
            record,
            status,
            reason=classified.error_class,
            attempted=True,
            evidence_ids=evidence_ids,
        )
        snapshot = error.snapshot if isinstance(error, BudgetExhausted) else None
        self._add_gap(
            context,
            record.subject_node_id,
            record.surface,
            status,
            classified.error_class,
            str(error),
            retryable=classified.retryable,
            operation=operation,
            budget_snapshot=snapshot,
        )

    def _derive_paths(
        self, context: AnalysisContext, root_node_id: str, direction: str
    ) -> list[dict[str, Any]]:
        adjacency: dict[str, list[tuple[Edge, str]]] = defaultdict(list)
        for edge in context.edges.values():
            adjacency[edge.source].append((edge, edge.target))
            adjacency[edge.target].append((edge, edge.source))
        for node_id in adjacency:
            adjacency[node_id].sort(key=lambda item: (item[0].id, item[1]))
        queue: deque[tuple[str, tuple[PathStep, ...], tuple[str, ...]]] = deque(
            [(root_node_id, (), (root_node_id,))]
        )
        best_depth: dict[str, int] = {root_node_id: 0}
        paths: list[dict[str, Any]] = []
        while queue:
            current, steps, node_ids = queue.popleft()
            depth = len(steps)
            if depth >= context.budget.max_depth:
                continue
            for edge, neighbor in adjacency.get(current, ()):
                step_direction = self._traversal_direction(edge, current)
                if neighbor in node_ids:
                    continue
                new_depth = depth + 1
                if neighbor in best_depth and best_depth[neighbor] < new_depth:
                    continue
                best_depth[neighbor] = new_depth
                step = PathStep(
                    edge.id,
                    current,
                    neighbor,
                    step_direction,
                    edge.evidence_ids,
                )
                new_steps = steps + (step,)
                path_direction = self._overall_direction(new_steps)
                if direction in {"upstream", "downstream"} and path_direction != direction:
                    continue
                new_nodes = node_ids + (neighbor,)
                path_id = _stable_id(
                    "path", root_node_id, neighbor, *[item.edge_id for item in new_steps]
                )
                labels = [context.nodes[node_id].display_name for node_id in new_nodes]
                evidence_ids = tuple(
                    sorted(
                        {
                            evidence_id
                            for item in new_steps
                            for evidence_id in item.evidence_ids
                        }
                    )
                )
                path_payload = {
                        "id": path_id,
                        "root_node_id": root_node_id,
                        "related_node_id": neighbor,
                        "hop_count": new_depth,
                        "direction": path_direction,
                        "node_ids": list(new_nodes),
                        "node_labels": labels,
                        "readable_path": " -> ".join(labels),
                        "steps": [self._serialize(item) for item in new_steps],
                        "evidence_ids": list(evidence_ids),
                    }
                if direction == "both" or path_direction == direction:
                    paths.append(path_payload)
                queue.append((neighbor, new_steps, new_nodes))
        return sorted(paths, key=lambda item: (item["hop_count"], item["id"]))

    @staticmethod
    def _traversal_direction(edge: Edge, from_node: str) -> str:
        if edge.traversal_class == "adjacent-structural":
            return "adjacent"
        return "downstream" if from_node == edge.source else "upstream"

    @staticmethod
    def _overall_direction(steps: Sequence[PathStep]) -> str:
        return DependencyGraphService._overall_direction_values(
            tuple(step.traversal_direction for step in steps)
        )

    @staticmethod
    def _overall_direction_values(values: Sequence[str]) -> str:
        directions = set(values)
        if "adjacent" in directions or len(directions) != 1:
            return "adjacent"
        return next(iter(directions))

    def _rank_paths(
        self,
        context: AnalysisContext,
        paths: Sequence[dict[str, Any]],
        change: Optional[str],
    ) -> list[dict[str, Any]]:
        change_text = (change or "").lower()
        change_terms = {
            term
            for term in change_text.replace("-", " ").replace("_", " ").split()
            if term
        }
        semantic_relations = {
            "schema": {"property", "object-type", "link-type", "dataset", "query-type"},
            "type": {"property", "object-type", "link-type", "query-type"},
            "permission": {"project", "third-party-application", "generic-resource"},
            "schedule": {"schedule", "schedule-run", "build", "job"},
            "trigger": {"schedule", "dataset", "table", "media-set"},
            "output": {"dataset", "media-set", "build", "job"},
            "build": {"build", "job", "dataset", "media-set"},
            "function": {"function", "query-type", "action-type"},
            "action": {"action-type", "object-type", "property", "link-type"},
            "query": {"query-type", "object-type", "property"},
        }
        confidence_penalty = {
            "verified": 0,
            "partial": 1,
            "unresolved": 2,
            "unsupported": 3,
        }
        ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for path in paths:
            final_step = path["steps"][-1]
            edge = context.edges[final_step["edge_id"]]
            severity = 0 if edge.traversal_class == "dependency-flow" else 1
            related = context.nodes[path["related_node_id"]]
            relation_terms = set(edge.relation_kind.replace("-", " ").split())
            node_terms = {
                related.kind.lower(),
                *related.display_name.lower().replace("-", " ").split(),
                *(str(value).lower() for value in related.identifiers.values()),
            }
            semantic_kinds = {
                kind
                for term in change_terms
                for kind in semantic_relations.get(term, set())
            }
            if not change_terms:
                change_relevance = 1
            elif change_terms & (relation_terms | node_terms):
                change_relevance = 0
            elif related.kind in semantic_kinds:
                change_relevance = 0
            elif semantic_kinds:
                change_relevance = 2
            else:
                change_relevance = 1
            path_coverages = [
                context.edges[step["edge_id"]].coverage for step in path["steps"]
            ]
            coverage_score = max(
                (confidence_penalty.get(value, 2) for value in path_coverages),
                default=2,
            )
            first_evidence_id = next(iter(path["evidence_ids"]), None)
            evidence_summary: Optional[dict[str, Any]] = None
            if first_evidence_id is not None:
                evidence = context.evidence[first_evidence_id]
                operation = context.operation_provenance[
                    evidence.operation_provenance_id
                ]
                evidence_summary = {
                    "evidence_id": evidence.id,
                    "locator": evidence.locator,
                    "field_path": evidence.field_path,
                    "sdk_namespace": operation.sdk_namespace,
                    "sdk_method": operation.sdk_method,
                }
            item = dict(path)
            item.update(
                {
                    "relation_kind": edge.relation_kind,
                    "traversal_class": edge.traversal_class,
                    "evidence_summary": evidence_summary,
                    "first_evidence_locator": evidence_summary["locator"]
                    if evidence_summary
                    else None,
                    "sdk_namespace": evidence_summary["sdk_namespace"]
                    if evidence_summary
                    else None,
                    "sdk_method": evidence_summary["sdk_method"]
                    if evidence_summary
                    else None,
                    "change_relevance": change_relevance,
                    "coverage_confidence": (
                        "verified" if coverage_score == 0 else "partial"
                    ),
                }
            )
            ranked.append(
                (
                    (
                        path["hop_count"],
                        change_relevance,
                        coverage_score,
                        severity,
                        path["id"],
                    ),
                    item,
                )
            )
        return [item for _, item in sorted(ranked, key=lambda value: value[0])]

    def _assess_change(
        self,
        change: str,
        ranked: Sequence[dict[str, Any]],
        context: AnalysisContext,
    ) -> dict[str, Any]:
        impacts = [
            {
                "path_id": path["id"],
                "related_node_id": path["related_node_id"],
                "direction": path["direction"],
                "relation_kind": path["relation_kind"],
                "readable_path": path["readable_path"],
                "reason": "Direct dependency requires verification"
                if path.get("hop_count", 1) == 1
                else "Transitive dependency may require verification",
            }
            for path in ranked
        ]
        uncertainty = [
            {
                "gap_id": gap.id,
                "surface": gap.surface,
                "reason_code": gap.reason_code,
                "message": gap.message,
            }
            for _, gap in sorted(context.gaps.items())
        ]
        verification_needed = sorted(
            {
                context.nodes[path["related_node_id"]].display_name
                for path in ranked
            }
        )
        if uncertainty:
            verification_needed.append("Resolve or accept intersecting coverage gaps")
        return {
            "change": change,
            "ranked_impacts": impacts,
            "verification_needed": verification_needed,
            "uncertainty": uncertainty,
            "assessment": "Verified relationships exist, but coverage gaps preserve uncertainty"
            if uncertainty
            else "Assessment is based on verified discovered relationships",
        }

    @staticmethod
    def _declared_fields(value: Any) -> list[tuple[str, Any]]:
        model_fields = getattr(type(value), "model_fields", None)
        if model_fields is not None:
            return [
                (name, getattr(value, name, None)) for name in sorted(model_fields)
            ]
        if isinstance(value, Mapping):
            return [(str(name), item) for name, item in sorted(value.items())]
        return []

    @staticmethod
    def _field_alias(value: Any, name: str) -> str:
        model_fields = getattr(type(value), "model_fields", None)
        if model_fields is None or name not in model_fields:
            return name
        return str(model_fields[name].alias or name)

    @classmethod
    def _model_dict(cls, value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return {str(key): cls._model_value(item) for key, item in value.items()}
        if hasattr(value, "model_dump"):
            return value.model_dump(by_alias=False, mode="json")
        if hasattr(value, "dict"):
            return value.dict(by_alias=False)
        return {
            name: cls._model_value(item)
            for name, item in cls._declared_fields(value)
        }

    @classmethod
    def _model_value(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): cls._model_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._model_value(item) for item in value]
        if hasattr(value, "model_dump"):
            return value.model_dump(by_alias=False, mode="json")
        return value

    @staticmethod
    def _resource_type(data: Mapping[str, Any]) -> str:
        value = data.get("type") or data.get("resource_type") or ""
        if isinstance(value, Mapping):
            value = value.get("type") or value.get("name") or ""
        return str(value).strip().lower()

    @staticmethod
    def _resource_kind(resource_type: str) -> str:
        normalized = resource_type.replace("_", "-").replace(" ", "-")
        if normalized in {"dataset", "foundry-dataset"}:
            return "dataset"
        if normalized in {
            "third-party-application",
            "thirdpartyapplication",
            "third-party-applications-application",
        }:
            return "third-party-application"
        if normalized in {
            "workshop",
            "workshop-resource",
            "workshop-module",
            "workshop-state",
        }:
            return "workshop-resource"
        return "generic-resource"

    @staticmethod
    def _kind_from_rid(resource_rid: str) -> str:
        parts = resource_rid.lower().split(".")
        if "dataset" in parts:
            return "dataset"
        if "media-set" in parts or "mediaset" in parts:
            return "media-set"
        if "table" in parts:
            return "table"
        return "resource"

    @staticmethod
    def _serialize(value: Any) -> Any:
        if isinstance(value, CoverageRecord):
            return dict(asdict(value), id=value.id)
        if hasattr(value, "__dataclass_fields__"):
            return asdict(value)
        return value
