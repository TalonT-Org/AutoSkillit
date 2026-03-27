"""Structural contract: every subprocess.run(["autoskillit", ...]) call in the CLI
must inject AUTOSKILLIT_SKIP_STALE_CHECK into the child's environment.

This test prevents infinite re-entry loops where the child process re-runs the
stale check before install completes. Analogous to test_interactive_subprocess_contracts.py
which enforces terminal_guard() wrapping for all non-capturing subprocess calls.
"""

from __future__ import annotations

import ast
from pathlib import Path

CLI_ROOT = Path(__file__).parents[2] / "src" / "autoskillit" / "cli"
REQUIRED_GUARD = "AUTOSKILLIT_SKIP_STALE_CHECK"


def _collect_autoskillit_subprocess_calls(source: str) -> list[tuple[int, str]]:
    """Return (lineno, source_fragment) for every subprocess.run(['autoskillit',...]) call."""
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
        results.append((node.lineno, fragment))
    return results


def test_autoskillit_subprocess_calls_inject_skip_stale_check_guard() -> None:
    """All subprocess.run(['autoskillit', ...]) calls in the CLI must pass env=
    containing AUTOSKILLIT_SKIP_STALE_CHECK. This prevents infinite re-entry
    loops where the child process re-runs the stale check before install completes."""
    violations: list[str] = []
    for py_file in sorted(CLI_ROOT.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        if "autoskillit" not in source:
            continue
        calls = _collect_autoskillit_subprocess_calls(source)
        for lineno, fragment in calls:
            if REQUIRED_GUARD not in fragment:
                rel = py_file.relative_to(CLI_ROOT.parents[2])
                violations.append(f"{rel}:{lineno}\n  {fragment.strip()}")
    assert not violations, (
        f"Found {len(violations)} subprocess.run(['autoskillit', ...]) call(s) "
        f"missing env= guard '{REQUIRED_GUARD}':\n\n" + "\n\n".join(violations)
    )
