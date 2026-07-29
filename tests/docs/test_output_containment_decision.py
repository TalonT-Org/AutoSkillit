"""Ratchet ADR-0006 as the historical output-containment decision."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION = REPO_ROOT / "docs/decisions/0006-output-containment.md"

pytestmark = [pytest.mark.layer("docs"), pytest.mark.small]


@pytest.fixture(scope="module")
def decision_text() -> str:
    return DECISION.read_text(encoding="utf-8")


def test_decision_names_both_installed_cleanup_owners(decision_text: str) -> None:
    for required in (
        "valid runner invocation",
        "bounded tail sweep",
        "capture_lifecycle_hook.py",
        "cleanup-only",
        "SessionStart",
        "interactive and headless",
    ):
        assert required in decision_text


@pytest.mark.parametrize(
    "state",
    [
        "RESERVED",
        "STAGED",
        "PUBLISHED_WRITING",
        "FINALIZED",
        "FAILED",
        "ABANDONED",
        "DELETING",
        "DELETED",
        "TAMPERED",
    ],
)
def test_decision_names_durable_lifecycle_states(
    decision_text: str,
    state: str,
) -> None:
    assert f"`{state}`" in decision_text


def test_decision_pins_liveness_retention_and_trigger_semantics(
    decision_text: str,
) -> None:
    for required in (
        "[ADR-0008](0008-shell-capture-snapshot-authority.md)",
        "one hour",
        "next enabled",
        "trusted trigger",
        "hooks are disabled",
        "eligible artifacts remain",
    ):
        assert required in decision_text


def test_decision_pins_allowlist_and_survivor_classes(decision_text: str) -> None:
    for required in (
        "shell_[0-9a-f]{16}.log",
        "Fresh records",
        "live writers",
        "nonmatching names",
        "symlinks",
        "FIFOs",
        "hardlinks",
        "world-writable files",
        "identity replacements",
        "tampered",
    ):
        assert required in decision_text


def test_decision_limits_retry_and_deleted_byte_claims(decision_text: str) -> None:
    for required in (
        "bounded",
        "backoff",
        "backlog",
        "`deleted_bytes`",
        "logical managed bytes",
        "not evidence of physical block reclamation",
    ):
        assert required in decision_text


def test_decision_pins_durability_and_same_uid_boundary(decision_text: str) -> None:
    for required in (
        "process-termination recovery",
        "supported local Linux and macOS filesystems",
        "Darwin",
        "power-loss durability",
        "ordinary `fsync()`",
        "cooperative same-UID boundary",
        "hostile same-UID process",
    ):
        assert required in decision_text


def test_decision_delegates_snapshot_authority_without_duplication(
    decision_text: str,
) -> None:
    assert "[ADR-0008](0008-shell-capture-snapshot-authority.md)" in decision_text
    assert "normative contract" in decision_text
    assert "opaque V2 reference" in decision_text
    assert "never an authoritative pathname" in decision_text
    for delegated_issue in ("#4322", "#4325", "#4326", "#4327"):
        assert delegated_issue not in decision_text


def test_obsolete_cleanup_claims_are_removed(decision_text: str) -> None:
    for obsolete in (
        "SessionStart therefore retains stale candidates",
        "Artifact quota and lifecycle reclamation remain follow-up work",
        "SessionStart classifies stale artifacts but conservatively retains them",
        "`disown` is the fire-and-forget escape",
        "marker reports its path",
        "path is present",
    ):
        assert obsolete not in decision_text
