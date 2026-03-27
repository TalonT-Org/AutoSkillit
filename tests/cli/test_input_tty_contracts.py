"""
Structural enforcement: every input() call in src/autoskillit/cli/ must be
preceded by _require_interactive_stdin(), or the function must be in the
allowlist (_TTY_EXEMPT_FUNCTIONS).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Functions exempt from the _require_interactive_stdin() contract.
# Two allowlist categories:
# - call-site-guarded: only called from contexts that already guarantee isatty()
# - custom-handled: implement their own non-interactive path (return a result,
#   not SystemExit) — cannot use _require_interactive_stdin by design
_TTY_EXEMPT_FUNCTIONS: frozenset[str] = frozenset(
    {
        "_prompt_github_repo",  # call-site-guarded: only caller (_register_all) wraps in isatty()
        "_check_secret_scanning",  # custom-handled: returns _ScanResult(False) non-interactively
        "run_onboarding_menu",  # custom-handled: catches EOFError on each input()
        "run_stale_check",  # custom-handled: guards with isatty() check at entry
    }
)

_CLI_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "cli"


def _has_tty_guard_before_input(func_body: list[ast.stmt]) -> bool:
    """Return True if the function body contains a _require_interactive_stdin() call.

    The canonical TTY guard is exclusively _require_interactive_stdin(). Inline
    isatty() checks are NOT accepted — they are too permissive: a function with
    multiple input() calls passes even when only one call site is guarded, and
    the enforcement silently breaks when the guard is removed.
    """
    for node in ast.walk(ast.Module(body=func_body, type_ignores=[])):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "_require_interactive_stdin":
                return True
    return False


def test_all_input_calls_in_cli_are_tty_guarded() -> None:
    """Every function in src/autoskillit/cli/ that calls input() must call
    _require_interactive_stdin() in its body, OR be listed in _TTY_EXEMPT_FUNCTIONS.

    This prevents silent EOFError crashes in non-interactive environments and makes
    the class of bugs in issue #470 structurally impossible to re-introduce.
    """
    violations: list[str] = []

    for py_file in sorted(_CLI_DIR.rglob("*.py")):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            func_name = node.name
            if func_name in _TTY_EXEMPT_FUNCTIONS:
                continue
            # Check if this function contains any input() call
            has_input = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "input"
                for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
            )
            if not has_input:
                continue
            # Function has input() — verify TTY guard
            if not _has_tty_guard_before_input(node.body):
                rel_path = py_file.relative_to(Path("src"))
                violations.append(
                    f"{rel_path}:{node.lineno}: {func_name}() calls input() "
                    f"without _require_interactive_stdin()"
                )

    assert not violations, (
        "The following CLI functions call input() without _require_interactive_stdin().\n"
        "Add _require_interactive_stdin(context) at the start of each function,\n"
        "or add the function to _TTY_EXEMPT_FUNCTIONS with a justification comment:\n\n"
        + "\n".join(f"  • {v}" for v in violations)
    )


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
    from autoskillit.cli._workspace import run_workspace_clean

    runs_dir = tmp_path / "autoskillit-runs"
    runs_dir.mkdir()
    entry = runs_dir / "stale-run"
    entry.mkdir()
    entry_mtime = entry.stat().st_mtime
    monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: entry_mtime + 20_000)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc_info:
        run_workspace_clean(dir=str(tmp_path), force=False)
    assert exc_info.value.code == 1
