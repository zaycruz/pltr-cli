"""Check skill reference command paths against the CLI grammar manifest."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE_DIR = PROJECT_ROOT / "skills" / "pltr-cli" / "reference"
MANIFEST_COMMAND = ("uv", "run", "pltr", "agent-manifest")
TOKEN_RE = re.compile(r""""[^"]*"|'[^']*'|`[^`]*`|[^\s]+""")
PLTR_RE = re.compile(r"(?<![\w-])pltr\b(?P<rest>[^\n`]*)")
COMMAND_TOKEN_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class DriftCheckError(RuntimeError):
    """Raised when the checker cannot load or validate its inputs."""


@dataclass(frozen=True)
class DriftReport:
    """The command-path differences found by the checker."""

    reference_paths: frozenset[str]
    manifest_paths: frozenset[str]
    reference_only: frozenset[str]
    manifest_only: frozenset[str]
    paths_by_file: Mapping[Path, frozenset[str]]

    @property
    def has_drift(self) -> bool:
        return bool(self.reference_only or self.manifest_only)


def load_manifest(path: Path) -> frozenset[str]:
    """Load command paths from a JSON manifest file."""
    if not path.is_file():
        raise DriftCheckError(
            f"Manifest file not found: {path}. "
            "Run `uv run pltr agent-manifest` or pass --manifest PATH."
        )

    try:
        payload = json.loads(path.read_text())
        commands = payload["commands"]
        paths = frozenset(command["path"] for command in commands)
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise DriftCheckError(f"Invalid command manifest {path}: {error}") from error

    if not all(isinstance(path, str) and path for path in paths):
        raise DriftCheckError(f"Invalid command paths in manifest {path}")
    return paths


def load_manifest_from_cli(project_root: Path = PROJECT_ROOT) -> frozenset[str]:
    """Run the authoritative CLI command and load its JSON output."""
    result = subprocess.run(
        MANIFEST_COMMAND,
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise DriftCheckError(
            "Unable to run `uv run pltr agent-manifest`"
            f" (exit {result.returncode}): {detail}"
        )

    try:
        payload = json.loads(result.stdout)
        paths = frozenset(command["path"] for command in payload["commands"])
    except (KeyError, TypeError, ValueError) as error:
        raise DriftCheckError(
            "`uv run pltr agent-manifest` did not return a valid JSON manifest: "
            f"{error}"
        ) from error

    if not all(isinstance(path, str) and path for path in paths):
        raise DriftCheckError(
            "`uv run pltr agent-manifest` returned invalid command paths"
        )
    return paths


def _tokens_after_pltr(line: str) -> list[str]:
    match = PLTR_RE.search(line)
    if match is None:
        return []
    return TOKEN_RE.findall(match.group("rest"))


def _fallback_path(tokens: Sequence[str]) -> str | None:
    """Recover a likely stale path when no manifest prefix matches."""
    path_tokens: list[str] = []
    for token in tokens:
        if (
            not COMMAND_TOKEN_RE.fullmatch(token)
            or token.startswith(("$", "[", "<"))
            or token.endswith(("...", "\\"))
        ):
            break
        path_tokens.append(token)
    if not path_tokens:
        return None
    return " ".join(path_tokens)


def _path_for_invocation(
    tokens: Sequence[str], manifest_paths: frozenset[str]
) -> str | None:
    candidates = [
        " ".join(tokens[:index])
        for index in range(1, len(tokens) + 1)
        if " ".join(tokens[:index]) in manifest_paths
    ]
    return max(candidates, key=len) if candidates else _fallback_path(tokens)


def extract_reference_paths(
    reference_dir: Path, manifest_paths: frozenset[str]
) -> tuple[frozenset[str], dict[Path, frozenset[str]]]:
    """Extract command paths and their source files from reference markdown."""
    if not reference_dir.is_dir():
        raise DriftCheckError(f"Reference directory not found: {reference_dir}")

    paths_by_file: dict[Path, frozenset[str]] = {}
    all_paths: set[str] = set()
    for document in sorted(reference_dir.glob("*.md")):
        paths: set[str] = set()
        for line in document.read_text().splitlines():
            tokens = _tokens_after_pltr(line)
            path = _path_for_invocation(tokens, manifest_paths)
            if path is not None:
                paths.add(path)
        paths_by_file[document] = frozenset(paths)
        all_paths.update(paths)
    return frozenset(all_paths), paths_by_file


def check_drift(reference_dir: Path, manifest_paths: frozenset[str]) -> DriftReport:
    """Compare reference command paths with manifest command paths."""
    reference_paths, paths_by_file = extract_reference_paths(
        reference_dir, manifest_paths
    )
    return DriftReport(
        reference_paths=reference_paths,
        manifest_paths=manifest_paths,
        reference_only=reference_paths - manifest_paths,
        manifest_only=manifest_paths - reference_paths,
        paths_by_file=paths_by_file,
    )


def _relative_document(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def format_report(report: DriftReport) -> str:
    """Render a human-readable drift report."""
    lines = [
        "Skill command drift report",
        f"Reference paths: {len(report.reference_paths)}",
        f"Manifest paths: {len(report.manifest_paths)}",
        f"Reference-only paths: {len(report.reference_only)}",
        f"Manifest-only paths: {len(report.manifest_only)}",
    ]

    if report.reference_only:
        lines.append("Reference-only paths by document:")
        for document, paths in sorted(
            report.paths_by_file.items(), key=lambda item: str(item[0])
        ):
            drift = sorted(paths & report.reference_only)
            if drift:
                lines.append(f"  {_relative_document(document)} ({len(drift)}):")
                lines.extend(f"    - {path}" for path in drift)
    else:
        lines.append("Reference-only paths: none")

    if report.manifest_only:
        lines.append("Manifest-only paths:")
        lines.extend(f"  - {path}" for path in sorted(report.manifest_only))
    else:
        lines.append("Manifest-only paths: none")

    if report.has_drift:
        lines.append("Result: drift detected (exit 1)")
    else:
        lines.append("Result: no drift detected")
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=DEFAULT_REFERENCE_DIR,
        help="Directory containing reference markdown files.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Use a JSON manifest fixture instead of invoking uv run pltr agent-manifest.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        manifest_paths = (
            load_manifest(args.manifest)
            if args.manifest is not None
            else load_manifest_from_cli()
        )
        report = check_drift(args.reference_dir, manifest_paths)
    except DriftCheckError as error:
        print(f"Drift check failed: {error}", file=sys.stderr)
        return 1

    print(format_report(report))
    return 1 if report.has_drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
