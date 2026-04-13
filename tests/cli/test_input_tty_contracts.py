"""
Structural enforcement: every input() call in src/autoskillit/cli/ must go
through timed_prompt() in _timed_input.py, or the function must be in the
allowlist (_RAW_INPUT_EXEMPT_FILES).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Files that are *allowed* to contain raw input() calls.
# _timed_input.py is the sole module that wraps input() with timeout/TTY/ANSI.
_RAW_INPUT_EXEMPT_FILES: frozenset[str] = frozenset(
    {
        "_timed_input.py",  # the prompt primitive itself
    }
)

_CLI_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "cli"


# ---------------------------------------------------------------------------
# Test 1a — no raw input() outside _timed_input.py
# ---------------------------------------------------------------------------


def test_all_cli_prompts_use_timed_prompt_or_are_exempt() -> None:
    """No function in src/autoskillit/cli/ may call input() directly except
    those inside files listed in _RAW_INPUT_EXEMPT_FILES.

    All user-facing prompts must go through timed_prompt() which composes
    TTY guard, ANSI formatting, and timeout into a single call.
    """
    violations: list[str] = []

    for py_file in sorted(_CLI_DIR.rglob("*.py")):
        if py_file.name in _RAW_INPUT_EXEMPT_FILES:
            continue
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        rel_path = py_file.relative_to(Path("src"))

        # Check inside function/method bodies
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            func_name = node.name
            has_input = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "input"
                for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
            )
            if has_input:
                violations.append(
                    f"{rel_path}:{node.lineno}: {func_name}() calls raw input() — "
                    f"use timed_prompt() from _timed_input.py instead"
                )

        # Check module-level code (outside any function or class)
        for stmt in tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for n in ast.walk(stmt):
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id == "input"
                ):
                    violations.append(
                        f"{rel_path}:{n.lineno}: <module-level> calls raw input() — "
                        f"use timed_prompt() from _timed_input.py instead"
                    )

    assert not violations, (
        "The following CLI functions call input() directly instead of timed_prompt().\n"
        "All user-facing prompts must use timed_prompt() which composes TTY guard,\n"
        "ANSI formatting, and select.select timeout into a single call:\n\n"
        + "\n".join(f"  • {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# _require_interactive_stdin behavioural tests (kept for regression coverage)
# ---------------------------------------------------------------------------


def test_require_interactive_stdin_raises_system_exit_when_not_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """_require_interactive_stdin raises SystemExit(1) with a clear message
    when sys.stdin.isatty() returns False."""
    from autoskillit.cli._init_helpers import _require_interactive_stdin

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc_info:
        _require_interactive_stdin("autoskillit init")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "non-interactive" in captured.out.lower() and "autoskillit init" in captured.out


def test_require_interactive_stdin_is_noop_when_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_require_interactive_stdin is a no-op when sys.stdin.isatty() returns True."""
    from autoskillit.cli._init_helpers import _require_interactive_stdin

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    _require_interactive_stdin("autoskillit init")  # must not raise


def test_prompt_recipe_choice_noninteractive_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_prompt_recipe_choice() must raise SystemExit(1) in non-interactive mode."""
    from autoskillit.cli._init_helpers import _prompt_recipe_choice

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc_info:
        _prompt_recipe_choice()
    assert exc_info.value.code == 1


def test_cook_noninteractive_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cook() launch-confirm prompt must raise SystemExit(1) when not interactive."""
    from autoskillit.cli._cook import cook

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc_info:
        cook()
    assert exc_info.value.code == 1


def test_run_workspace_clean_noninteractive_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """workspace clean prompt must raise SystemExit(1) when not interactive and
    force=False. Requires stale entries to exist so the confirmation input() is reached."""
    import asyncio

    from autoskillit.cli._workspace import run_workspace_clean

    runs_dir = tmp_path / "autoskillit-runs"
    runs_dir.mkdir()
    entry = runs_dir / "stale-run"
    entry.mkdir()
    entry_mtime = entry.stat().st_mtime
    monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: entry_mtime + 20_000)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(run_workspace_clean(dir=str(tmp_path), force=False))
    assert exc_info.value.code == 1
