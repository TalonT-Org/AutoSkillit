"""AST guard: Channel B tests must use timeout >= TimeoutTier.CHANNEL_B.

Prevents the timeout=15 class of flaky-test bug by scanning run_managed_async
call sites and enforcing minimum timeout values for Channel B tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.conftest import TimeoutTier

TESTS_ROOT = Path(__file__).parent.parent
CHANNEL_B_TEST_FILE = TESTS_ROOT / "execution" / "test_process_channel_b.py"

# Tests that deliberately use short timeouts to provoke TIMED_OUT behavior.
# Each entry: (function_name, timeout_value, rationale)
EXEMPTIONS: dict[str, tuple[int | float, str]] = {
    "test_drain_window_times_out_when_no_session_jsonl": (
        10,
        "Deliberately short timeout with completion_drain_timeout=0.2; "
        "no session JSONL written, so Channel B never fires.",
    ),
}

_CHANNEL_B_DRAIN_THRESHOLD = 0.3  # completion_drain_timeout below this is not Channel B


def _extract_run_managed_async_calls(
    tree: ast.Module,
) -> list[tuple[str, int, dict[str, ast.expr]]]:
    """Extract (enclosing_function_name, lineno, keyword_dict) for run_managed_async calls."""
    results: list[tuple[str, int, dict[str, ast.expr]]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        func_name = node.name
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            # Match run_managed_async(... or await run_managed_async(...)
            callee = child.func
            name = None
            if isinstance(callee, ast.Name):
                name = callee.id
            elif isinstance(callee, ast.Attribute):
                name = callee.attr
            if name != "run_managed_async":
                continue
            kw_dict = {kw.arg: kw.value for kw in child.keywords if kw.arg is not None}
            results.append((func_name, child.lineno, kw_dict))

    return results


def _is_channel_b_call(kw_dict: dict[str, ast.expr]) -> bool:
    """Compound heuristic: session_log_dir + completion_marker + drain >= threshold."""
    has_session_log_dir = "session_log_dir" in kw_dict
    has_completion_marker = "completion_marker" in kw_dict
    drain_node = kw_dict.get("completion_drain_timeout")
    if not (has_session_log_dir and has_completion_marker and drain_node is not None):
        return False
    if not isinstance(drain_node, ast.Constant):
        return True  # Non-literal drain — conservatively treat as Channel B
    return drain_node.value >= _CHANNEL_B_DRAIN_THRESHOLD


class TestChannelBTimeoutGuard:
    """AST guard: Channel B run_managed_async calls must have timeout >= CHANNEL_B tier."""

    def test_all_channel_b_calls_meet_minimum_timeout(self) -> None:
        source = CHANNEL_B_TEST_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(CHANNEL_B_TEST_FILE))
        calls = _extract_run_managed_async_calls(tree)
        violations: list[str] = []

        for func_name, lineno, kw_dict in calls:
            if not _is_channel_b_call(kw_dict):
                continue

            # Check exemption
            if func_name in EXEMPTIONS:
                continue

            timeout_node = kw_dict.get("timeout")
            if timeout_node is None:
                violations.append(f"  {func_name} (line {lineno}): no timeout= keyword")
                continue

            # timeout must be a literal (ast.Constant) or an attribute (TimeoutTier.CHANNEL_B)
            if isinstance(timeout_node, ast.Constant):
                if timeout_node.value < TimeoutTier.CHANNEL_B:
                    violations.append(
                        f"  {func_name} (line {lineno}): timeout={timeout_node.value} "
                        f"< TimeoutTier.CHANNEL_B ({TimeoutTier.CHANNEL_B})"
                    )
            elif isinstance(timeout_node, ast.Attribute):
                # TimeoutTier.CHANNEL_B — accepted by name
                pass
            else:
                violations.append(
                    f"  {func_name} (line {lineno}): timeout is not a literal or "
                    f"TimeoutTier attribute — cannot statically verify"
                )

        assert not violations, (
            f"Channel B tests with timeout below {TimeoutTier.CHANNEL_B}s:\n"
            + "\n".join(violations)
        )

    def test_timeout_tier_constants(self) -> None:
        """TimeoutTier values encode the documented budget math."""
        assert TimeoutTier.UNIT == 10
        assert TimeoutTier.INTEGRATION == 30
        assert TimeoutTier.CHANNEL_B == 60

    def test_exemptions_are_still_present(self) -> None:
        """Guard against stale exemptions — each exempted function must still exist."""
        source = CHANNEL_B_TEST_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(CHANNEL_B_TEST_FILE))
        func_names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        stale = set(EXEMPTIONS.keys()) - func_names
        assert not stale, f"Stale exemptions (functions no longer exist): {stale}"
