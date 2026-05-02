"""AST-based structural test for post-session operation ordering in headless.py.

T-ORD-1: _compute_loc_changed must not be called before _build_skill_result
         in _execute_claude_headless. After the fix, the call is wrapped in
         _compute_post_session_metrics which is invoked after _build_skill_result.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_HEADLESS_PATH = (
    Path(__file__).parent.parent.parent / "src" / "autoskillit" / "execution" / "headless.py"
)


def _find_first_call_line(func_body: list[ast.stmt], call_name: str) -> int | None:
    """Return the first line number of a direct Call to call_name in func_body.

    Walks all nodes under each statement in source order and returns the first
    matching call's line number, or None if no match.
    """
    for node in ast.walk(ast.Module(body=func_body, type_ignores=[])):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == call_name:
                return node.lineno
    return None


# T-ORD-1
def test_compute_loc_changed_called_after_build_skill_result():
    """_compute_loc_changed must not appear before _build_skill_result in _execute_claude_headless.

    After the fix the direct call to _compute_loc_changed is replaced by
    _compute_post_session_metrics, which must itself appear after _build_skill_result.
    """
    source = _HEADLESS_PATH.read_text()
    tree = ast.parse(source)

    target_func: ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute_claude_headless":
            target_func = node
            break

    assert target_func is not None, "_execute_claude_headless not found in headless.py"

    build_result_line = _find_first_call_line(target_func.body, "_build_skill_result")
    assert build_result_line is not None, (
        "_build_skill_result call not found in _execute_claude_headless"
    )

    # Direct call to _compute_loc_changed must not appear before _build_skill_result.
    # After the fix, it should not appear at all in _execute_claude_headless (it is
    # delegated to _compute_post_session_metrics).
    loc_changed_line = _find_first_call_line(target_func.body, "_compute_loc_changed")
    if loc_changed_line is not None:
        assert loc_changed_line > build_result_line, (
            f"_compute_loc_changed (line {loc_changed_line}) must appear after "
            f"_build_skill_result (line {build_result_line}) in _execute_claude_headless. "
            "LoC measurement must use the constructed SkillResult to resolve effective_cwd."
        )

    # _compute_post_session_metrics must appear after _build_skill_result.
    metrics_line = _find_first_call_line(target_func.body, "_compute_post_session_metrics")
    assert metrics_line is not None, (
        "_compute_post_session_metrics not found in _execute_claude_headless — "
        "the post-session metrics factory must be wired up."
    )
    assert metrics_line > build_result_line, (
        f"_compute_post_session_metrics (line {metrics_line}) must appear after "
        f"_build_skill_result (line {build_result_line}) in _execute_claude_headless."
    )
