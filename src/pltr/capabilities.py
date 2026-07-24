"""Versioned capability manifest for the native, agent-first Foundry CLI."""

from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Iterable, Mapping, Optional, Sequence


CAPABILITY_SCHEMA_VERSION = "foundry-agent-capabilities-v1"
CATALOG_VERSION = "palantir-mcp-available-tools-2026-07-20"
CATALOG_SOURCE_URL = (
    "https://www.palantir.com/docs/foundry/palantir-mcp/available-tools/"
)
CATALOG_RETRIEVED_ON = "2026-07-20"
VALID_KINDS = frozenset({"tool", "workflow"})
VALID_STATUSES = frozenset({"planned", "implemented", "blocked", "unsupported"})
VALID_MUTATION_RISKS = frozenset({"read", "write", "destructive"})


class ManifestValidationError(ValueError):
    """Raised when the capability manifest violates its contract."""

    def __init__(self, errors: Sequence[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class CapabilitySpec:
    """One native CLI capability and its parity evidence."""

    capability_id: str
    kind: str
    group: str
    command: str
    service: str
    api_evidence: str
    status: str
    mutation_risk: str
    output_contract: str
    test_reference: str
    blocked_reason: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        """Return the stable serialized representation."""
        return asdict(self)


# These IDs are the exact tool rows from the 2026-07-20 parity baseline.
_TOOL_ROWS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "compass",
        "list_resources_in_foundry_folder",
        "resource list",
        "ResourceService",
        "official-catalog",
    ),
    (
        "compass",
        "get_project_imports",
        "project imports",
        "ProjectService",
        "official-catalog",
    ),
    (
        "compass",
        "list_foundry_namespaces",
        "namespace list",
        "CompassService",
        "official-catalog",
    ),
    (
        "compass",
        "list_foundry_project_templates",
        "project templates list",
        "CompassService",
        "official-catalog",
    ),
    (
        "compass",
        "create_foundry_project",
        "project create",
        "ProjectService",
        "official-catalog",
    ),
    (
        "compass",
        "search_foundry_projects",
        "project search",
        "ProjectService",
        "official-catalog",
    ),
    (
        "dataset",
        "get_foundry_dataset_schema",
        "dataset schema get",
        "DatasetService",
        "official-catalog",
    ),
    (
        "dataset",
        "run_sql_query_on_foundry_dataset",
        "sql execute",
        "SqlService",
        "official-catalog",
    ),
    (
        "dataset",
        "create_and_write_to_foundry_dataset",
        "dataset create",
        "DatasetService",
        "official-catalog",
    ),
    (
        "dataset",
        "list_dataset_files",
        "dataset files list",
        "DatasetService",
        "official-catalog",
    ),
    (
        "dataset",
        "build_datasets",
        "orchestration builds create",
        "OrchestrationService",
        "official-catalog",
    ),
    (
        "dataset",
        "get_build_status",
        "orchestration builds get",
        "OrchestrationService",
        "official-catalog",
    ),
    (
        "dataset",
        "search_dataset_builds",
        "orchestration builds search",
        "OrchestrationService",
        "official-catalog",
    ),
    (
        "dataset",
        "get_job_status",
        "orchestration jobs get",
        "OrchestrationService",
        "official-catalog",
    ),
    (
        "dataset",
        "get_dataset_stats",
        "dataset stats",
        "DatasetService",
        "official-catalog",
    ),
    (
        "data-lineage",
        "get_resource_graph",
        "lineage graph",
        "LineageService",
        "official-catalog",
    ),
    (
        "ontology",
        "get_foundry_ontology_rid",
        "ontology rid",
        "OntologyService",
        "official-catalog",
    ),
    (
        "ontology",
        "search_foundry_ontology",
        "ontology object-search",
        "OntologyService",
        "official-catalog",
    ),
    (
        "ontology",
        "search_foundry_functions",
        "functions search",
        "FunctionsService",
        "official-catalog",
    ),
    (
        "ontology",
        "view_foundry_object_type",
        "ontology object-type-get",
        "ObjectTypeService",
        "official-catalog",
    ),
    (
        "ontology",
        "create_or_update_foundry_object_type",
        "ontology object-type-create",
        "ObjectTypeService",
        "official-catalog",
    ),
    (
        "ontology",
        "delete_foundry_object_type",
        "ontology object-type-delete",
        "ObjectTypeService",
        "official-catalog",
    ),
    (
        "ontology",
        "view_foundry_link_type",
        "ontology link-type-get",
        "ObjectTypeService",
        "official-catalog",
    ),
    (
        "ontology",
        "create_or_update_foundry_link_type",
        "ontology link-type-create",
        "ObjectTypeService",
        "official-catalog",
    ),
    (
        "ontology",
        "delete_foundry_link_type",
        "ontology link-type-delete",
        "ObjectTypeService",
        "official-catalog",
    ),
    (
        "ontology",
        "view_foundry_action_type",
        "ontology action-type-get",
        "ActionService",
        "official-catalog",
    ),
    (
        "ontology",
        "create_or_update_foundry_action_type",
        "ontology action-type-upsert",
        "ActionService",
        "official-catalog",
    ),
    (
        "ontology",
        "delete_foundry_action_type",
        "ontology action-type-delete",
        "ActionService",
        "official-catalog",
    ),
    (
        "object-set",
        "query_ontology_objects",
        "ontology query-execute",
        "OntologyObjectService",
        "official-catalog",
    ),
    (
        "object-set",
        "aggregate_ontology_objects",
        "ontology object-aggregate",
        "OntologyObjectService",
        "official-catalog",
    ),
    (
        "osdk",
        "get_ontology_sdk_context",
        "osdk context",
        "OsdkService",
        "official-catalog",
    ),
    (
        "osdk",
        "get_ontology_sdk_examples",
        "osdk examples",
        "OsdkService",
        "official-catalog",
    ),
    (
        "platform-sdk",
        "list_platform_sdk_apis",
        "platform-sdk api list",
        "PlatformSdkService",
        "official-catalog",
    ),
    (
        "platform-sdk",
        "get_platform_sdk_api_reference",
        "platform-sdk api reference",
        "PlatformSdkService",
        "official-catalog",
    ),
    (
        "code-repository",
        "get_repository_context",
        "repository context",
        "RepositoryService",
        "official-catalog",
    ),
    (
        "code-repository",
        "create_python_transforms_code_repository",
        "repository create-python-transforms",
        "RepositoryService",
        "official-catalog",
    ),
    (
        "code-repository",
        "clone_code_repository_locally",
        "repository clone",
        "RepositoryService",
        "official-catalog",
    ),
    (
        "code-repository",
        "create_code_repository_pull_request",
        "repository pull-request create",
        "RepositoryService",
        "official-catalog",
    ),
    (
        "code-repository",
        "list_code_repository_pull_requests",
        "repository pull-request list",
        "RepositoryService",
        "official-catalog",
    ),
    (
        "code-repository",
        "get_code_repository_pull_request",
        "repository pull-request get",
        "RepositoryService",
        "official-catalog",
    ),
    (
        "code-repository",
        "create_code_repository_pull_request_comment",
        "repository pull-request comment",
        "RepositoryService",
        "official-catalog",
    ),
    (
        "global-branching",
        "create_global_branch",
        "global-branch create",
        "GlobalBranchService",
        "official-catalog",
    ),
    (
        "global-branching",
        "view_global_branch",
        "global-branch get",
        "GlobalBranchService",
        "official-catalog",
    ),
    (
        "global-branching",
        "close_global_branch",
        "global-branch close",
        "GlobalBranchService",
        "official-catalog",
    ),
    (
        "global-branching",
        "create_global_proposal",
        "global-proposal create",
        "GlobalProposalService",
        "official-catalog",
    ),
    (
        "global-branching",
        "view_global_proposal",
        "global-proposal get",
        "GlobalProposalService",
        "official-catalog",
    ),
    (
        "global-branching",
        "close_global_proposal",
        "global-proposal close",
        "GlobalProposalService",
        "official-catalog",
    ),
    (
        "developer-console",
        "connect_to_dev_console_app",
        "dev-console connect",
        "DeveloperConsoleService",
        "official-catalog",
    ),
    (
        "developer-console",
        "convert_to_osdk_react",
        "dev-console convert-osdk-react",
        "DeveloperConsoleService",
        "official-catalog",
    ),
    (
        "developer-console",
        "generate_new_ontology_sdk_version",
        "dev-console sdk generate",
        "DeveloperConsoleService",
        "official-catalog",
    ),
    (
        "developer-console",
        "install_sdk_package",
        "dev-console sdk install",
        "DeveloperConsoleService",
        "official-catalog",
    ),
    (
        "developer-console",
        "view_osdk_definition",
        "dev-console osdk definition",
        "DeveloperConsoleService",
        "official-catalog",
    ),
    (
        "compute",
        "get_compute_modules_documentation",
        "compute docs",
        "ComputeService",
        "official-catalog",
    ),
    (
        "compute",
        "get_compute_modules_info",
        "compute info",
        "ComputeService",
        "official-catalog",
    ),
    (
        "compute",
        "get_compute_modules_logs",
        "compute logs",
        "ComputeService",
        "official-catalog",
    ),
    (
        "compute",
        "manage_compute_modules",
        "compute manage",
        "ComputeService",
        "official-catalog",
    ),
    (
        "compute",
        "execute_compute_modules_function",
        "compute execute",
        "ComputeService",
        "official-catalog",
    ),
    (
        "data-connection",
        "create_foundry_rest_api_data_source",
        "connectivity rest-source create",
        "DataConnectionService",
        "official-catalog",
    ),
    (
        "data-connection",
        "create_foundry_rest_api_data_source_webhook",
        "connectivity webhook create",
        "DataConnectionService",
        "official-catalog",
    ),
    (
        "data-connection",
        "update_foundry_rest_api_data_source_webhook",
        "connectivity webhook update",
        "DataConnectionService",
        "official-catalog",
    ),
    (
        "data-connection",
        "view_foundry_rest_api_data_source_webhook",
        "connectivity webhook get",
        "DataConnectionService",
        "official-catalog",
    ),
    (
        "data-connection",
        "get_or_create_network_egress_policy",
        "connectivity egress ensure",
        "DataConnectionService",
        "official-catalog",
    ),
    (
        "documentation",
        "get_python_transforms_documentation",
        "docs python-transforms",
        "DocumentationService",
        "official-catalog",
    ),
    (
        "documentation",
        "get_typescript_v1_functions_documentation",
        "docs typescript-v1-functions",
        "DocumentationService",
        "official-catalog",
    ),
    (
        "documentation",
        "get_typescript_v2_functions_documentation",
        "docs typescript-v2-functions",
        "DocumentationService",
        "official-catalog",
    ),
    (
        "documentation",
        "get_custom_widget_documentation",
        "docs custom-widgets",
        "DocumentationService",
        "official-catalog",
    ),
    (
        "documentation",
        "get_ml_documentation",
        "docs ml",
        "DocumentationService",
        "official-catalog",
    ),
    (
        "documentation",
        "get_spark_profile_documentation",
        "docs spark-profile",
        "DocumentationService",
        "official-catalog",
    ),
    (
        "documentation",
        "get_osdk_react_components_documentation",
        "docs osdk-react-components",
        "DocumentationService",
        "official-catalog",
    ),
    (
        "documentation",
        "load_foundry_documentation_page",
        "docs page",
        "DocumentationService",
        "official-catalog",
    ),
    (
        "documentation",
        "get_documentation_summaries",
        "docs summaries",
        "DocumentationService",
        "official-catalog",
    ),
    (
        "documentation",
        "search_foundry_documentation",
        "docs search",
        "DocumentationService",
        "official-catalog",
    ),
)

