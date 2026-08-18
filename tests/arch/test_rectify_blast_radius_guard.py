"""Layer-count blast-radius gate for rectify plan output (#4684 Fix G / 2.9).

Three consecutive rectify-adjacent changes to the same function/module
(PR #4503 -> #4512 -> #4613/#4684) shipped with no CI signal that the
underlying plan touched multiple import layers at once. PR #4613's plan
touched >=3 layers across 141 files with no gate to flag it for explicit
reviewer attention.

``.autoskillit/temp/`` is gitignored (``.autoskillit/.gitignore:1``) — a scan
of the directory's *current on-disk contents* would pass vacuously in a
fresh CI clone, since nothing under a gitignored path persists across
clones. The only scan that can ever observe a *committed* plan file is a
``git diff`` against the parent commit, so that is what this guard uses.
In the common case (no rectify plan file was ever added/modified in the
most recent commit — true for this very worktree, since the driving plan
lives outside version control per the gitignore) the integration test below
passes vacuously too; the enforcement value is structural — it fires
whenever a rectify plan file *is* committed in a single commit — and the
unit tests pin the counting/classification logic independent of git state.
"""

from __future__ import annotations

import re
import subprocess
import warnings
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Matches the IL-N (single digit) import-layer notation documented in
# AGENTS.md's disambiguation table. Deliberately excludes IL-NNN (three-digit)
# import-linter contract IDs (e.g. IL-001) -- \b after the digit stops the
# match before a second digit, so "IL-001" does not contribute "IL-0".
_IL_LAYER_RE = re.compile(r"\bIL-([0-3])\b")

_SIGN_OFF_THRESHOLD = 3
_DECOMPOSITION_THRESHOLD = 5


def _distinct_layers(text: str) -> set[str]:
    """Return the set of distinct IL-0..IL-3 import-layer tokens in text."""
    return {f"IL-{n}" for n in _IL_LAYER_RE.findall(text)}


def _classify(layer_count: int) -> str:
    """Classify a plan's layer count against the sign-off/decomposition thresholds."""
    if layer_count > _DECOMPOSITION_THRESHOLD:
        return "decompose"
    if layer_count > _SIGN_OFF_THRESHOLD:
        return "sign_off"
    return "ok"


def _changed_rectify_plans() -> list[Path]:
    """Rectify plan .md files added/modified in the most recent commit."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "--", ".autoskillit/temp/rectify/*.md"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    paths = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        candidate = _REPO_ROOT / line
        if candidate.is_file():
            paths.append(candidate)
    return paths


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("No layer references here.", set()),
        ("Touches IL-0 and IL-2 and IL-2 again.", {"IL-0", "IL-2"}),
        ("References IL-001 (a contract ID, not a layer) only.", set()),
        ("IL-0, IL-1, IL-2, IL-3 all appear.", {"IL-0", "IL-1", "IL-2", "IL-3"}),
    ],
)
def test_distinct_layers_extraction(text: str, expected: set[str]) -> None:
    assert _distinct_layers(text) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, "ok"), (3, "ok"), (4, "sign_off"), (5, "sign_off"), (6, "decompose")],
)
def test_classify_thresholds(count: int, expected: str) -> None:
    assert _classify(count) == expected


def test_no_committed_rectify_plan_exceeds_the_decomposition_threshold() -> None:
    """Any rectify plan committed in the most recent commit must not exceed
    the >5-distinct-import-layer decomposition threshold. Plans in the 4-5
    range are surfaced as a pytest warning (explicit reviewer check), not a
    hard failure -- only the >5 case blocks."""
    violations = []
    warned = []
    for plan_path in _changed_rectify_plans():
        layers = _distinct_layers(plan_path.read_text(encoding="utf-8"))
        classification = _classify(len(layers))
        relpath = plan_path.relative_to(_REPO_ROOT)
        if classification == "decompose":
            violations.append(
                f"{relpath} touches {len(layers)} import layers ({sorted(layers)}) -- "
                f"exceeds the >{_DECOMPOSITION_THRESHOLD} decomposition threshold. "
                "Split the plan per layer."
            )
        elif classification == "sign_off":
            warned.append(
                f"{relpath} touches {len(layers)} import layers ({sorted(layers)}) -- "
                f"exceeds the >{_SIGN_OFF_THRESHOLD} sign-off threshold. "
                "Requires explicit reviewer check."
            )
    for message in warned:
        warnings.warn(message, stacklevel=1)
    assert not violations, "\n".join(violations)
