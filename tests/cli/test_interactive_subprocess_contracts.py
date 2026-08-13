"""Structural enforcement: all CLI subprocess.run calls that inherit the
terminal (no capture_output, no stdout=PIPE/DEVNULL) must be wrapped in
a terminal_guard() context manager.

Follows the same AST-walk pattern as test_input_tty_contracts.py.
"""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

CLI_DIR = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "cli"

# Files that contain no subprocess.run calls — skip for clarity
# (test will skip automatically if no subprocess.run found in source)

# subprocess.run calls with these keyword args are capturing (non-interactive)
# and are exempt from the terminal_guard() requirement.
_EXEMPT_KWARGS = frozenset({"capture_output", "stdout"})


def _is_capturing_call(call_node: ast.Call) -> bool:
    """Return True if this subprocess.run call captures or redirects stdout."""
    for kw in call_node.keywords:
        if kw.arg in _EXEMPT_KWARGS:
            return True
    return False


def _collect_violations(source: str, filename: str) -> list[int]:
    """Return line numbers of non-capturing subprocess.run calls outside terminal_guard().

    Parses the module AST and tracks entry/exit into `with terminal_guard():`
    context manager blocks. Any subprocess.run call found outside such a block
    that does not use capture_output or redirect stdout is a violation.
    """
    tree = ast.parse(source, filename=filename)
    violations: list[int] = []

    class GuardTracker(ast.NodeVisitor):
        def __init__(self) -> None:
            self._guard_depth = 0

        def visit_With(self, node: ast.With) -> None:
            entered = False
            for item in node.items:
                ctx = item.context_expr
                if (
                    isinstance(ctx, ast.Call)
                    and isinstance(ctx.func, ast.Name)
                    and ctx.func.id == "terminal_guard"
                ):
                    entered = True
            if entered:
                self._guard_depth += 1
            self.generic_visit(node)
            if entered:
                self._guard_depth -= 1

        def visit_Call(self, node: ast.Call) -> None:
            is_subprocess_run = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
            )
            if is_subprocess_run and not _is_capturing_call(node) and self._guard_depth == 0:
                violations.append(node.lineno)
            self.generic_visit(node)

    GuardTracker().visit(tree)
    return violations


def test_terminal_guard_never_emits_smcup_on_entry() -> None:
    """_terminal.py must not contain the smcup escape sequence (?1049h).

    terminal_guard() is an exit-only safety net. It emits ?1049l (rmcup)
    in its finally block as a safety net for abnormal subprocess exits, but
    must NEVER emit ?1049h (smcup) on entry. DECSET 1049 is a boolean toggle
    with no nesting counter — emitting it before a TUI subprocess launch
    (e.g. Claude Code Ink) overwrites the DECSC cursor save point and corrupts
    the TUI's viewport layout.

    This source-scan guard complements the mock-based behavioral test
    (test_does_not_emit_entry_alt_screen_sequence) and catches any future PR
    that attempts to re-add entry-side alt-screen sequences.

    Regression guard for: investigation_terminal_guard_alt_screen_scrollbar
    See: test_interactive_subprocess_calls_wrapped_in_terminal_guard for the
    analogous structural guard on subprocess call sites.
    """
    terminal_py = CLI_DIR / "ui" / "_terminal.py"
    source = terminal_py.read_text()
    assert "?1049h" not in source, (
        f"{terminal_py.name} must not emit \\033[?1049h (smcup). "
        "terminal_guard() is an exit-only cleanup safety net. "
        "The subprocess (e.g. Claude Code Ink TUI) is the sole owner of "
        "alt-screen entry. See: test_does_not_emit_entry_alt_screen_sequence "
        "for the behavioral guard."
    )


@pytest.mark.parametrize("py_file", sorted(CLI_DIR.rglob("*.py")))
def test_interactive_subprocess_calls_wrapped_in_terminal_guard(py_file: Path) -> None:
    """Every non-capturing subprocess.run call in cli/ must be inside terminal_guard().

    This test is the structural immune system for the terminal raw-mode bug class
    (GitHub Issue #509). It prevents any future interactive subprocess.run call
    from being added to the CLI layer without terminal state management.

    If this test fails with your change:
        1. You added a subprocess.run call in a CLI module without capture_output=True
        2. Wrap it: `with terminal_guard(): result = subprocess.run(...)`
        3. Import: `from autoskillit.cli.ui._terminal import terminal_guard`
    """
    source = py_file.read_text()
    if "subprocess.run" not in source:
        pytest.skip(f"{py_file.name}: no subprocess.run calls")

    violations = _collect_violations(source, str(py_file))
    assert violations == [], (
        f"\n\n{py_file.name}: interactive subprocess.run found at line(s) "
        f"{violations} without terminal_guard() wrapper.\n\n"
        f"Fix: wrap with `with terminal_guard():` and import from "
        f"`autoskillit.cli.ui._terminal`.\n\n"
        f"See: tests/cli/test_input_tty_contracts.py for the analogous pattern."
    )


def _calls_in(node: ast.AST, *, owner: str, attr: str) -> list[ast.Call]:
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == owner
        and call.func.attr == attr
    ]


def _named_calls_in(node: ast.AST, *, name: str) -> list[ast.Call]:
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == name
    ]


def _attributes_in(node: ast.AST, *, owner: str, attr: str) -> list[ast.Attribute]:
    return [
        attribute
        for attribute in ast.walk(node)
        if isinstance(attribute, ast.Attribute)
        and isinstance(attribute.value, ast.Name)
        and attribute.value.id == owner
        and attribute.attr == attr
    ]


