"""Architectural enforcement: AST-based visitor rules (ARCH-001 through ARCH-007).

Rules enforced here (compile-time, no execution required):
  1. No print() calls in production code
  2. No sensitive keyword arguments passed to logger calls
  3. No broad except without logger call or re-raise
  4. asyncio.PIPE ban outside process.py
  5. get_logger() must be called with __name__
  6. No f-string interpolation of sensitive variables in logger positional args
  7. Exhaustive TerminationReason dispatch (match/case + assert_never)

Note: `import logging` and `logging.getLogger()` are enforced by ruff TID251
at pre-commit time (see pyproject.toml [tool.ruff.lint.flake8-tidy-imports]).
Those rules belong in the toolchain, not duplicated here.

Exemptions:
  - cli/app.py, cli/_doctor.py, cli/_cook.py: may use print() for user-facing terminal output
"""

from __future__ import annotations

import ast
import hashlib
import os
import sys
from pathlib import Path

import pytest

from tests.arch._helpers import (
    _SOURCE_FILES,
    SRC_ROOT,
    _scan,
)
from tests.arch._rules import (
    _DISPATCH_TABLE_EXEMPT_FUNCTIONS,
    RuleDescriptor,
    _rel,
)


def _check_termination_dispatch_exhaustive(src_dir: Path) -> list[str]:
    """
    ARCH-007: Detect functions that dispatch over TerminationReason via if/elif
    chains (dispatch tables) rather than exhaustive match/case + assert_never.

    A "dispatch table" is detected when a single FunctionDef contains comparisons
    to >=2 distinct TerminationReason.* values (including values inside tuple
    membership tests like `termination in (TerminationReason.X, TerminationReason.Y)`).
    A single comparison (guard) is exempt. Functions in
    _DISPATCH_TABLE_EXEMPT_FUNCTIONS are also exempt.

    Returns a list of violation strings for failing tests.
    """
    violations = []
    for py_file in src_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in _DISPATCH_TABLE_EXEMPT_FUNCTIONS:
                continue
            # Collect all TerminationReason.VALUE names compared with == or in
            tr_values: set[str] = set()
            has_assert_never = False
            has_match = False
            for child in ast.walk(node):
                # Detect: termination == TerminationReason.SOME_VALUE
                # and: termination in (TerminationReason.X, TerminationReason.Y)
                if isinstance(child, ast.Compare):
                    for comparator in child.comparators:
                        if (
                            isinstance(comparator, ast.Attribute)
                            and isinstance(comparator.value, ast.Name)
                            and comparator.value.id == "TerminationReason"
                        ):
                            tr_values.add(comparator.attr)
                        elif isinstance(comparator, ast.Tuple):
                            # Handle: termination in (TerminationReason.X, TerminationReason.Y)
                            for elt in comparator.elts:
                                if (
                                    isinstance(elt, ast.Attribute)
                                    and isinstance(elt.value, ast.Name)
                                    and elt.value.id == "TerminationReason"
                                ):
                                    tr_values.add(elt.attr)
                # Detect match statements (Python 3.10+: ast.Match)
                if hasattr(ast, "Match") and isinstance(child, ast.Match):
                    has_match = True
                # Detect assert_never calls
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "assert_never"
                ):
                    has_assert_never = True
            # Dispatch table = >=2 distinct TerminationReason values checked
            if len(tr_values) >= 2 and not (has_match and has_assert_never):
                violations.append(
                    f"{py_file.relative_to(src_dir.parent.parent)}:{node.lineno}: "
                    f"{node.name}() dispatches on {tr_values} via if/elif -- "
                    f"use match/case + assert_never"
                )
    return violations


def _find_enclosing_function(node: ast.AST, tree: ast.AST) -> str | None:
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(parent):
                if child is node:
                    return parent.name
    return None


# ── Tests ─────────────────────────────────────────────────────────────────────