# Richer evidence for a few implemented capabilities. This no longer drives
# status — status is derived from whether the mapped command actually exists in
# the CLI (see _spec_status) — it only replaces the generic "official-catalog"
# evidence string with the concrete SDK path when one is known.
_IMPLEMENTED_EVIDENCE: dict[str, str] = {
    "get_project_imports": "foundry-platform-sdk==1.95.0: filesystem.Project.Reference.list",
    "search_foundry_projects": "foundry-platform-sdk==1.95.0: filesystem.Space.list + Folder.children",
    "get_dataset_stats": "foundry-platform-sdk==1.95.0: datasets.Dataset.File.list + Dataset.transactions",
    "get_resource_graph": "foundry-platform-sdk==1.95.0: filesystem.Resource.get + Folder.children + Project.Reference.list",
}

# Out of scope for a Foundry operations CLI. These MCP tools fetch
# documentation, generate or inspect SDK code, or drive a local IDE / dev
# console -- none of which are Foundry control-plane operations. They are
# reported so the parity picture is complete, marked unsupported (with a
# reason) rather than dangled as "planned" work this CLI intends to build.
_DOC_REASON = (
    "Documentation retrieval is an IDE-assistant function, not a Foundry "
    "operation; this CLI does not proxy Palantir's documentation."
)
_SDK_REASON = (
    "SDK generation and inspection is a codegen / IDE-assistant function "
    "outside the scope of a Foundry operations CLI."
)
_WORKSPACE_REASON = (
    "Local workspace and dev-console actions run in an IDE, not a headless CLI."
)
_UNSUPPORTED: dict[str, str] = {
    # documentation retrieval
    "get_python_transforms_documentation": _DOC_REASON,
    "get_typescript_v1_functions_documentation": _DOC_REASON,
    "get_typescript_v2_functions_documentation": _DOC_REASON,
    "get_custom_widget_documentation": _DOC_REASON,
    "get_ml_documentation": _DOC_REASON,
    "get_spark_profile_documentation": _DOC_REASON,
    "get_osdk_react_components_documentation": _DOC_REASON,
    "get_compute_modules_documentation": _DOC_REASON,
    "load_foundry_documentation_page": _DOC_REASON,
    "get_documentation_summaries": _DOC_REASON,
    "search_foundry_documentation": _DOC_REASON,
    # SDK / OSDK codegen and inspection
    "get_ontology_sdk_context": _SDK_REASON,
    "get_ontology_sdk_examples": _SDK_REASON,
    "list_platform_sdk_apis": _SDK_REASON,
    "get_platform_sdk_api_reference": _SDK_REASON,
    "convert_to_osdk_react": _SDK_REASON,
    "generate_new_ontology_sdk_version": _SDK_REASON,
    "install_sdk_package": _SDK_REASON,
    "view_osdk_definition": _SDK_REASON,
    # local IDE / dev console / workspace
    "connect_to_dev_console_app": _WORKSPACE_REASON,
    "clone_code_repository_locally": _WORKSPACE_REASON,
    "get_repository_context": _WORKSPACE_REASON,
    "create_python_transforms_code_repository": _WORKSPACE_REASON,
}

