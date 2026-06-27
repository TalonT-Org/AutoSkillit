"""Meta-tests for the PreToolUse deny-mechanism strength matrix.

The matrix is written to ``.autoskillit/temp/hook_deny_strength_matrix.json``
by the probe harness conftest once probes complete. When the JSON is absent
(e.g., when only these meta-tests are collected), all three tests skip
gracefully — the probe suite must be run first to populate the matrix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY
from tests.execution.backends.conftest import EXPECTED_NON_INERT_COMBINATIONS

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


_MATRIX_PATH = (
    Path(__file__).resolve().parents[3]
    / ".autoskillit"
    / "temp"
    / "hook_deny_strength_matrix.json"
)


def _load_matrix() -> dict:
    """Load the serialized strength matrix, skipping if absent."""
    if not _MATRIX_PATH.exists():
        pytest.skip("probe suite was not run — matrix JSON absent")
    return json.loads(_MATRIX_PATH.read_text(encoding="utf-8"))


def test_matrix_combination_count() -> None:
    """The matrix must contain exactly EXPECTED_NON_INERT_COMBINATIONS rows.

    Inert probes return early without calling ``record_probe_row``, so the
    matrix holds only non-inert combinations.
    """
    matrix = _load_matrix()
    assert len(matrix["combinations"]) == EXPECTED_NON_INERT_COMBINATIONS


def test_works_as_is_hooks_have_soft_or_better() -> None:
    """For each works-as-is hook, at least one matrix row must be soft or hard.

    A "works-as-is" hook claims codex parity — its guard must produce a
    non-inert outcome (``soft`` or ``hard``) for at least one matrix row.
    """
    matrix = _load_matrix()
    rows = matrix["combinations"]

    works_as_is_stems: set[str] = {
        Path(hd.scripts[0]).stem for hd in HOOK_REGISTRY if hd.codex_status == "works-as-is"
    }
    seen_stems: set[str] = set()
    for row in rows:
        if row["hook"] in works_as_is_stems and row["strength"] in {"soft", "hard"}:
            seen_stems.add(row["hook"])

    missing = works_as_is_stems - seen_stems
    assert not missing, f"works-as-is hooks missing soft/hard rows: {missing}"


def test_not_applicable_hooks_appear_only_as_inert() -> None:
    """Not-applicable hooks must be absent from the matrix (inert probes are not recorded)."""
    matrix = _load_matrix()
    rows = matrix["combinations"]

    not_applicable_stems: set[str] = {
        Path(hd.scripts[0]).stem for hd in HOOK_REGISTRY if hd.codex_status == "not-applicable"
    }
    matrix_stems: set[str] = {row["hook"] for row in rows}
    leaked = not_applicable_stems & matrix_stems
    assert not leaked, f"not-applicable hooks appeared in matrix: {leaked}"
