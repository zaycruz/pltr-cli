"""Transport provider seams for dependency discovery operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, Union

from .dependency import (
    SDK_OPERATION_SPECS,
    AnalysisContext,
    ArgumentObservation,
    DependencyFatalError,
    SDKOperationSpec,
    OperationProvenance,
    _is_expected_collection_failure,
    _stable_id,
    _utc_now,
    classify_exception,
)


class OperationSpec(Protocol):
    """Common supertype implemented by transport-specific operation specs."""

    capability_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProviderResult:
    """Result and evidence semantics returned by a dependency provider."""

    payload: Any
    operation_provenance_id: str
    result_semantics: str
    positive_control_status: str


class SdkInvoke(Protocol):
    def __call__(
        self,
        context: AnalysisContext,
        operation: str,
        kwargs: Mapping[str, Any],
        *,
        target: str,
        fatal: bool = False,
        known_limitations: Sequence[dict[str, Any]] = (),
    ) -> ProviderResult: ...


class ConjureInvoke(Protocol):
    def __call__(
        self,
        context: AnalysisContext,
        operation: str,
        verb: str,
        path: str,
        body: Optional[Mapping[str, Any]],
        *,
        target: str,
    ) -> ProviderResult: ...


class GraphQLInvoke(Protocol):
    def __call__(
        self,
        context: AnalysisContext,
        operation: str,
        operation_name: str,
        document: str,
        variables_list: Sequence[Mapping[str, Any]],
        *,
        target: str,
    ) -> ProviderResult: ...


ProviderInvoke = Union[SdkInvoke, ConjureInvoke, GraphQLInvoke]


class Provider(Protocol):
    """Provider with a transport-specific invocation signature."""

    invoke: ProviderInvoke


class SdkProvider(Provider):
    """Invoke one bound Foundry SDK operation with immutable provenance."""

    def __init__(self, call: Callable[..., Any]) -> None:
        self.call = call

    def invoke(
        self,
        context: AnalysisContext,
        operation: str,
        kwargs: Mapping[str, Any],
        *,
        target: str,
        fatal: bool = False,
        known_limitations: Sequence[dict[str, Any]] = (),
    ) -> ProviderResult:
        spec: SDKOperationSpec | None = SDK_OPERATION_SPECS.get(operation)
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
            response = self.call(**supplied)
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
                transport="sdk",
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
            transport="sdk",
        )
        return ProviderResult(response, operation_id, "ok", "not-run")

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
