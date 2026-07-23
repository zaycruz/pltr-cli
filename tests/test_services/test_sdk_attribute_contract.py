"""Contract-test SDK call paths against the installed Foundry SDK.

Service unit tests commonly replace ``FoundryClient`` with ``MagicMock``.  That
is useful for testing command behavior, but it also lets misspelled or obsolete
SDK attribute chains pass unnoticed.  This test discovers the statically
expressed SDK chains in every ``BaseService`` subclass and resolves them on a
real, credential-free client from the installed SDK.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Iterator

import pytest
from foundry_sdk import FoundryClient, UserTokenAuth


SERVICES_DIR = Path(__file__).parents[2] / "src" / "pltr" / "services"
PINNED_SDK_VERSION = "1.95.0"


@dataclass(frozen=True, order=True)
class SdkAttributeUse:
    """One SDK attribute chain and the service source location using it."""

    path: Path
    line: int
    class_name: str
    root_attribute: str
    remainder: tuple[str, ...]

    @property
    def location(self) -> str:
        return f"{self.path.relative_to(SERVICES_DIR.parent.parent.parent)}:{self.line}"

    @property
    def source_expression(self) -> str:
        attributes = ".".join((self.root_attribute, *self.remainder))
        return f"{self.class_name}.self.{attributes}"


def _self_attribute_chain(node: ast.AST) -> tuple[str, ...] | None:
    """Return the attributes after ``self`` for a simple attribute expression."""

    attributes: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        attributes.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name) or current.id != "self":
        return None
    return tuple(reversed(attributes))


def _named_attribute_chain(node: ast.AST) -> tuple[str, ...] | None:
    """Return a simple name followed by its attributes."""

    attributes: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        attributes.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    return (current.id, *reversed(attributes))


def _is_base_service_subclass(node: ast.ClassDef) -> bool:
    return any(
        (isinstance(base, ast.Name) and base.id == "BaseService")
        or (isinstance(base, ast.Attribute) and base.attr == "BaseService")
        for base in node.bases
    )


def _class_sdk_attribute_uses(
    path: Path, service_class: ast.ClassDef
) -> Iterator[SdkAttributeUse]:
    sdk_roots = {"client", "service"}
    sdk_roots.update(
        statement.name
        for statement in service_class.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(decorator, ast.Name) and decorator.id == "property"
            for decorator in statement.decorator_list
        )
    )
    parent_by_node = {
        child: parent
        for parent in ast.walk(service_class)
        for child in ast.iter_child_nodes(parent)
    }

    for node in ast.walk(service_class):
        if not isinstance(node, ast.Attribute):
            continue
        parent = parent_by_node.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            continue

        chain = _self_attribute_chain(node)
        if chain is None or len(chain) < 2:
            continue
        root, remainder = chain[0], chain[1:]
        if root not in sdk_roots:
            continue
        yield SdkAttributeUse(
            path=path,
            line=node.lineno,
            class_name=service_class.name,
            root_attribute=root,
            remainder=remainder,
        )

    for method in service_class.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local_aliases: dict[str, tuple[str, ...]] = {}
        for node in ast.walk(method):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value_chain = (
                _self_attribute_chain(node.value) if node.value is not None else None
            )
            if (
                value_chain is None
                or len(value_chain) < 2
                or value_chain[0] not in sdk_roots
            ):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    local_aliases[target.id] = value_chain

        for node in ast.walk(method):
            if not isinstance(node, ast.Attribute):
                continue
            parent = parent_by_node.get(node)
            if isinstance(parent, ast.Attribute) and parent.value is node:
                continue
            local_chain = _named_attribute_chain(node)
            if (
                local_chain is None
                or local_chain[0] not in local_aliases
                or len(local_chain) < 2
            ):
                continue
            root, *remainder = local_aliases[local_chain[0]]
            yield SdkAttributeUse(
                path=path,
                line=node.lineno,
                class_name=service_class.name,
                root_attribute=root,
                remainder=(*remainder, *local_chain[1:]),
            )


def _sdk_attribute_uses() -> list[SdkAttributeUse]:
    uses: set[SdkAttributeUse] = set()
    service_classes = 0
    for path in sorted(SERVICES_DIR.glob("*.py")):
        module = ast.parse(path.read_text(), filename=str(path))
        for node in module.body:
            if isinstance(node, ast.ClassDef) and _is_base_service_subclass(node):
                service_classes += 1
                uses.update(_class_sdk_attribute_uses(path, node))

    # These lower bounds make accidental scanner breakage fail closed.
    assert service_classes >= 25
    assert len(uses) >= 150
    return sorted(uses)


def _real_service_instance(use: SdkAttributeUse, client: FoundryClient) -> object:
    module = import_module(f"pltr.services.{use.path.stem}")
    service_class = getattr(module, use.class_name)
    instance = object.__new__(service_class)
    instance._client = client
    return instance


def _resolve_attribute_chain(
    service: object, root_attribute: str, remainder: tuple[str, ...]
) -> None:
    current = getattr(service, root_attribute)
    for attribute in remainder:
        current = getattr(current, attribute)


def test_service_sdk_attribute_chains_exist_on_pinned_real_client() -> None:
    """Every statically expressed service SDK chain resolves on SDK 1.95.0."""

    assert version("foundry-platform-sdk") == PINNED_SDK_VERSION
    client = FoundryClient(
        auth=UserTokenAuth(token="sdk-contract-test-token"),
        hostname="https://sdk-contract.invalid",
    )

    failures: list[str] = []
    service_instances: dict[tuple[Path, str], object] = {}
    for use in _sdk_attribute_uses():
        service_key = (use.path, use.class_name)
        if service_key not in service_instances:
            service_instances[service_key] = _real_service_instance(use, client)
        service = service_instances[service_key]
        try:
            _resolve_attribute_chain(service, use.root_attribute, use.remainder)
        except AttributeError as error:
            failures.append(f"{use.location}: {use.source_expression} ({error})")

    if failures:
        pytest.fail(
            "Service code references attributes missing from the real installed "
            f"foundry-platform-sdk {PINNED_SDK_VERSION} client:\n"
            + "\n".join(failures),
            pytrace=False,
        )