_U3_TEST_REFERENCES: dict[str, str] = {
    "get_project_imports": "tests/test_services/test_project.py;tests/test_commands/test_project.py",
    "list_foundry_namespaces": "tests/test_services/test_compass.py;tests/test_commands/test_namespace.py",
    "list_foundry_project_templates": "tests/test_services/test_compass.py;tests/test_commands/test_project.py",
    "search_foundry_projects": "tests/test_services/test_project.py;tests/test_commands/test_project.py",
    "get_dataset_stats": "tests/test_services/test_dataset.py;tests/test_commands/test_dataset.py",
    "get_resource_graph": "tests/test_services/test_lineage.py;tests/test_commands/test_lineage.py",
}

_U3_BLOCKED: dict[str, str] = {
    "list_foundry_namespaces": (
        "foundry-platform-sdk==1.95.0 exposes filesystem.Space.list but no "
        "Namespace resource or documented namespace-list operation; the CLI "
        "offers Space discovery as an explicit namespace-like fallback"
    ),
    "list_foundry_project_templates": (
        "foundry-platform-sdk==1.95.0 exposes create_from_template and a "
        "ProjectTemplateRid type, but no public template-list operation or "
        "documented template catalog endpoint"
    ),
}

_WORKFLOW_ROWS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "dataset",
        "preview_transform",
        "orchestration transform-preview",
        "OrchestrationService",
        "official-overview-workflow",
    ),
)