NO_WRITE_TEXT_RULE = RuleDescriptor(
    rule_id="REQ-AST-002",
    name="no-direct-write-text-in-src",
    lens="error-resilience",
    description=(
        "No src/autoskillit/ file may call .write_text() or .write_bytes() directly; "
        "use _atomic_write() from autoskillit.core.io."
    ),
    rationale=(
        "Non-atomic writes produce corrupted JSON when two concurrent recipe steps "
        "interleave writes to the same file. _atomic_write() uses a temp-file + rename "
        "pattern that is crash-safe and O_EXCL-safe on both Linux and macOS."
    ),
    exemptions=frozenset(),
    severity="error",
    defense_standard="DS-001",
)


def test_no_direct_write_text_in_src() -> None:
    """No src/autoskillit/ file may call .write_text() or .write_bytes() directly.

    All persistent file writes must use _atomic_write() from autoskillit.core.io to
    ensure crash-safe atomic semantics. This prevents the race condition where two
    concurrent writers produce corrupted JSON by interleaving a non-atomic write.
    """
    import ast as _ast

    src_root = Path(__file__).parent.parent.parent / "src" / "autoskillit"
    violations: list[str] = []
    for py_file in sorted(src_root.rglob("*.py")):
        try:
            tree = _ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in _ast.walk(tree):
            if (
                isinstance(node, _ast.Call)
                and isinstance(node.func, _ast.Attribute)
                and node.func.attr in {"write_text", "write_bytes"}
            ):
                rel = py_file.relative_to(src_root)
                violations.append(f"  {rel}:{node.lineno}")
    assert not violations, (
        "Direct path.write_text/write_bytes calls found in src/autoskillit/.\n"
        "Use atomic_write(path, content) from autoskillit.core.io instead:\n"
        + "\n".join(violations)
    )


def test_tmp_path_is_ram_backed(tmp_path: Path) -> None:
    """On Linux/WSL2, tmp_path must resolve to /dev/shm (RAM-backed tmpfs).

    On macOS no assertion is made -- disk-backed /tmp is acceptable there.
    Fails intentionally on Linux when pytest is invoked directly without --basetemp.
    Always run tests via 'task test-all', not pytest directly.
    """
    if sys.platform == "linux":
        path_str = str(tmp_path)
        assert path_str.startswith("/dev/shm"), (
            f"tmp_path ({path_str!r}) is not in /dev/shm. "
            "Run tests via 'task test-all', which passes "
            "--basetemp=/dev/shm/pytest-tmp."
        )


def test_tmp_path_has_worktree_hash(tmp_path: Path) -> None:
    """tmp_path must contain a .ROOT_DIR-derived hash to prevent cross-worktree collision.

    Fails when pytest is invoked with --basetemp=/dev/shm/pytest-tmp (static path).
    Passes only when Taskfile.yml derives PYTEST_TMPDIR from .ROOT_DIR via the
    slim-sprig sha256sum template function.
    """
    if sys.platform == "linux":
        cwd_hash = hashlib.sha256(os.getcwd().encode()).hexdigest()[:8]
        path_str = str(tmp_path)
        assert f"pytest-tmp-{cwd_hash}" in path_str, (
            f"tmp_path ({path_str!r}) does not contain the expected worktree hash "
            f"'{cwd_hash}'. PYTEST_TMPDIR must be derived from .ROOT_DIR. "
            f"Expected /dev/shm/pytest-tmp-{cwd_hash} as the base. "
            "Update Taskfile.yml PYTEST_TMPDIR to use a .ROOT_DIR-derived hash suffix "
            "(use slim-sprig: {{ substr 0 8 (sha256sum .ROOT_DIR) }})."
        )


class TestArchitectureEnforcement:
    """Parametrized AST checks over every .py file in src/autoskillit/."""

    @pytest.mark.parametrize(
        "source_file",
        _SOURCE_FILES,
        ids=[_rel(f) for f in _SOURCE_FILES],
    )
    def test_no_violations(self, source_file: Path) -> None:
        violations = _scan(source_file)
        if violations:
            report = "\n".join(f"  {v}" for v in violations)
            pytest.fail(
                f"Architectural violations in {_rel(source_file)}:\n{report}",
                pytrace=False,
            )