def _with_context_calls(node: ast.AST, *, name: str) -> list[ast.With]:
    return [
        with_node
        for with_node in ast.walk(node)
        if isinstance(with_node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == name
            for item in with_node.items
        )
    ]


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1, f"expected exactly one {name}() definition"
    return matches[0]


def test_cook_attempt_uses_only_the_shared_spawn_bound_owner() -> None:
    session_dir = CLI_DIR / "session"
    violations: list[str] = []
    for path in sorted(session_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if path.name not in {"_session_process.py", "_session_launch.py"} and _calls_in(
            tree, owner="subprocess", attr="Popen"
        ):
            violations.append(path.name)

    assert violations == []
    process_path = session_dir / "_session_process.py"
    process_tree = ast.parse(process_path.read_text(encoding="utf-8"), filename=str(process_path))
    run_attempt = _function(process_tree, "run_cook_attempt")
    assert _calls_in(run_attempt, owner="subprocess", attr="Popen") == []
    spawn_calls = _named_calls_in(run_attempt, name="spawn_owned_process")
    assert len(spawn_calls) == 2
    terminal_guard = _with_context_calls(run_attempt, name="terminal_guard")[0]
    assert terminal_guard.end_lineno is not None
    assert all(
        terminal_guard.lineno < call.lineno <= terminal_guard.end_lineno for call in spawn_calls
    )

    cook_source = (session_dir / "_session_cook.py").read_text(encoding="utf-8")
    assert "run_cook_attempt(" in cook_source
    assert "subprocess.run(" not in cook_source
    assert "subprocess.Popen(" not in cook_source

    shared_launch_source = (session_dir / "_session_launch.py").read_text(encoding="utf-8")
    assert "run_cook_attempt(" in shared_launch_source
    assert "subprocess.run(" in shared_launch_source, (
        "fleet and nonpersistent backends retain the raw interactive process owner"
    )


def test_cook_owner_spawn_returns_before_the_single_spawn_callback() -> None:
    process_path = CLI_DIR / "session" / "_session_process.py"
    tree = ast.parse(process_path.read_text(encoding="utf-8"), filename=str(process_path))
    run_attempt = _function(tree, "run_cook_attempt")
    spawn_calls = _named_calls_in(run_attempt, name="spawn_owned_process")
    on_spawn_calls = [
        call
        for call in ast.walk(run_attempt)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "on_spawn"
    ]

    assert len(on_spawn_calls) == 1
    assert max(call.lineno for call in spawn_calls) < on_spawn_calls[0].lineno


def test_cook_pty_path_has_no_unsafe_post_fork_python_setup() -> None:
    session_dir = CLI_DIR / "session"
    paths = [
        session_dir / "_session_cook.py",
        session_dir / "_session_process.py",
        session_dir / "pty" / "_observer.py",
        session_dir / "pty" / "_exec.py",
    ]
    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            if any(keyword.arg == "preexec_fn" for keyword in call.keywords):
                violations.append(f"{path.name}:{call.lineno}:preexec_fn")
            if (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "pty"
                and call.func.attr == "fork"
            ):
                violations.append(f"{path.name}:{call.lineno}:pty.fork")

    exec_source = paths[-1].read_text(encoding="utf-8")
    assert "setsid(" not in exec_source
    assert "login_tty(" not in exec_source
    assert violations == []


def test_terminal_and_lease_ownership_are_not_duplicated_across_pty_layers() -> None:
    session_dir = CLI_DIR / "session"
    process_path = session_dir / "_session_process.py"
    observer_path = session_dir / "pty" / "_observer.py"
    process_tree = ast.parse(
        process_path.read_text(encoding="utf-8"),
        filename=str(process_path),
    )
    observer_tree = ast.parse(
        observer_path.read_text(encoding="utf-8"),
        filename=str(observer_path),
    )
    run_attempt = _function(process_tree, "run_cook_attempt")

    assert len(_with_context_calls(run_attempt, name="terminal_guard")) == 1
    assert _calls_in(run_attempt, owner="subprocess", attr="Popen") == []
    assert len(_named_calls_in(run_attempt, name="spawn_owned_process")) == 2
    assert _calls_in(process_tree, owner="os", attr="tcsetpgrp")
    assert _attributes_in(process_tree, owner="fcntl", attr="LOCK_UN") == []
    assert not any(
        isinstance(node, ast.Name) and node.id == "LOCK_UN" for node in ast.walk(process_tree)
    )
    assert _calls_in(observer_tree, owner="os", attr="tcsetpgrp") == []
    assert _calls_in(observer_tree, owner="subprocess", attr="Popen") == []


def test_order_fleet_launch_path_owns_one_guarded_popen() -> None:
    launch_path = CLI_DIR / "session" / "_session_launch.py"
    tree = ast.parse(launch_path.read_text(encoding="utf-8"), filename=str(launch_path))
    run_interactive = _function(tree, "_run_interactive_session")
    terminal_guards = _with_context_calls(run_interactive, name="terminal_guard")

    assert len(_calls_in(run_interactive, owner="subprocess", attr="Popen")) == 1
    assert len(terminal_guards) == 1
    assert len(_calls_in(terminal_guards[0], owner="subprocess", attr="Popen")) == 1
    assert _calls_in(run_interactive, owner="subprocess", attr="run") == []
