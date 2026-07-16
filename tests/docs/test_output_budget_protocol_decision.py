"""Ratchet the accepted Output Budget Protocol decision record."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION = REPO_ROOT / "docs/decisions/0005-output-budget-protocol.md"
DECISION_INDEX = REPO_ROOT / "docs/decisions/README.md"

pytestmark = [pytest.mark.layer("docs"), pytest.mark.small]


@pytest.fixture(scope="module")
def decision_text() -> str:
    assert DECISION.exists(), "ADR-0005 must exist"
    return DECISION.read_text(encoding="utf-8")


def test_output_budget_decision_is_indexed(decision_text: str) -> None:
    assert "**Status:** Accepted" in decision_text
    assert "**Date:** 2026-07-15" in decision_text
    assert "#4272" in decision_text
    assert "0005-output-budget-protocol.md" in DECISION_INDEX.read_text(encoding="utf-8")


def test_decision_owns_independent_backend_limits(decision_text: str) -> None:
    assert "MAX_MCP_OUTPUT_TOKENS" in decision_text
    assert "CODEX_TOOL_OUTPUT_TOKEN_LIMIT" in decision_text
    assert "no shared source of truth" in decision_text


@pytest.mark.parametrize(
    "required",
    [
        "Lossless source shaping",
        "Universal response backstop",
        "Pre-spend command guard",
        "Producer-aware discipline and derived transport ceiling",
    ],
)
def test_decision_names_all_four_layers(decision_text: str, required: str) -> None:
    assert required in decision_text


@pytest.mark.parametrize(
    "required",
    [
        "max_chars = 179_000",
        "max_utf8_bytes = 179_000",
        "178,601 characters and UTF-8 bytes",
        "max_chars = 180_000",
        "max_utf8_bytes = 180_000",
        "178,660 characters and UTF-8 bytes",
        "((180_000 + 3) // 4) + 8_000",
        "CODEX_TOOL_OUTPUT_TOKEN_LIMIT = 53_000",
        "CODEX_AUTO_COMPACT_LIMIT = 999_999_999",
        "inline_max_chars = 5_000",
        "response_max_bytes = 90_000",
        "MAX_MCP_OUTPUT_TOKENS = 50_000",
    ],
)
def test_decision_pins_numeric_rationales(decision_text: str, required: str) -> None:
    assert required in decision_text


def test_decision_pins_ceiling_backstop_pair(decision_text: str) -> None:
    assert "rust-v0.144.1" in decision_text
    assert "44918ea10c0f99151c6710411b4322c2f5c96bea" in decision_text
    assert "codex-rs/utils/string/src/truncate.rs" in decision_text
    assert "one-token-per-four-UTF-8-bytes" in decision_text
    assert "response_max_bytes // 3 < CODEX_TOOL_OUTPUT_TOKEN_LIMIT" in decision_text
    assert "open_kitchen" in decision_text
    assert "load_recipe" in decision_text
    assert "live large-output probe" in decision_text
    assert "RESPONSE_BACKSTOP_EXEMPTION_REGISTRY" in decision_text
    assert "canonical digest" in decision_text
    assert "bundled-recipes-all-modes-2026-07-15/load-recipe" in decision_text
    assert "bundled-recipes-all-modes-2026-07-15/open-kitchen" in decision_text


def test_decision_records_both_corrections(decision_text: str) -> None:
    correction = decision_text.split("## Corrections of Record", maxsplit=1)[1].split(
        "## Accepted Gaps", maxsplit=1
    )[0]
    for required in ["6b421e38e", "native shell", "unified_exec", "PR #4259", "develop", "main"]:
        assert required in correction


def test_decision_names_all_eight_accepted_gaps(decision_text: str) -> None:
    gaps = decision_text.split("## Accepted Gaps", maxsplit=1)[1].split(
        "## Operational Signals", maxsplit=1
    )[0]
    numbered = re.findall(r"^\d+\. ", gaps, flags=re.MULTILINE)
    assert len(numbered) == 8
    for required in [
        "Non-JSONL single-file searches",
        "Codex `resume`",
        "artifact persistence is unavailable",
        "Prompt-side caps",
        "`merge_worktree` drops passing raw test output",
        "pruned collection",
        "cumulative context",
        "worker memory",
    ]:
        assert required in gaps


def test_decision_limits_operational_signal_claims(decision_text: str) -> None:
    signals = decision_text.split("## Operational Signals", maxsplit=1)[1].split(
        "## Forward Obligations", maxsplit=1
    )[0]
    for required in [
        "spill count",
        "original and artifact UTF-8 byte totals",
        "measured exemption use",
        "spill failures grouped by bounded cause code",
        "must never contain artifact paths, hashes, or output content",
        "artifact quota",
        "artifact cleanup",
        "cumulative reserve accounting",
    ]:
        assert required in signals


def test_decision_ratchets_forward_obligations(decision_text: str) -> None:
    obligations = decision_text.split("## Forward Obligations", maxsplit=1)[1].split(
        "## Consequences", maxsplit=1
    )[0]
    for required in [
        "after CLI upgrades",
        "ceiling relaxation",
        "command-guard rule removal",
        "response-backstop exemption",
        "output-discipline policy-version change",
        "invalidates the applicable cached",
        "live large-output probe",
    ]:
        assert required in obligations