# ── ARCH-003 calibration tests ────────────────────────────────────────────────


def test_broad_except_exception_without_log_is_violation(tmp_path: Path) -> None:
    """Rule 3: except Exception: pass with no logger call must be flagged."""
    f = tmp_path / "bad.py"
    f.write_text("try:\n    pass\nexcept Exception:\n    pass\n")
    violations = _scan(f)
    assert violations, "Expected violation for broad except Exception without logger"
    messages = " ".join(v.message for v in violations)
    assert "except" in messages.lower()
    assert "logger" in messages.lower()


def test_broad_except_base_exception_without_log_is_violation(tmp_path: Path) -> None:
    """Rule 3: except BaseException: pass with no logger call must be flagged."""
    f = tmp_path / "bad.py"
    f.write_text("try:\n    pass\nexcept BaseException:\n    pass\n")
    violations = _scan(f)
    assert violations, "Expected violation for broad except BaseException without logger"


def test_bare_except_without_log_is_violation(tmp_path: Path) -> None:
    """Rule 3: bare except: pass with no logger call must be flagged."""
    f = tmp_path / "bad.py"
    f.write_text("try:\n    pass\nexcept:\n    pass\n")
    violations = _scan(f)
    assert violations, "Expected violation for bare except without logger"


def test_broad_except_with_log_call_is_not_violation(tmp_path: Path) -> None:
    """Rule 3: except Exception with a logger call is not a violation."""
    f = tmp_path / "ok.py"
    f.write_text(
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "try:\n"
        "    pass\n"
        "except Exception:\n"
        "    logger.warning('failed')\n"
    )
    violations = _scan(f)
    except_violations = [v for v in violations if "except" in v.message.lower()]
    assert not except_violations, f"Unexpected except violation: {except_violations}"


def test_specific_except_without_log_is_not_violation(tmp_path: Path) -> None:
    """Rule 3: except OSError (specific type) without logger is not a violation."""
    f = tmp_path / "ok.py"
    f.write_text("try:\n    pass\nexcept OSError:\n    pass\n")
    violations = _scan(f)
    except_violations = [v for v in violations if "except" in v.message.lower()]
    assert not except_violations, f"Unexpected except violation: {except_violations}"


def test_broad_except_with_reraise_is_not_violation(tmp_path: Path) -> None:
    """Rule 3: except Exception with unconditional re-raise is not a violation."""
    f = tmp_path / "ok.py"
    f.write_text("try:\n    pass\nexcept Exception:\n    raise\n")
    violations = _scan(f)
    except_violations = [v for v in violations if "except" in v.message.lower()]
    assert not except_violations, f"Unexpected except violation: {except_violations}"


# ── ARCH-004/005/006 calibration tests ───────────────────────────────────────


def test_asyncio_pipe_ban_detects_violation(tmp_path: Path) -> None:
    f = tmp_path / "some_module.py"
    f.write_text("import asyncio\nval = asyncio.PIPE\n")
    violations = _scan(f)
    assert any("asyncio.PIPE" in v.message for v in violations)


def test_asyncio_pipe_ban_exempt_in_process(tmp_path: Path) -> None:
    f = tmp_path / "process.py"
    f.write_text("import asyncio\nval = asyncio.PIPE\n")
    violations = _scan(f)
    assert not any("asyncio.PIPE" in v.message for v in violations)


def test_get_logger_name_enforcement_detects_literal(tmp_path: Path) -> None:
    f = tmp_path / "some.py"
    f.write_text("from autoskillit._logging import get_logger\nlogger = get_logger('mymodule')\n")
    violations = _scan(f)
    assert any("get_logger" in v.message for v in violations)


def test_fstring_secret_detects_token_var(tmp_path: Path) -> None:
    f = tmp_path / "some.py"
    f.write_text("logger.info(f'Using {token}')\n")
    violations = _scan(f)
    assert any("token" in v.message for v in violations)