@lru_cache(maxsize=1)
def registered_command_paths() -> frozenset[str]:
    """Every command path registered on the live Typer app.

    Imported lazily so this module never triggers a circular import at load
    time. By the time this runs (a command invocation or a test) the CLI is
    fully assembled. This is the same surface `pltr agent-manifest` emits, so a
    capability marked implemented is guaranteed to name a command that exists.
    """
    import click
    from typer.main import get_command

    from pltr.cli import app

    paths: set[str] = set()

    def _walk(command: click.Command, prefix: tuple[str, ...] = ()) -> None:
        if isinstance(command, click.Group):
            for name, sub in command.commands.items():
                _walk(sub, (*prefix, name))
        elif prefix:
            paths.add(" ".join(prefix))

    _walk(get_command(app))
    return frozenset(paths)


def _spec_status(
    capability_id: str, command: str, command_paths: frozenset[str]
) -> tuple[str, Optional[str], Optional[str]]:
    """Derive (status, blocked_reason, evidence_override) for one capability.

    Precedence matters. A capability the SDK cannot do (`blocked`) or that is
    out of scope for a CLI (`unsupported`) is classified explicitly and keeps
    that status even when it names a real fallback command. Everything else is
    classified against the live command surface: `implemented` iff the mapped
    command exists today, `planned` otherwise. So the implemented/planned split
    can never drift from the commands that actually ship, while blocked and
    unsupported stay authoritative.
    """
    if capability_id in _U3_BLOCKED:
        return "blocked", _U3_BLOCKED[capability_id], None
    if capability_id in _UNSUPPORTED:
        return "unsupported", _UNSUPPORTED[capability_id], None
    if command in command_paths:
        return "implemented", None, _IMPLEMENTED_EVIDENCE.get(capability_id)
    return "planned", None, None


