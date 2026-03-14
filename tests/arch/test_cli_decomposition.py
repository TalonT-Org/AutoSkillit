"""AST-level tests enforcing CLI decomposition and hook security hardening."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"


# ── AST helpers ────────────────────────────────────────────────────────────────


def _is_bare_except_exception(node: ast.ExceptHandler) -> bool:
    """Return True if handler catches Exception broadly (not a narrowed tuple)."""
    if node.type is None:
        return True  # bare except:
    return isinstance(node.type, ast.Name) and node.type.id == "Exception"


def _body_is_only_sys_exit(node: ast.ExceptHandler) -> bool:
    """Return True if the handler body is solely sys.exit(0)."""
    if len(node.body) != 1:
        return False
    stmt = node.body[0]
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return False
    call = stmt.value
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "exit"
        and isinstance(func.value, ast.Name)
        and func.value.id == "sys"
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == 0
    )


# CD1
# cli/app.py is L3 and can import from every internal layer (L0–L2), which makes
# it the single easiest place for AI to dump new logic — it bypasses all layer
# restrictions that guard other modules.  This limit exists to keep that file
# decomposed.  Only a human may raise it beyond 750.
def test_app_py_under_line_limit():
    """cli/app.py must stay under the line limit to prevent monolith regrowth."""
    p = SRC_ROOT / "cli" / "app.py"
    lines = p.read_text().splitlines()
    assert len(lines) <= 750, (
        f"cli/app.py has {len(lines)} lines -- must be <=750; "
        "decompose into cli/ submodules instead of growing this file"
    )


# CD2
def test_unified_hook_helper_in_hooks_module():
    """cli/_hooks.py must define sync_hooks_to_settings (registry-driven registration)."""
    tree = ast.parse((SRC_ROOT / "cli" / "_hooks.py").read_text())
    fn_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "sync_hooks_to_settings" in fn_names


# CD3
def test_skill_command_guard_no_silent_broad_except():
    """CC-1: skill_command_guard.py must not have bare 'except Exception: sys.exit(0)'.
    The broad catch must either be narrowed or log before approving."""
    src = (SRC_ROOT / "hooks" / "skill_command_guard.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Broad Exception catch followed by ONLY sys.exit(0) is the violation
            if _is_bare_except_exception(node) and _body_is_only_sys_exit(node):
                pytest.fail(
                    "skill_command_guard.py has bare 'except Exception: sys.exit(0)' -- "
                    "CC-1 fix required: narrow scope or deny on unexpected errors"
                )


# CD4
def test_quota_check_no_silent_broad_except():
    """CC-2: quota_check.py must not have bare 'except Exception: sys.exit(0)'.
    Each except must be narrowed to specific errors or log before approving."""
    src = (SRC_ROOT / "hooks" / "quota_check.py").read_text()
    tree = ast.parse(src)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if _is_bare_except_exception(node) and _body_is_only_sys_exit(node):
                violations.append(node.lineno)
    assert not violations, (
        f"quota_check.py has silent broad except at lines {violations} -- CC-2 fix required"
    )
