"""Structural contract: every subprocess.run(["autoskillit", ...]) call in the CLI
must inject AUTOSKILLIT_SKIP_STALE_CHECK into the child's environment.

This test prevents infinite re-entry loops where the child process re-runs the
stale check before install completes. Analogous to test_interactive_subprocess_contracts.py
which enforces terminal_guard() wrapping for all non-capturing subprocess calls.

Contract: each call site must satisfy BOTH conditions:
  1. An ``env=`` keyword argument is present in the call.
  2. The string literal ``AUTOSKILLIT_SKIP_STALE_CHECK`` appears somewhere in the
     same source file, confirming the env dict is built from the guard (the variable
     pattern ``_skip_env = {**os.environ, "AUTOSKILLIT_SKIP_STALE_CHECK": "1"}``
     satisfies this without requiring the literal to appear inside the call expression).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CLI_ROOT = Path(__file__).parents[2] / "src" / "autoskillit" / "cli"
REQUIRED_GUARD = "AUTOSKILLIT_SKIP_STALE_CHECK"


def _collect_autoskillit_subprocess_calls(source: str) -> list[tuple[int, str, bool]]:
    """Return (lineno, source_fragment, has_env_kwarg) for every
    subprocess.run(['autoskillit',...]) call."""
    tree = ast.parse(source)
    results = []
    lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_subprocess_call = (
            isinstance(func, ast.Attribute)
            and func.attr in ("run", "Popen")
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        )
        if not is_subprocess_call:
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.List) or not first_arg.elts:
            continue
        first_elt = first_arg.elts[0]
        if not (isinstance(first_elt, ast.Constant) and first_elt.value == "autoskillit"):
            continue
        end = getattr(node, "end_lineno", node.lineno)
        fragment = "\n".join(lines[node.lineno - 1 : end])
        has_env_kwarg = any(kw.arg == "env" for kw in node.keywords)
        results.append((node.lineno, fragment, has_env_kwarg))
    return results


def test_autoskillit_subprocess_calls_inject_skip_stale_check_guard() -> None:
    """All CLI subprocess.run(['autoskillit', ...]) calls must inject the skip-stale-check guard."""  # noqa: E501
    if not CLI_ROOT.is_dir():
        pytest.skip("Source tree unavailable")
    violations: list[str] = []
    for py_file in sorted(CLI_ROOT.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        if "autoskillit" not in source:
            continue
        calls = _collect_autoskillit_subprocess_calls(source)
        non_comment_source = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        guard_in_file = REQUIRED_GUARD in non_comment_source
        for lineno, fragment, has_env_kwarg in calls:
            if not has_env_kwarg or not guard_in_file:
                rel = py_file.relative_to(CLI_ROOT.parents[2])
                reason = []
                if not has_env_kwarg:
                    reason.append("missing env= kwarg")
                if not guard_in_file:
                    reason.append(f"'{REQUIRED_GUARD}' not found anywhere in file")
                violations.append(f"{rel}:{lineno} ({', '.join(reason)})\n  {fragment.strip()}")
    assert not violations, (
        f"Found {len(violations)} subprocess.run(['autoskillit', ...]) call(s) "
        f"not satisfying the env guard contract:\n\n" + "\n\n".join(violations)
    )
