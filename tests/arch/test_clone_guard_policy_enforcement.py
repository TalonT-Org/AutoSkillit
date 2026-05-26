"""Enforcement tests for CloneGuardPolicy usage — prevents regressions."""

from __future__ import annotations

import ast
import inspect

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_no_raw_readonly_skill_in_clone_guard_call():
    """check_and_revert_clone_contamination must never be called with readonly_skill=."""
    import autoskillit.execution.headless._headless_execute as mod

    source = inspect.getsource(mod)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name == "check_and_revert_clone_contamination":
                for kw in node.keywords:
                    assert kw.arg != "readonly_skill", (
                        f"check_and_revert_clone_contamination called with readonly_skill= "
                        f"at line {node.lineno} — must use policy= instead"
                    )


def test_check_and_revert_has_no_readonly_skill_param():
    """The function signature must not accept readonly_skill (only policy)."""
    from autoskillit.execution.clone_guard import check_and_revert_clone_contamination

    sig = inspect.signature(check_and_revert_clone_contamination)
    assert "readonly_skill" not in sig.parameters, (
        "check_and_revert_clone_contamination still has readonly_skill parameter — "
        "it must accept policy: CloneGuardPolicy instead"
    )
    assert "policy" in sig.parameters, (
        "check_and_revert_clone_contamination missing policy parameter"
    )
