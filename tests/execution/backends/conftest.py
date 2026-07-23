"""Probe harness for PreToolUse deny-mechanism guard efficacy.

Provides shared constants, registry-derived count invariants, and xdist-safe
accumulation that ferries probe rows from workers to the controller via the
``workeroutput`` IPC channel under the dedicated key ``"hook_probe_rows"`` —
distinct from the root conftest's ``"filter_selected"`` / ``"filter_deselected"``
keys, so filter-stats IPC is unaffected.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY


@pytest.fixture
def update_fixtures(request: pytest.FixtureRequest) -> bool:
    """Expose --update-fixtures CLI flag as a fixture."""
    return bool(request.config.getoption("--update-fixtures"))


# Local mirror of _SKIP_CODEX_STATUSES from autoskillit.execution.backends._codex_hooks.
# Defined here so the conftest has no import dependency on execution.backends (which
# would create a tests/ -> execution/ IL cycle for the backends subdirectory).
_SKIP_CODEX: frozenset[str] = frozenset({"fix-required", "not-applicable"})

TOOL_CLASSES: dict[str, str] = {
    "Bash": "Bash",
    "Write": "Write",
    "Edit": "Edit",
    "Grep": "Grep",
    "AskUserQuestion": "AskUserQuestion",
    "mcp_run_skill": "mcp__autoskillit__run_skill",
    "mcp_run_cmd": "mcp__autoskillit__run_cmd",
    "mcp_run_python": "mcp__autoskillit__run_python",
    "mcp_open_kitchen": "mcp__autoskillit__open_kitchen",
    "mcp_remove_clone": "mcp__autoskillit__remove_clone",
    "mcp_merge_worktree": "mcp__autoskillit__merge_worktree",
    "mcp_push_to_remote": "mcp__autoskillit__push_to_remote",
    "mcp_dispatch_food_truck": "mcp__autoskillit__dispatch_food_truck",
    "mcp_wait_for_ci": "mcp__autoskillit__wait_for_ci",
    "mcp_reset_dispatch": "mcp__autoskillit__reset_dispatch",
    "apply_patch": "apply_patch",
}

SESSION_MODES: tuple[str, ...] = ("interactive", "headless-p", "subagent")
BACKENDS: tuple[str, ...] = ("claude-code", "codex")


def _compute_total_probe_count() -> int:
    """Total parametrized combinations (including inert codex rows).

    Mirrors the cross-product that ``_collect_probe_params()`` yields:
    for each PreToolUse+deny hook with at least one matching tool class,
    every (script, matching tool class, session mode, backend) tuple counts.
    """
    count = 0
    for hd in HOOK_REGISTRY:
        if hd.event_type != "PreToolUse" or hd.mechanism != "deny":
            continue
        matching = sum(1 for t in TOOL_CLASSES.values() if re.fullmatch(hd.matcher, t))
        if matching == 0:
            continue
        count += matching * len(hd.scripts) * len(SESSION_MODES) * len(BACKENDS)
    return count


def _compute_expected_non_inert() -> int:
    """Non-inert combinations only (matrix rows that get recorded).

    A combination is inert when backend=="codex" AND the hook would be
    excluded from ``generate_codex_hooks_config()`` — i.e.,
    ``session_scope=="interactive_only"`` OR ``codex_status`` in
    ``{"fix-required", "not-applicable"}``. This mirrors the filtering at
    ``_codex_hooks.py:52-55``.
    """
    count = 0
    for hd in HOOK_REGISTRY:
        if hd.event_type != "PreToolUse" or hd.mechanism != "deny":
            continue
        matching = sum(1 for t in TOOL_CLASSES.values() if re.fullmatch(hd.matcher, t))
        if matching == 0:
            continue
        for _ in hd.scripts:
            for be in BACKENDS:
                if be == "codex" and (
                    hd.session_scope == "interactive_only" or hd.codex_status in _SKIP_CODEX
                ):
                    continue
                count += matching * len(SESSION_MODES)
    return count


EXPECTED_TOTAL_PROBE_COUNT: int = _compute_total_probe_count()
EXPECTED_NON_INERT_COMBINATIONS: int = _compute_expected_non_inert()

# xdist-safe probe row accumulation -------------------------------------------------
#
# Each xdist worker has its own ``_probe_rows`` list (separate process). Workers
# forward their accumulated rows to the controller via the built-in
# ``workeroutput`` IPC channel under the key ``"hook_probe_rows"``. The
# controller merges in ``pytest_testnodedown`` and writes the serialized
# matrix in ``pytest_sessionfinish``.

_probe_rows: list[dict] = []
_controller_rows: list[dict] = []
_controller_probe_suite_collected = False


def record_probe_row(row: dict) -> None:
    """Append a probe row to the current worker's accumulator."""
    _probe_rows.append(row)


