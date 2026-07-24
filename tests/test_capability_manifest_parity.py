"""Guard: the capability scorecard cannot drift from the real command surface.

`pltr agent-manifest` emits the commands that exist. `pltr capabilities` scores
this CLI against Palantir's published MCP tool catalog. They used to be
maintained independently, so the scorecard claimed 4 tools implemented while
235 commands actually shipped. Status is now derived from the live command
surface; these tests fail if that derivation is ever bypassed or the two views
disagree.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from pltr.capabilities import (
    VALID_STATUSES,
    capability_manifest,
    registered_command_paths,
)
from pltr.cli import app

runner = CliRunner()


def test_implemented_capabilities_name_a_real_command():
    """Every implemented capability points at a command agent-manifest lists."""
    paths = registered_command_paths()
    manifest = capability_manifest()
    offenders = [
        c["capability_id"]
        for c in manifest["capabilities"]
        if c["status"] == "implemented" and c["command"] not in paths
    ]
    assert not offenders, (
        f"capabilities marked implemented but the command does not exist: {offenders}"
    )


def test_planned_capabilities_do_not_name_an_existing_command():
    """A command that exists must read implemented, never planned."""
    paths = registered_command_paths()
    manifest = capability_manifest()
    ghosts = [
        c["capability_id"]
        for c in manifest["capabilities"]
        if c["status"] == "planned" and c["command"] in paths
    ]
    assert not ghosts, (
        f"capabilities marked planned even though the command already exists: {ghosts}"
    )


def test_blocked_and_unsupported_carry_a_reason():
    """A non-buildable status without a reason is a dead end for an agent."""
    manifest = capability_manifest()
    missing = [
        c["capability_id"]
        for c in manifest["capabilities"]
        if c["status"] in {"blocked", "unsupported"} and not c["blocked_reason"]
    ]
    assert not missing, f"blocked/unsupported without a reason: {missing}"


def test_every_status_is_valid_and_counts_are_exact():
    manifest = capability_manifest()
    caps = manifest["capabilities"]
    for c in caps:
        assert c["status"] in VALID_STATUSES, c
    counts = manifest["counts"]
    assert counts["total"] == len(caps)
    for status in ("implemented", "planned", "blocked", "unsupported"):
        assert counts[status] == sum(c["status"] == status for c in caps)


def test_the_two_commands_agree_at_runtime():
    """The binary's own agent-manifest and capabilities must not contradict."""
    with patch("pltr.auth.storage.CredentialStorage", MagicMock()):
        surface = runner.invoke(
            app, ["--agent", "agent-manifest"], catch_exceptions=True
        )
        scorecard = runner.invoke(
            app, ["--agent", "capabilities"], catch_exceptions=True
        )

    paths = {c["path"] for c in json.loads(surface.stdout)["data"]["commands"]}
    caps = json.loads(scorecard.stdout)["data"]["capabilities"]
    # implemented must exist; planned must not. blocked/unsupported are exempt:
    # they may name a real fallback command (e.g. `namespace list` stands in for
    # a namespace listing the SDK cannot do) while the capability itself is not
    # at parity.
    disagreements = [
        c["capability_id"]
        for c in caps
        if (c["status"] == "implemented" and c["command"] not in paths)
        or (c["status"] == "planned" and c["command"] in paths)
    ]
    assert not disagreements, (
        "capabilities and agent-manifest disagree on what is implemented: "
        f"{disagreements}"
    )
