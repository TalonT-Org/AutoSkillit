"""Ratchet the installed shell-capture lifecycle documented by ADR-0006."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION = REPO_ROOT / "docs/decisions/0006-output-containment.md"
OUTPUT_BUDGET_DECISION = REPO_ROOT / "docs/decisions/0005-output-budget-protocol.md"
SAFETY_GUIDE = REPO_ROOT / "docs/safety/hooks.md"

pytestmark = [pytest.mark.layer("docs"), pytest.mark.small]


@pytest.fixture(scope="module")
def lifecycle_docs() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in (DECISION, OUTPUT_BUDGET_DECISION, SAFETY_GUIDE)
    )


def test_decision_names_both_installed_cleanup_owners(lifecycle_docs: str) -> None:
    for required in (
        "valid runner invocation",
        "bounded tail sweep",
        "capture_lifecycle_hook.py",
        "cleanup-only",
        "SessionStart",
        "interactive and headless",
    ):
        assert required in lifecycle_docs


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
        "RETRY",
        "TAMPERED",
    ],
)
def test_decision_names_durable_lifecycle_states(
    lifecycle_docs: str,
    state: str,
) -> None:
    assert f"`{state}`" in lifecycle_docs


def test_decision_pins_liveness_retention_and_trigger_semantics(
    lifecycle_docs: str,
) -> None:
    for required in (
        "writer lease",
        "durable finalization",
        "one hour",
        "next enabled",
        "trusted trigger",
        "hooks are disabled",
        "eligible artifacts remain",
    ):
        assert required in lifecycle_docs


def test_decision_pins_allowlist_and_survivor_classes(lifecycle_docs: str) -> None:
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
        assert required in lifecycle_docs


def test_decision_limits_retry_and_deleted_byte_claims(lifecycle_docs: str) -> None:
    for required in (
        "bounded",
        "backoff",
        "backlog",
        "`deleted_bytes`",
        "logical managed bytes",
        "not evidence of physical block reclamation",
    ):
        assert required in lifecycle_docs


def test_decision_pins_durability_and_same_uid_boundary(lifecycle_docs: str) -> None:
    for required in (
        "process-termination recovery",
        "supported local Linux and macOS filesystems",
        "Darwin",
        "power-loss durability",
        "ordinary `fsync()`",
        "cooperative same-UID boundary",
        "hostile same-UID process",
    ):
        assert required in lifecycle_docs


def test_decision_keeps_future_features_outside_the_guarantee(
    lifecycle_docs: str,
) -> None:
    for issue in ("#4322", "#4325", "#4326", "#4327"):
        assert issue in lifecycle_docs
    assert "does not claim those features are implemented" in lifecycle_docs
    assert "features are not implemented by this lifecycle" in lifecycle_docs


def test_obsolete_cleanup_claims_are_removed(lifecycle_docs: str) -> None:
    for obsolete in (
        "SessionStart therefore retains stale candidates",
        "Artifact quota and lifecycle reclamation remain follow-up work",
        "SessionStart classifies stale artifacts but conservatively retains them",
    ):
        assert obsolete not in lifecycle_docs