def validate_strength_matrix(rows: list[dict]) -> list[str]:
    """Return invariant failures for a completed deny-strength matrix."""

    failures: list[str] = []
    if len(rows) != EXPECTED_NON_INERT_COMBINATIONS:
        failures.append(
            f"expected {EXPECTED_NON_INERT_COMBINATIONS} combinations, got {len(rows)}"
        )

    works_as_is = {
        Path(script).stem
        for hook in HOOK_REGISTRY
        if hook.event_type == "PreToolUse"
        and hook.mechanism == "deny"
        and hook.codex_status == "works-as-is"
        for script in hook.scripts
    }
    effective = {
        row["hook"]
        for row in rows
        if row["hook"] in works_as_is and row["strength"] in {"soft", "hard"}
    }
    if missing := works_as_is - effective:
        failures.append(f"works-as-is hooks missing soft/hard rows: {sorted(missing)}")

    not_applicable = {
        Path(script).stem
        for hook in HOOK_REGISTRY
        if hook.codex_status == "not-applicable"
        for script in hook.scripts
    }
    if leaked := not_applicable & {row["hook"] for row in rows}:
        failures.append(f"not-applicable hooks appeared in matrix: {sorted(leaked)}")
    return failures


def _full_probe_suite_collected(items: list[pytest.Item]) -> bool:
    probe_count = sum(getattr(item, "originalname", "") == "test_probe" for item in items)
    return probe_count == EXPECTED_TOTAL_PROBE_COUNT


def pytest_testnodedown(node, error):
    """Controller-side: harvest rows from a finishing worker."""
    global _controller_probe_suite_collected

    wo = getattr(node, "workeroutput", {})
    rows = wo.get("hook_probe_rows", [])
    _controller_rows.extend(rows)
    _controller_probe_suite_collected |= bool(wo.get("hook_probe_suite_collected", False))


def pytest_sessionfinish(session, exitstatus):
    """Worker-side: ship rows to controller; controller-side: serialize matrix."""
    if hasattr(session.config, "workerinput"):
        # xdist worker: send accumulated rows to controller via workeroutput IPC.
        session.config.workeroutput["hook_probe_rows"] = _probe_rows
        session.config.workeroutput["hook_probe_suite_collected"] = _full_probe_suite_collected(
            session.items
        )
        return
    # Controller (or non-xdist run): write matrix JSON.
    all_rows = _controller_rows or _probe_rows
    if not all_rows:
        return
    project_dir = Path(__file__).resolve().parents[3]
    out_dir = project_dir / ".autoskillit" / "temp"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "hook_deny_strength_matrix.json"
    out_path.write_text(
        json.dumps({"combinations": all_rows}, indent=2),
        encoding="utf-8",
    )
    full_probe_suite = _controller_probe_suite_collected or _full_probe_suite_collected(
        session.items
    )
    if full_probe_suite and (failures := validate_strength_matrix(all_rows)):
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_sep("=", "hook deny strength matrix validation failed")
            for failure in failures:
                reporter.write_line(failure)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