def _build_specs(
    command_paths: Optional[frozenset[str]] = None,
) -> tuple[CapabilitySpec, ...]:
    paths = command_paths if command_paths is not None else registered_command_paths()
    specs: list[CapabilitySpec] = []
    for group, capability_id, command, service, evidence in (
        *_TOOL_ROWS,
        *_WORKFLOW_ROWS,
    ):
        mutation_risk = "read"
        if capability_id.startswith(("create_", "update_", "manage_", "execute_")):
            mutation_risk = "write"
        if capability_id.startswith(("delete_", "close_")):
            mutation_risk = "destructive"
        status, blocked_reason, evidence_override = _spec_status(
            capability_id, command, paths
        )
        specs.append(
            CapabilitySpec(
                capability_id=capability_id,
                kind="workflow" if capability_id == "preview_transform" else "tool",
                group=group,
                command=command,
                service=service,
                api_evidence=evidence_override or evidence,
                status=status,
                mutation_risk=mutation_risk,
                output_contract="agent-v1",
                test_reference=_U3_TEST_REFERENCES.get(
                    capability_id,
                    "tests/test_capabilities.py;tests/test_commands/test_capabilities.py",
                ),
                blocked_reason=blocked_reason,
            )
        )
    return tuple(specs)


@lru_cache(maxsize=1)
def all_capabilities() -> tuple[CapabilitySpec, ...]:
    """The full capability set with status derived from the live command surface."""
    return _build_specs()