def test_fstring_secret_safe_for_nonsensitive(tmp_path: Path) -> None:
    f = tmp_path / "some.py"
    f.write_text("logger.info(f'Count: {count}')\n")
    violations = _scan(f)
    assert not any("f-string" in v.message for v in violations)


# ── ARCH-007 test ──────────────────────────────────────────────────────────────


def test_arch007_termination_dispatch_tables_use_exhaustive_match() -> None:
    """
    ARCH-007: Any function in execution/ that dispatches on >=2 distinct
    TerminationReason values via if/elif must use match/case with assert_never.
    Single-value guard checks (e.g., `if termination == TIMED_OUT:`) are exempt.
    """
    violations = _check_termination_dispatch_exhaustive(SRC_ROOT / "execution")
    assert violations == [], (
        "Non-exhaustive TerminationReason dispatch tables found:\n" + "\n".join(violations)
    )


def _check_channel_confirmation_dispatch_exhaustive(src_dir: Path) -> list[str]:
    """
    T7 / ARCH-007 extension: Detect functions that dispatch over ChannelConfirmation
    via if/elif chains rather than exhaustive match/case + assert_never.

    A "dispatch table" is detected when a single FunctionDef contains comparisons
    to >=2 distinct ChannelConfirmation.* values (CHANNEL_A, CHANNEL_B, UNMONITORED).
    A single-value guard is exempt.

    Returns a list of violation strings for failing tests.
    """
    violations = []
    for py_file in src_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            cc_values: set[str] = set()
            has_assert_never = False
            has_match = False
            for child in ast.walk(node):
                if isinstance(child, ast.Compare):
                    for comparator in child.comparators:
                        if (
                            isinstance(comparator, ast.Attribute)
                            and isinstance(comparator.value, ast.Name)
                            and comparator.value.id == "ChannelConfirmation"
                        ):
                            cc_values.add(comparator.attr)
                        elif isinstance(comparator, ast.Tuple):
                            for elt in comparator.elts:
                                if (
                                    isinstance(elt, ast.Attribute)
                                    and isinstance(elt.value, ast.Name)
                                    and elt.value.id == "ChannelConfirmation"
                                ):
                                    cc_values.add(elt.attr)
                if hasattr(ast, "Match") and isinstance(child, ast.Match):
                    has_match = True
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "assert_never"
                ):
                    has_assert_never = True
            if len(cc_values) >= 2 and not (has_match and has_assert_never):
                violations.append(
                    f"{py_file.relative_to(src_dir.parent.parent)}:{node.lineno}: "
                    f"{node.name}() dispatches on {cc_values} via if/elif -- "
                    f"use match/case + assert_never"
                )
    return violations


def test_arch007_channel_confirmation_dispatch_uses_match_case() -> None:
    """
    T7 / ARCH-007 extension: Any function in execution/ that dispatches on >=2
    distinct ChannelConfirmation values via if/elif must use match/case with
    assert_never. Single-value guard checks are exempt.
    """
    violations = _check_channel_confirmation_dispatch_exhaustive(SRC_ROOT / "execution")
    assert violations == [], (
        "Non-exhaustive ChannelConfirmation dispatch tables found:\n" + "\n".join(violations)
    )