def __getattr__(name: str) -> Any:
    # PEP 562: keep `from pltr.capabilities import CAPABILITIES` working without
    # walking the Typer app at module-import time (which would be circular).
    if name == "CAPABILITIES":
        return all_capabilities()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _validation_errors(specs: Iterable[CapabilitySpec]) -> list[str]:
    entries = tuple(specs)
    errors: list[str] = []
    ids = [entry.capability_id for entry in entries]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        errors.append(f"duplicate capability ids: {', '.join(duplicates)}")

    for index, entry in enumerate(entries):
        prefix = f"capabilities[{index}]"
        if not entry.capability_id.strip():
            errors.append(f"{prefix}.capability_id is required")
        if entry.kind not in VALID_KINDS:
            errors.append(f"{entry.capability_id}.kind is invalid: {entry.kind}")
        if not entry.group.strip():
            errors.append(f"{entry.capability_id}.group is required")
        if not entry.command.strip():
            errors.append(f"{entry.capability_id}.command is required")
        if not entry.service.strip():
            errors.append(f"{entry.capability_id}.service is required")
        if not entry.api_evidence.strip():
            errors.append(f"{entry.capability_id}.api_evidence is required")
        if entry.status not in VALID_STATUSES:
            errors.append(f"{entry.capability_id}.status is invalid: {entry.status}")
        if entry.mutation_risk not in VALID_MUTATION_RISKS:
            errors.append(
                f"{entry.capability_id}.mutation_risk is invalid: {entry.mutation_risk}"
            )
        if not entry.output_contract.strip():
            errors.append(f"{entry.capability_id}.output_contract is required")
        if not entry.test_reference.strip():
            errors.append(f"{entry.capability_id}.test_reference is required")
        if entry.status in {"blocked", "unsupported"} and not entry.blocked_reason:
            errors.append(
                f"{entry.capability_id}.blocked_reason is required for {entry.status}"
            )
        if entry.status in {"planned", "implemented"} and entry.blocked_reason:
            errors.append(
                f"{entry.capability_id}.blocked_reason is only valid for blocked/unsupported"
            )

    expected_tools = {row[1] for row in _TOOL_ROWS}
    actual_tools = {entry.capability_id for entry in entries if entry.kind == "tool"}
    missing_tools = sorted(expected_tools - actual_tools)
    unexpected_tools = sorted(actual_tools - expected_tools)
    if missing_tools:
        errors.append(f"missing baseline tool ids: {', '.join(missing_tools)}")
    if unexpected_tools:
        errors.append(f"unexpected baseline tool ids: {', '.join(unexpected_tools)}")

    expected_workflows = {row[1] for row in _WORKFLOW_ROWS}
    actual_workflows = {
        entry.capability_id for entry in entries if entry.kind == "workflow"
    }
    missing_workflows = sorted(expected_workflows - actual_workflows)
    if missing_workflows:
        errors.append(f"missing workflow ids: {', '.join(missing_workflows)}")
    if actual_workflows - expected_workflows:
        errors.append(
            f"unexpected workflow ids: {', '.join(sorted(actual_workflows - expected_workflows))}"
        )
    return errors


def validate_capabilities(
    specs: Optional[Iterable[CapabilitySpec]] = None,
) -> None:
    """Validate a capability collection, raising one deterministic error."""
    if specs is None:
        specs = all_capabilities()
    errors = _validation_errors(specs)
    if errors:
        raise ManifestValidationError(errors)


def manifest_payload(
    specs: Optional[Iterable[CapabilitySpec]] = None,
) -> dict[str, Any]:
    """Return the complete versioned manifest payload."""
    if specs is None:
        specs = all_capabilities()
    entries = tuple(specs)
    validate_capabilities(entries)
    capabilities = [entry.as_dict() for entry in entries]
    return {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "catalog": {
            "version": CATALOG_VERSION,
            "source_url": CATALOG_SOURCE_URL,
            "retrieved_on": CATALOG_RETRIEVED_ON,
            "tool_count": sum(entry.kind == "tool" for entry in entries),
            "workflow_count": sum(entry.kind == "workflow" for entry in entries),
        },
        "counts": {
            "total": len(entries),
            "implemented": sum(entry.status == "implemented" for entry in entries),
            "planned": sum(entry.status == "planned" for entry in entries),
            "blocked": sum(entry.status == "blocked" for entry in entries),
            "unsupported": sum(entry.status == "unsupported" for entry in entries),
        },
        "capabilities": capabilities,
    }


def capability_manifest(
    specs: Optional[Iterable[CapabilitySpec]] = None,
) -> Mapping[str, Any]:
    """Return the validated native CLI capability manifest."""
    return manifest_payload(specs)