def test_no_raw_claude_list_construction() -> None:
    """No list literal starting with 'claude' may be constructed outside the ALLOWED set.

    Enforces that all claude command construction goes through the canonical
    builders in execution/commands.py, preventing ad-hoc command assembly
    that bypasses established safety flags.
    """
    ALLOWED = {
        ("_marketplace.py", "install"),
        ("_cook.py", "cook"),
        ("_llm_triage.py", "_triage_batch"),
        ("commands.py", "build_interactive_cmd"),
        ("commands.py", "build_headless_cmd"),
        ("commands.py", "build_headless_resume_cmd"),
        ("_init_helpers.py", "_is_plugin_installed"),
        ("_doctor.py", "_check_mcp_server_registered"),
    }
    violations: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.List)
                and node.elts
                and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == "claude"
            ):
                fn_name = _find_enclosing_function(node, tree)
                if (path.name, fn_name) not in ALLOWED:
                    violations.append(
                        f"{path.relative_to(SRC_ROOT.parent.parent)}:{node.lineno}: "
                        f"raw ['claude', ...] list in {path.name}:{fn_name or '<module>'}"
                    )
    assert not violations, (
        "Raw ['claude', ...] list construction found outside allowed locations:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ── P14-2: Sub-package __init__.py facade enforcement ─────────────────────────


def _type_checking_linenos(tree: ast.AST) -> set[int]:
    """Return line numbers of all AST nodes inside `if TYPE_CHECKING:` guards."""
    linenos: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_guard = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if is_guard:
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    linenos.add(child.lineno)  # type: ignore[attr-defined]
    return linenos


HOOKS_STDLIB_RULE = RuleDescriptor(
    rule_id="REQ-AST-001",
    name="hooks-are-stdlib-only",
    lens="security",
    description=(
        "Hook scripts in src/autoskillit/hooks/ must not import from autoskillit.* "
        "at runtime; they execute outside the venv."
    ),
    rationale=(
        "Claude Code hook scripts run in a subprocess without the autoskillit "
        "venv active. Any autoskillit.* import at runtime causes an ImportError "
        "that silently kills the hook. Only stdlib imports are safe. Imports inside "
        "TYPE_CHECKING blocks are annotation-only and never executed."
    ),
    exemptions=frozenset({"TYPE_CHECKING"}),
    severity="error",
    defense_standard="DS-001",
)


def test_hooks_are_stdlib_only() -> None:
    """Hook scripts must not import from autoskillit.* — they run outside the venv.

    Exemption: imports inside `if TYPE_CHECKING:` blocks are annotation-only and
    are never executed at runtime, so they do not break the stdlib-only constraint.
    """
    hooks_dir = SRC_ROOT / "hooks"
    violations: list[str] = []
    for py_file in sorted(hooks_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text())
        exempt = _type_checking_linenos(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("autoskillit")
                and node.lineno not in exempt
            ):
                violations.append(f"  {py_file.name}:{node.lineno}: imports from {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("autoskillit") and node.lineno not in exempt:
                        violations.append(f"  {py_file.name}:{node.lineno}: imports {alias.name}")
    assert not violations, (
        "Hook scripts must be stdlib-only (no autoskillit.* imports) — "
        "they run outside the venv:\n" + "\n".join(violations)
    )


def test_init_files_are_pure_facades() -> None:
    """P14-2: Sub-package __init__.py files must not define FunctionDef or AsyncFunctionDef
    at module scope. They must be pure re-export facades.

    After groupE (P14-1), server/__init__.py is a pure facade. This test enforces the
    same constraint across all immediate sub-package __init__.py files.

    Exempt: src/autoskillit/__init__.py (package root, defines __version__ at module scope).
    """
    violations: list[str] = []

    for init_file in SRC_ROOT.glob("*/__init__.py"):
        source = init_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(init_file))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                violations.append(
                    f"  {_rel(init_file)}:{node.lineno}: defines {node.name!r} at module scope"
                )

    assert not violations, (
        "Sub-package __init__.py files must not define functions at module scope "
        "(pure re-export facades only):\n" + "\n".join(violations)
    )


def test_get_logger_no_bind() -> None:
    """get_logger() must not call .bind() on the proxy — it eagerly resolves.

    The lazy proxy contract requires that get_logger() returns an unresolved
    BoundLoggerLazyProxy. Calling .bind() resolves the proxy against the current
    structlog config, freezing it before configure_logging() runs. This arch rule
    prevents regression to the eager-resolution pattern.

    Uses AST Call+Attribute analysis (not string matching) to avoid false
    positives from comments mentioning .bind().
    """
    import ast as _ast

    logging_py = SRC_ROOT / "core" / "logging.py"
    tree = _ast.parse(logging_py.read_text())
    for node in _ast.walk(tree):
        if isinstance(node, _ast.FunctionDef) and node.name == "get_logger":
            # Walk the function body AST for Call nodes invoking .bind()
            bind_calls = [
                n
                for n in _ast.walk(node)
                if isinstance(n, _ast.Call)
                and isinstance(n.func, _ast.Attribute)
                and n.func.attr == "bind"
            ]
            assert not bind_calls, (
                "get_logger() must not call .bind() on the structlog proxy — "
                "it eagerly resolves the lazy proxy, freezing the pre-boot config. "
                "Use proxy._initial_values instead to keep the proxy lazy."
            )
            return
    pytest.fail("get_logger() function not found in core/logging.py")


# ── Kill-path structural guards (1f) ─────────────────────────────────────────


def test_no_direct_async_kill_process_tree_outside_executor() -> None:
    """No src file may call async_kill_process_tree or kill_process_tree
    outside the designated kill helper functions.

    Allowed call sites:
    - src/autoskillit/execution/_process_kill.py (defines the helpers)
    - execute_termination_action in src/autoskillit/execution/process.py
    - BaseException handler in run_managed_async in process.py (cleanup path)
    - run_managed_sync in process.py (sync cleanup path)
    """
    allowed_files = {
        SRC_ROOT / "execution" / "_process_kill.py",
        SRC_ROOT / "execution" / "process.py",
    }
    violations: list[str] = []

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        if py_file in allowed_files:
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"async_kill_process_tree", "kill_process_tree"}
            ):
                violations.append(
                    f"  {py_file.relative_to(SRC_ROOT.parent.parent)}:{node.lineno}: "
                    f"direct call to {node.func.id}() outside allowed files"
                )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"async_kill_process_tree", "kill_process_tree"}
            ):
                violations.append(
                    f"  {py_file.relative_to(SRC_ROOT.parent.parent)}:{node.lineno}: "
                    f"direct call to .{node.func.attr}() outside allowed files"
                )

    assert not violations, (
        "Direct async_kill_process_tree/kill_process_tree calls found outside allowed files.\n"
        "All kill calls must go through execute_termination_action in process.py:\n"
        + "\n".join(violations)
    )


def test_no_direct_termination_dispatch_ifelse_in_run_managed() -> None:
    """run_managed_async must not contain an if/elif chain that inspects
    TerminationReason.* or signals.process_exited directly.

    The dispatch must be delegated to decide_termination_action.
    """
    process_py = SRC_ROOT / "execution" / "process.py"
    tree = ast.parse(process_py.read_text())

    # Find run_managed_async function body
    run_managed_node: ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_managed_async":
            run_managed_node = node
            break

    assert run_managed_node is not None, "run_managed_async not found in process.py"

    # Walk the function body and detect any If node whose test references
    # TerminationReason.* attribute or signals.process_exited
    violations: list[str] = []
    for node in ast.walk(run_managed_node):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # Detect: timeout_scope.cancelled_caught (allowed), but TerminationReason.* is banned
        # Walk the test expression for TerminationReason attribute access
        for subnode in ast.walk(test):
            if (
                isinstance(subnode, ast.Attribute)
                and isinstance(subnode.value, ast.Name)
                and subnode.value.id == "TerminationReason"
            ):
                violations.append(
                    f"process.py:{getattr(node, 'lineno', '?')}: "
                    f"run_managed_async uses if/elif on TerminationReason.{subnode.attr} "
                    "— dispatch must go through decide_termination_action"
                )
            # Detect: signals.process_exited in if test
            if (
                isinstance(subnode, ast.Attribute)
                and isinstance(subnode.value, ast.Name)
                and subnode.value.id == "signals"
                and subnode.attr == "process_exited"
            ):
                violations.append(
                    f"process.py:{getattr(node, 'lineno', '?')}: "
                    "run_managed_async branches on signals.process_exited directly "
                    "— dispatch must go through decide_termination_action"
                )

    assert not violations, (
        "run_managed_async must not inspect TerminationReason or signals.process_exited directly."
        "\nUse decide_termination_action to make the kill decision:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# ARCH-008: no-raw-pid-to-start-linux-tracing — Test 1.9 calibration tests
# ---------------------------------------------------------------------------


def test_arch008_detects_raw_pid_attribute_passed_as_target(tmp_path: Path) -> None:
    """ARCH-008 calibration: start_linux_tracing(target=proc.pid) is a violation.

    The .pid attribute on an anyio/subprocess Process object is the wrapper PID when
    PTY mode is active. Passing it directly to start_linux_tracing caused issue #806.
    """
    f = tmp_path / "bad.py"
    f.write_text(
        "from autoskillit.execution.linux_tracing import start_linux_tracing\n"
        "start_linux_tracing(target=proc.pid, config=cfg, tg=tg)\n"
    )
    violations = _scan(f)
    arch008 = [v for v in violations if v.rule_id == "ARCH-008"]
    assert arch008, (
        "ARCH-008 must fire when start_linux_tracing is called with target=<expr>.pid. "
        f"All violations found: {violations}"
    )
    assert "pid" in arch008[0].message.lower() or "raw" in arch008[0].message.lower(), (
        f"ARCH-008 violation message must mention the raw pid issue. Got: {arch008[0].message!r}"
    )


def test_arch008_accepts_resolve_trace_target_result(tmp_path: Path) -> None:
    """ARCH-008 calibration: start_linux_tracing(target=resolve_trace_target(...)) is allowed.

    Calling resolve_trace_target() returns a TraceTarget (not a raw int), so it
    satisfies the type contract.
    """
    f = tmp_path / "good.py"
    f.write_text(
        "from autoskillit.execution.linux_tracing import start_linux_tracing, resolve_trace_target\n"
        "target = resolve_trace_target(root_pid=proc.pid, expected_basename='claude', timeout=2.0)\n"
        "start_linux_tracing(target=target, config=cfg, tg=tg)\n"
    )
    violations = _scan(f)
    arch008 = [v for v in violations if v.rule_id == "ARCH-008"]
    assert not arch008, (
        f"ARCH-008 must NOT fire when target is a Name (variable), not an Attribute. "
        f"Violations found: {arch008}"
    )


def test_arch008_accepts_trace_target_from_pid_result(tmp_path: Path) -> None:
    """ARCH-008 calibration: start_linux_tracing(target=trace_target_from_pid(...)) is allowed."""
    f = tmp_path / "good_direct.py"
    f.write_text(
        "from autoskillit.execution.linux_tracing import start_linux_tracing, trace_target_from_pid\n"
        "target = trace_target_from_pid(proc.pid)\n"
        "start_linux_tracing(target=target, config=cfg, tg=tg)\n"
    )
    violations = _scan(f)
    arch008 = [v for v in violations if v.rule_id == "ARCH-008"]
    assert not arch008, (
        f"ARCH-008 must NOT fire when target is a Name variable, not an Attribute. "
        f"Violations found: {arch008}"
    )


def test_no_raw_pid_attr_to_start_linux_tracing() -> None:
    """ARCH-008 (Test 1.9): no production file passes <expr>.pid as target to start_linux_tracing.

    Enforces the PTY wrapper tracer PID immunity contract from issue #806:
    any call site that tries to pass proc.pid (or any .pid Attribute) directly
    to start_linux_tracing is caught in CI before it ships.
    """
    violations = []
    for src_file in _SOURCE_FILES:
        file_violations = _scan(src_file)
        arch008 = [v for v in file_violations if v.rule_id == "ARCH-008"]
        violations.extend(arch008)

    assert not violations, (
        "ARCH-008: start_linux_tracing called with a raw .pid attribute as target. "
        "Use resolve_trace_target() (PTY mode) or trace_target_from_pid() (direct mode) "
        "to get a TraceTarget first (issue #806):\n" + "\n".join(f"  {v}" for v in violations)
    )
