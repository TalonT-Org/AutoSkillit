"""Architectural enforcement: AST-based visitor rules (ARCH-001 through ARCH-012).

Rules enforced here (compile-time, no execution required):
  1. No print() calls in production code
  2. No sensitive keyword arguments passed to logger calls
  3. No broad except without logger call or re-raise
  4. asyncio.PIPE ban outside process.py
  5. get_logger() must be called with __name__
  6. No f-string interpolation of sensitive variables in logger positional args
  7. Exhaustive TerminationReason dispatch (match/case + assert_never)
  8. No raw .pid attribute passed to start_linux_tracing()
  9. get_logger() result must be bound to variable named 'logger'
 10. StrEnum fields must not be compared against raw string literals
 11. __init__.pyi stub files must contain only re-export imports
 12. A frozen version reference must not be compared against a live
     importlib.metadata.version() read
 13. Every check invocation inside cli/doctor's _collect_doctor_results must
     route through _run_check — a bare call lets one check's exception (or a
     nested-list append/extend mistake) crash all other checks (see #4768)

Note: `import logging` and `logging.getLogger()` are enforced by ruff TID251
at pre-commit time (see pyproject.toml [tool.ruff.lint.flake8-tidy-imports]).
Those rules belong in the toolchain, not duplicated here.

Exemptions:
  - cli/app.py, cli/_doctor.py, cli/_session_cook.py: user-facing terminal output OK
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
    _scan_strenum_compare,
)
from tests.arch._rules import (
    _DISPATCH_TABLE_EXEMPT_FUNCTIONS,
    RuleDescriptor,
    _rel,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


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


def test_tmp_and_cache_share_generation_parent(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Tmp and cache paths must be siblings in one invocation-unique generation."""
    if sys.platform == "linux":
        cwd_hash = hashlib.sha256(os.getcwd().encode()).hexdigest()[:8]
        tmp_root = next(path for path in (tmp_path, *tmp_path.parents) if path.name == "tmp")
        generation = tmp_root.parent
        cache_dir = Path(str(request.config.getini("cache_dir"))).resolve()
        assert cache_dir.parent == generation
        assert generation.name.startswith(f"pytest-{cwd_hash}-")


def test_tmpdir_env_matches_basetemp(tmp_path: Path) -> None:
    """TMPDIR must resolve to the basetemp ancestor rendered for this invocation."""
    configured_tmpdir = Path(os.environ["TMPDIR"]).resolve()
    assert (
        configured_tmpdir == tmp_path.resolve() or configured_tmpdir in tmp_path.resolve().parents
    )


def test_tmp_path_has_worktree_hash(tmp_path: Path) -> None:
    """tmp_path must contain the worktree hash followed by a generation suffix."""
    if sys.platform == "linux":
        cwd_hash = hashlib.sha256(os.getcwd().encode()).hexdigest()[:8]
        path_str = str(tmp_path)
        assert f"pytest-{cwd_hash}-" in path_str, (
            f"tmp_path ({path_str!r}) does not contain invocation-unique worktree identity "
            f"pytest-{cwd_hash}-. Run tests through the Taskfile test gate."
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


def test_post_pivot_reporter_exemption_is_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.arch import _helpers

    src_root = tmp_path / "src" / "autoskillit"
    monkeypatch.setattr(_helpers, "SRC_ROOT", src_root)
    transaction = src_root / "cli" / "update" / "_transaction.py"
    transaction.parent.mkdir(parents=True)
    transaction.write_text(
        "def _report_post_pivot_failure(message):\n"
        "    try:\n"
        "        logger.warning(message)\n"
        "    except Exception:\n"
        "        pass\n"
        "try:\n"
        "    risky()\n"
        "except Exception:\n"
        "    _report_post_pivot_failure('failed')\n"
    )
    assert not [v for v in _scan(transaction) if v.rule_id == "ARCH-003"]

    unrelated = src_root / "unrelated" / "_transaction.py"
    unrelated.parent.mkdir()
    unrelated.write_text("try:\n    risky()\nexcept Exception:\n    pass\n")
    assert [v for v in _scan(unrelated) if v.rule_id == "ARCH-003"]


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

    Enforces that all claude command construction goes through
    ClaudeCodeBackend in execution/backends/claude.py, preventing ad-hoc
    command assembly that bypasses established safety flags.
    """
    ALLOWED = {
        ("_session_cook.py", "cook"),
        ("claude.py", "build_interactive_cmd"),
        ("claude.py", "build_headless_cmd"),
        ("claude.py", "build_resume_cmd"),
        ("_init_helpers.py", "_is_plugin_installed"),
        ("_doctor_mcp.py", "_check_mcp_server_registered"),
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
    for py_file in sorted(hooks_dir.rglob("*.py")):
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

    Scans all __init__.py files recursively, including nested sub-packages.

    Exempt: src/autoskillit/__init__.py (package root, defines __version__ at module scope).
    """
    # TODO: remove exemptions below once business-logic functions are refactored out
    # of these __init__.py files into dedicated modules (P14-2 architectural debt).
    _EXEMPT_INITS = frozenset(
        {
            Path("cli/fleet/__init__.py"),
            Path("cli/doctor/__init__.py"),
            Path("execution/process/__init__.py"),
            Path("execution/headless/__init__.py"),
            Path("execution/backends/__init__.py"),
        }
    )

    violations: list[str] = []

    for init_file in SRC_ROOT.rglob("__init__.py"):
        if init_file.parent == SRC_ROOT:
            continue
        rel = init_file.relative_to(SRC_ROOT)
        if rel in _EXEMPT_INITS:
            continue
        source = init_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(init_file))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # PEP 562 module protocol functions are allowed at module scope
                if node.name in ("__getattr__", "__dir__"):
                    continue
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


def test_logger_variable_name_violation_underscore_log(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    f.write_text("from autoskillit.core import get_logger\n_log = get_logger(__name__)\n")
    violations = _scan(f)
    assert any(v.rule_id == "ARCH-009" for v in violations)


def test_logger_variable_name_violation_underscore_logger(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    f.write_text("from autoskillit.core import get_logger\n_logger = get_logger(__name__)\n")
    violations = _scan(f)
    assert any(v.rule_id == "ARCH-009" for v in violations)


def test_logger_variable_name_accepts_logger(tmp_path: Path) -> None:
    f = tmp_path / "good.py"
    f.write_text("from autoskillit.core import get_logger\nlogger = get_logger(__name__)\n")
    violations = _scan(f)
    arch009_violations = [v for v in violations if v.rule_id == "ARCH-009"]
    assert not arch009_violations


# ── Kill-path structural guards (1f) ─────────────────────────────────────────


def test_no_direct_async_kill_process_tree_outside_executor() -> None:
    """No src file may call async_kill_process_tree or kill_process_tree
    outside the designated kill helper functions.

    Allowed call sites:
    - src/autoskillit/execution/_process_kill.py (defines the helpers)
    - execute_termination_action in src/autoskillit/execution/process.py
    - BaseException handler in run_managed_async in process.py (cleanup path)
    - run_managed_sync in process.py (sync cleanup path)
    - reap_orphaned_codex_processes in _codex_orphans.py (orphan reaper)
    - reap_orphaned_autoskillit_daemons in _daemon_orphans.py (registered daemon reaper)
    - sweep_orphaned_tethers in _process_tether.py (generic spawner-death sweep)
    - CodexSessionStore.recover() in _codex_session_storage.py (verify-before-mark)
    """
    allowed_files = {
        SRC_ROOT / "execution" / "process" / "_process_kill.py",
        SRC_ROOT / "execution" / "process" / "__init__.py",
        SRC_ROOT / "execution" / "process" / "_codex_orphans.py",
        SRC_ROOT / "execution" / "process" / "_daemon_orphans.py",
        SRC_ROOT / "execution" / "process" / "_process_tether.py",
        SRC_ROOT / "execution" / "backends" / "_codex_session_storage.py",
        SRC_ROOT / "fleet" / "_dispatch_reaper.py",
        # _api.py's _write_pid callback (fail-closed layer IL-2) kills the spawned
        # child via the canonical sync primitive when mark_dispatch_running
        # raises (see plan rectify_fleet-resume-precondition-chokepoint_*).
        SRC_ROOT / "fleet" / "_api.py",
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


def _gather_import_aliases(tree: ast.AST) -> dict[str, tuple[str, str | None]]:
    """Build a map from local name → (module, attr) for every import in the tree.

    Handles `import x`, `import x as y`, `from x import a`, `from x import a as b`.
    """
    aliases: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name
                aliases[local] = (alias.name, None)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                local = alias.asname or alias.name
                aliases[local] = (node.module, alias.name)
    return aliases


def test_no_direct_settle_call_outside_allowlist() -> None:
    """No src file may call .settle() outside the designated allowlist."""
    # .settle() raises OwnedProcessCleanupError on incomplete teardown. Callers
    # that do not need that raising behavior must use .settle_evidence() or
    # .settle_preserving() instead — a bare .settle() call is deliberately narrow.
    allowed_files = {
        SRC_ROOT / "execution" / "process" / "_process_kill.py",  # defines settle()
        SRC_ROOT / "cli" / "session" / "_session_process.py",  # requires raising semantics
        SRC_ROOT / "execution" / "evidence_reader.py",  # pre-existing catch-and-convert
        SRC_ROOT / "hooks" / "_capture_process.py",  # structurally unrelated reimpl
    }
    violations: list[str] = []

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        if py_file in allowed_files:
            continue
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue
        aliases = _gather_import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr == "settle":
                violations.append(
                    f"  {py_file.relative_to(SRC_ROOT.parent.parent)}:{node.lineno}: "
                    f"direct call to .settle() outside allowed files"
                )
            elif isinstance(node.func, ast.Name):
                _alias_entry = aliases.get(node.func.id)
                if _alias_entry is not None and _alias_entry[1] == "settle":
                    violations.append(
                        f"  {py_file.relative_to(SRC_ROOT.parent.parent)}:{node.lineno}: "
                        f"direct call to settle (from-import or aliased) outside allowed files"
                    )

    assert not violations, (
        "Direct .settle() calls found outside allowed files.\n"
        "Use .settle_evidence() or .settle_preserving() unless the raising semantics "
        "are genuinely required, then add the file to the allowlist:\n" + "\n".join(violations)
    )


def test_no_raw_zombie_blind_liveness_check_outside_shared_primitive() -> None:
    """No src file may call psutil.pid_exists(), bare os.kill(pid, 0), or
    psutil.Process(pid).is_running() outside the shared zombie-aware primitive."""
    # All three checks are zombie-blind — a zombie retains a readable /proc
    # entry and reports as alive under an exact-PID-existence check alone.
    allowed_files = {
        SRC_ROOT / "core" / "runtime" / "_linux_proc.py",  # defines the shared primitive
        SRC_ROOT / "core" / "_plugin_cache.py",  # cross-boot stored_create_time needs psutil
        SRC_ROOT / "execution" / "process" / "_daemon_orphans.py",  # /proc unreadable fallback
        SRC_ROOT
        / "execution"
        / "process"
        / "_process_kill.py",  # identity-coherence with create_time
        SRC_ROOT / "fleet" / "_dispatch_reaper.py",  # pre-existing follow-up
        SRC_ROOT / "hooks" / "guards" / "mcp_health_advisor.py",  # stdlib-only hook
    }
    violations: list[str] = []

    def _is_psutil_pid_exists_call(
        node: ast.Call, aliases: dict[str, tuple[str, str | None]]
    ) -> bool:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "pid_exists":
            if (
                isinstance(node.func.value, ast.Name)
                and aliases.get(node.func.value.id, (None,))[0] == "psutil"
            ):
                return True
        elif isinstance(node.func, ast.Name) and aliases.get(node.func.id, (None,)) == (
            "psutil",
            "pid_exists",
        ):
            return True
        return False

    def _is_os_kill_probe_call(node: ast.Call, aliases: dict[str, tuple[str, str | None]]) -> bool:
        if (
            len(node.args) != 2
            or not isinstance(node.args[1], ast.Constant)
            or node.args[1].value != 0
        ):
            return False
        if isinstance(node.func, ast.Attribute) and node.func.attr == "kill":
            if (
                isinstance(node.func.value, ast.Name)
                and aliases.get(node.func.value.id, (None,))[0] == "os"
            ):
                return True
        elif isinstance(node.func, ast.Name) and aliases.get(node.func.id, (None,)) == (
            "os",
            "kill",
        ):
            return True
        return False

    def _is_psutil_is_running_call(
        node: ast.Call, aliases: dict[str, tuple[str, str | None]]
    ) -> bool:
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "is_running"):
            return False
        receiver = node.func.value
        if not isinstance(receiver, ast.Call):
            return False
        if isinstance(receiver.func, ast.Attribute) and receiver.func.attr == "Process":
            # `psutil.Process(pid).is_running()` form
            if (
                isinstance(receiver.func.value, ast.Name)
                and aliases.get(receiver.func.value.id, (None,))[0] == "psutil"
            ):
                return True
        elif isinstance(receiver.func, ast.Name):
            # `from psutil import Process; Process(pid).is_running()` form
            _proc_alias = aliases.get(receiver.func.id)
            if (
                _proc_alias is not None
                and _proc_alias[0] == "psutil"
                and _proc_alias[1] == "Process"
            ):
                return True
        return False

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        if py_file in allowed_files:
            continue
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue
        aliases = _gather_import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _is_psutil_pid_exists_call(node, aliases):
                violations.append(
                    f"  {py_file.relative_to(SRC_ROOT.parent.parent)}:{node.lineno}: "
                    f"direct call to psutil.pid_exists() outside allowed files"
                )
            elif _is_os_kill_probe_call(node, aliases):
                violations.append(
                    f"  {py_file.relative_to(SRC_ROOT.parent.parent)}:{node.lineno}: "
                    f"direct call to os.kill(pid, 0) outside allowed files"
                )
            elif _is_psutil_is_running_call(node, aliases):
                violations.append(
                    f"  {py_file.relative_to(SRC_ROOT.parent.parent)}:{node.lineno}: "
                    f"direct call to psutil.Process(pid).is_running() outside allowed files"
                )

    assert not violations, (
        "Direct zombie-blind liveness checks found outside allowed files.\n"
        "Use core.is_pid_alive()/is_pid_zombie() (or core._plugin_cache._pid_alive() "
        "when cross-boot stored_create_time verification is needed) instead:\n"
        + "\n".join(violations)
    )


# (path, rationale) rows rather than a bare set — the kill-funnel guard's docstring
# rationale drifted out of sync with its allowlist because the two live apart; binding
# rationale to entry here makes that drift unrepresentable.
_DETACHED_SPAWN_ALLOWLIST: list[tuple[Path, str]] = [
    (
        SRC_ROOT / "execution" / "process" / "_process_kill.py",
        "defines spawn_owned_process — the funnel itself",
    ),
    (
        SRC_ROOT / "hooks" / "_capture_process.py",
        "stdlib-only standalone hook primitive; sub-second bounded capture shells; "
        "passes process_group=0 but start_new_session=False — group-isolated, not "
        "session-detached, so not an orphan source; expansion forbidden",
    ),
]

_DETACH_SPAWN_TARGETS = {
    ("subprocess", "Popen"),
    ("asyncio", "create_subprocess_exec"),
    ("asyncio", "create_subprocess_shell"),
}
_DETACH_SPAWN_KEYWORDS = {"start_new_session", "process_group"}


def _resolve_detach_spawn_target(
    node: ast.Call, aliases: dict[str, tuple[str, str | None]]
) -> tuple[str, str] | None:
    """Resolve a Call to (module, attr) if it targets subprocess.Popen /
    asyncio.create_subprocess_exec / asyncio.create_subprocess_shell, honoring
    import aliases (``import subprocess as sp``, ``from subprocess import Popen``)."""
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module, _ = aliases.get(func.value.id, (func.value.id, None))
        target = (module, func.attr)
        return target if target in _DETACH_SPAWN_TARGETS else None
    if isinstance(func, ast.Name):
        alias_entry = aliases.get(func.id)
        if alias_entry is not None and alias_entry[1] is not None:
            target = (alias_entry[0], alias_entry[1])
            return target if target in _DETACH_SPAWN_TARGETS else None
    return None


def _detach_spawn_violation_reason(node: ast.Call) -> str | None:
    """A call is a violation if it literally sets start_new_session=True or
    process_group=0, or if its keywords cannot be statically proven NOT to set
    either — a ``**kwargs`` unpack, or a non-literal value for either keyword.
    Violations-by-default: an unprovable call is treated as a violation, closing
    the indirection hole a static, value-based match cannot see through (the
    funnel itself uses exactly this construction internally via **popen_kwargs).
    """
    for kw in node.keywords:
        if kw.arg is None:
            return (
                "**kwargs unpack — cannot statically prove it omits "
                "start_new_session/process_group"
            )
        if kw.arg not in _DETACH_SPAWN_KEYWORDS:
            continue
        value = kw.value
        if not isinstance(value, ast.Constant):
            return f"non-literal value for {kw.arg}= — cannot statically prove its value"
        if kw.arg == "start_new_session" and value.value is True:
            return "literal start_new_session=True"
        if kw.arg == "process_group" and value.value == 0:
            return "literal process_group=0"
    return None


def test_no_detached_spawn_outside_owned_funnel() -> None:
    """No src file may construct a detached/process-group-isolated
    subprocess.Popen or asyncio.create_subprocess_exec/_shell call outside the
    owned-process spawn funnel (spawn_owned_process in _process_kill.py).

    Every detached spawn must fund through spawn_owned_process so it durably
    records a process tether (see _process_tether.py) — an untethered detached
    child must never exist. Violations-by-default, explicit allowlist below.
    """
    allowed_files = {path for path, _ in _DETACHED_SPAWN_ALLOWLIST}
    rationale_by_file = dict(_DETACHED_SPAWN_ALLOWLIST)
    violations: list[str] = []

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        if py_file in allowed_files:
            continue
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue
        aliases = _gather_import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = _resolve_detach_spawn_target(node, aliases)
            if target is None:
                continue
            reason = _detach_spawn_violation_reason(node)
            if reason is None:
                continue
            module, attr = target
            violations.append(
                f"  {py_file.relative_to(SRC_ROOT.parent.parent)}:{node.lineno}: "
                f"{module}.{attr}() call is a detached-spawn violation ({reason})"
            )

    assert not violations, (
        "Detached subprocess.Popen/asyncio.create_subprocess_* calls found outside "
        "spawn_owned_process. Route through spawn_owned_process(tether=...) — or add "
        "an allowlist entry to _DETACHED_SPAWN_ALLOWLIST with rationale — for:\n"
        + "\n".join(violations)
        + "\n\nAllowlisted files and why:\n"
        + "\n".join(
            f"  {path.relative_to(SRC_ROOT.parent.parent)}: {rationale}"
            for path, rationale in rationale_by_file.items()
        )
    )


_ENUMERATION_ATTR_METHODS = {"glob", "rglob", "iterdir", "scandir"}
_ENUMERATION_DOTTED_CALLS = {"os.walk", "os.scandir", "os.listdir"}
_GUARDED_STAT_ATTRS = {"stat", "lstat", "read_text", "read_bytes"}
_GUARDED_STAT_DOTTED_CALLS = {"os.stat", "os.lstat", "os.path.getmtime", "os.path.getsize"}
_OSERROR_FAMILY = {
    "OSError",
    "FileNotFoundError",
    "FileExistsError",
    "PermissionError",
    "NotADirectoryError",
    "IsADirectoryError",
    "InterruptedError",
    "ProcessLookupError",
    "ChildProcessError",
    "BlockingIOError",
    "BrokenPipeError",
    "ConnectionError",
    "ConnectionAbortedError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "TimeoutError",
}
_FS_OBSERVATION_FUNNEL = {"observe_path_mode", "safe_mtime"}

# (path, rationale, tracking issue) rows — mirrors _DETACHED_SPAWN_ALLOWLIST's
# (path, rationale) shape, extended with the tracking-issue reference T-A3 requires
# for every LATENT site this part's sweep found but did not fix.
_ENUMERATION_STAT_ALLOWLIST: list[tuple[Path, str]] = [
    (
        SRC_ROOT / "cli" / "_install_snapshot" / "_snapshot.py",
        "_matches_staged_state lstat()s two rglob()-enumerated entries with no "
        "try/except in the function; caller chain already absorbs it via "
        "rollback()'s except BaseException, so not a live crash today — #4784",
    ),
    (
        SRC_ROOT / "cli" / "doctor" / "_doctor_fleet.py",
        "unguarded stat on an iterdir()-enumerated campaign state file — #4768",
    ),
    (
        SRC_ROOT / "core" / "io.py",
        "directory_tree_digest stats every os.walk()-enumerated entry with no guard; "
        "3 of 5 callers guard against this at the call site, this function does not — #4770",
    ),
    (
        SRC_ROOT / "execution" / "_recording_skills.py",
        "build_skills_manifest reads an iterdir()-enumerated skill_md with no "
        "try/except; safe today only because its one caller passes a private "
        "post-copytree directory, not a live shared one — #4785",
    ),
    (
        SRC_ROOT / "execution" / "session_log.py",
        "unguarded stat in an iterdir()-then-sort key= over committed session dirs — #4771",
    ),
    (
        SRC_ROOT / "hooks" / "session_start_hook.py",
        "unguarded read on a glob()-enumerated kitchen-state marker inside a broad "
        "except Exception — stdlib-only hook code, core.fs_observation is a safe import "
        "here — #4772",
    ),
    (
        SRC_ROOT / "recipe" / "_cmd_rpc_issues.py",
        "batch_create_issues reads a glob()-enumerated ticket_body_*.md with no "
        "try/except at all; no concurrent writer identified, but a hit would hard-fail "
        "the whole ticket batch, not just the racing file — #4786",
    ),
    (
        SRC_ROOT / "server" / "_editable_guard.py",
        "unguarded read on a glob()-enumerated direct_url.json inside a broad "
        "except Exception — #4773",
    ),
    (
        SRC_ROOT / "workspace" / "session_skills.py",
        "unguarded stat on an iterdir()-enumerated lease-sweep candidate, no try/except "
        "nearby at all — #4774",
    ),
    (
        SRC_ROOT / "workspace" / "_projected_artifact" / "materialization.py",
        "_render_agent_definitions reads a glob()-enumerated agent .md file with no "
        "try/except; agents_dir is a private, synchronously-populated tempdir with no "
        "identified concurrent writer at all — #4787",
    ),
]


def _dotted_name(node: ast.expr, aliases: dict[str, tuple[str, str | None]]) -> str | None:
    """Resolve an expression to a dotted string, honoring import aliases for the root name."""
    if isinstance(node, ast.Name):
        alias_entry = aliases.get(node.id)
        if alias_entry is not None:
            module, attr = alias_entry
            return f"{module}.{attr}" if attr else module
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value, aliases)
        return f"{base}.{node.attr}" if base is not None else None
    return None


def _is_enumeration_call(node: ast.expr, aliases: dict[str, tuple[str, str | None]]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Attribute) and node.func.attr in _ENUMERATION_ATTR_METHODS:
        return True
    return _dotted_name(node.func, aliases) in _ENUMERATION_DOTTED_CALLS


def _enumeration_source(
    iterable: ast.expr,
    aliases: dict[str, tuple[str, str | None]],
    tainted: set[str],
) -> bool:
    """Return whether `iterable` is (or wraps via subscript/starred/comprehension/
    sorted/min/max) an enumeration call, or a tainted name already established
    elsewhere in the function."""
    if isinstance(iterable, (ast.Starred, ast.Subscript)):
        inner = iterable.value
        if _is_enumeration_call(inner, aliases):
            return True
        return isinstance(inner, ast.Name) and inner.id in tainted
    if isinstance(iterable, ast.Name):
        return iterable.id in tainted
    if _is_enumeration_call(iterable, aliases):
        return True
    if isinstance(iterable, ast.Call):
        dotted = _dotted_name(iterable.func, aliases)
        if (
            dotted is not None
            and dotted.rsplit(".", 1)[-1] in {"sorted", "min", "max"}
            and iterable.args
        ):
            return _enumeration_source(iterable.args[0], aliases, tainted)
    if isinstance(iterable, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return any(_is_enumeration_call(gen.iter, aliases) for gen in iterable.generators)
    if isinstance(iterable, (ast.List, ast.Tuple, ast.Set)):
        return any(_enumeration_source(elt, aliases, tainted) for elt in iterable.elts)
    return False


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_target_names(elt))
        return names
    return []


def _mentions_tainted_name(node: ast.AST, tainted: set[str]) -> bool:
    return any(isinstance(sub, ast.Name) and sub.id in tainted for sub in ast.walk(node))


def _guarded_call_subject(
    node: ast.Call, aliases: dict[str, tuple[str, str | None]]
) -> str | None:
    """Return the tainted-shaped subject name `node` stats/reads, if any."""
    if isinstance(node.func, ast.Attribute) and node.func.attr in _GUARDED_STAT_ATTRS:
        return node.func.value.id if isinstance(node.func.value, ast.Name) else None
    if isinstance(node.func, ast.Name) and node.func.id == "open" and node.args:
        return node.args[0].id if isinstance(node.args[0], ast.Name) else None
    dotted = _dotted_name(node.func, aliases)
    if dotted in _GUARDED_STAT_DOTTED_CALLS and node.args and isinstance(node.args[0], ast.Name):
        return node.args[0].id
    return None


def _is_funnel_call(node: ast.Call, aliases: dict[str, tuple[str, str | None]]) -> bool:
    if isinstance(node.func, ast.Name) and node.func.id in _FS_OBSERVATION_FUNNEL:
        return True
    if isinstance(node.func, ast.Attribute) and node.func.attr in _FS_OBSERVATION_FUNNEL:
        return True
    dotted = _dotted_name(node.func, aliases)
    return dotted is not None and dotted.rsplit(".", 1)[-1] in _FS_OBSERVATION_FUNNEL


# Superclasses of OSError that genuinely catch it at runtime despite not being
# a literal OSError-family member name — kept separate from _OSERROR_FAMILY,
# which enumerates OSError itself and its subclasses, not its superclasses.
_BROAD_EXCEPTION_COVERAGE = {"Exception", "BaseException"}


def _handler_covers_oserror(handler: ast.ExceptHandler) -> bool:
    kind = handler.type
    if kind is None:
        return True
    covers = _OSERROR_FAMILY | _BROAD_EXCEPTION_COVERAGE
    if isinstance(kind, ast.Tuple):
        return any(isinstance(elt, ast.Name) and elt.id in covers for elt in kind.elts)
    return isinstance(kind, ast.Name) and kind.id in covers


def _comprehension_local_taint(
    expr: ast.expr, aliases: dict[str, tuple[str, str | None]], tainted: set[str]
) -> frozenset[str]:
    """Return names bound by a comprehension's own generator target inside `expr`,
    scoped locally to that comprehension. Lets check_expr recognize e.g. `p` in
    `[p for p in d.iterdir() if p.stat()...]` as tainted within that
    comprehension's own subtree — even where `p` is never tainted at function
    level at all. A comprehension's own generator target never leaks into the
    function-level `tainted` set (real Python scoping), but the reverse can
    coincide: an unrelated function-level-tainted name (e.g. a `for` loop
    target elsewhere in the same function) may share a name with a later
    comprehension's own generator variable — ordinary name reuse, not a
    scoping bug, so the two sets are allowed to overlap.
    """
    local: set[str] = set()
    for node in ast.walk(expr):
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for gen in node.generators:
                if _enumeration_source(gen.iter, aliases, tainted):
                    local.update(_target_names(gen.target))
    return frozenset(local)


def _find_enumeration_stat_violations(
    tree: ast.AST, aliases: dict[str, tuple[str, str | None]]
) -> list[tuple[int, str]]:
    """Scan every function in `tree` for an unguarded stat/read on an
    enumeration-derived path. Each function gets its own taint set — this is
    intentionally intra-function only; nested defs are analyzed separately
    when the outer walk reaches them."""
    violations: list[tuple[int, str]] = []

    def scan_function(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        tainted: set[str] = set()

        def check_expr(
            expr: ast.expr | None, guarded: bool, local_tainted: frozenset[str] = frozenset()
        ) -> None:
            if expr is None:
                return
            for node in ast.walk(expr):
                if not isinstance(node, ast.Call):
                    continue
                if _is_funnel_call(node, aliases):
                    continue
                subject = _guarded_call_subject(node, aliases)
                if subject is not None:
                    if (subject in tainted or subject in local_tainted) and not guarded:
                        violations.append(
                            (
                                node.lineno,
                                f"unguarded stat/read on enumeration-derived name {subject!r}",
                            )
                        )
                    continue
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id in {"sorted", "min", "max"}
                    and node.args
                    and _enumeration_source(node.args[0], aliases, tainted)
                ):
                    for kw in node.keywords:
                        if kw.arg != "key" or not isinstance(kw.value, ast.Lambda):
                            continue
                        lam = kw.value
                        if len(lam.args.args) != 1:
                            continue
                        param = lam.args.args[0].arg
                        for sub in ast.walk(lam.body):
                            if not isinstance(sub, ast.Call) or _is_funnel_call(sub, aliases):
                                continue
                            inner_subject = _guarded_call_subject(sub, aliases)
                            if inner_subject == param and not guarded:
                                violations.append(
                                    (
                                        sub.lineno,
                                        "unguarded stat in sorted/min/max key= lambda over "
                                        "an enumeration call",
                                    )
                                )

        def visit_stmts(stmts: list[ast.stmt], guarded: bool) -> None:
            for stmt in stmts:
                if isinstance(stmt, ast.For):
                    if _enumeration_source(stmt.iter, aliases, tainted):
                        tainted.update(_target_names(stmt.target))
                    check_expr(
                        stmt.iter, guarded, _comprehension_local_taint(stmt.iter, aliases, tainted)
                    )
                    visit_stmts(stmt.body, guarded)
                    visit_stmts(stmt.orelse, guarded)
                elif isinstance(stmt, ast.Assign):
                    check_expr(
                        stmt.value,
                        guarded,
                        _comprehension_local_taint(stmt.value, aliases, tainted),
                    )
                    if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                        if _mentions_tainted_name(stmt.value, tainted) or _enumeration_source(
                            stmt.value, aliases, tainted
                        ):
                            tainted.add(stmt.targets[0].id)
                elif isinstance(stmt, ast.Try):
                    body_guarded = guarded or any(
                        _handler_covers_oserror(h) for h in stmt.handlers
                    )
                    visit_stmts(stmt.body, body_guarded)
                    for handler in stmt.handlers:
                        visit_stmts(handler.body, guarded)
                    visit_stmts(stmt.orelse, guarded)
                    visit_stmts(stmt.finalbody, guarded)
                elif isinstance(stmt, (ast.If, ast.While)):
                    check_expr(
                        stmt.test, guarded, _comprehension_local_taint(stmt.test, aliases, tainted)
                    )
                    visit_stmts(stmt.body, guarded)
                    visit_stmts(stmt.orelse, guarded)
                elif isinstance(stmt, ast.With):
                    for item in stmt.items:
                        check_expr(
                            item.context_expr,
                            guarded,
                            _comprehension_local_taint(item.context_expr, aliases, tainted),
                        )
                    visit_stmts(stmt.body, guarded)
                elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                else:
                    for child in ast.iter_child_nodes(stmt):
                        if isinstance(child, ast.expr):
                            check_expr(
                                child, guarded, _comprehension_local_taint(child, aliases, tainted)
                            )

        visit_stmts(func_node.body, False)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan_function(node)
    return violations


@pytest.mark.parametrize(
    ("source", "expected_lines"),
    [
        pytest.param(
            "import os\n"
            "def f(root):\n"
            "    for d, names, files in os.walk(root):\n"
            "        for name in names:\n"
            "            candidate = d + name\n"
            "            candidate.stat()\n",
            [6],
            id="os_walk_then_unguarded_stat",
        ),
        pytest.param(
            "def f(d):\n    for p in d.rglob('*'):\n        p.lstat()\n",
            [3],
            id="rglob_then_unguarded_lstat",
        ),
        pytest.param(
            "def f(d):\n"
            "    for p in d.glob('*.json'):\n"
            "        with open(p) as fh:\n"
            "            fh.read()\n",
            [3],
            id="open_on_tainted_path_is_flagged_but_handle_dot_read_is_not_a_second_hit",
        ),
        pytest.param(
            "def f(d):\n"
            "    for p in d.glob('*.json'):\n"
            "        try:\n"
            "            p.stat()\n"
            "        except OSError:\n"
            "            pass\n",
            [],
            id="guarded_by_oserror_try_except_is_not_flagged",
        ),
        pytest.param(
            "def f(d):\n"
            "    for p in d.glob('*.json'):\n"
            "        try:\n"
            "            p.stat()\n"
            "        except ValueError:\n"
            "            pass\n",
            [4],
            id="try_except_naming_an_unrelated_type_is_still_flagged",
        ),
        pytest.param(
            "from autoskillit.core import safe_mtime\n"
            "def f(d):\n"
            "    return sorted(d.glob('*'), key=lambda p: safe_mtime(p) or 0.0)\n",
            [],
            id="funnel_call_in_sort_key_is_not_flagged",
        ),
        pytest.param(
            "import os\n"
            "def f(d):\n"
            "    return sorted(d.glob('*'), key=lambda p: os.path.getmtime(p))\n",
            [3],
            id="unguarded_getmtime_in_sort_key_is_flagged",
        ),
        pytest.param(
            "def f(d):\n    p = d\n    p.stat()\n",
            [],
            id="non_enumeration_name_is_never_tainted",
        ),
        pytest.param(
            "def f(d):\n"
            "    candidates = sorted(d.glob('*.json'), key=lambda p: p.stat().st_mtime)\n"
            "    sentinel = candidates[0]\n"
            "    return sentinel.read_text()\n",
            [2, 4],
            id="sorted_result_bound_then_indexed_and_read_is_flagged",
        ),
        pytest.param(
            "def f(d):\n"
            "    candidates = d.glob('*.json')\n"
            "    for p in candidates:\n"
            "        p.stat()\n",
            [4],
            id="bare_glob_bound_then_iterated_is_flagged",
        ),
        pytest.param(
            "def f(d, cutoff):\n"
            "    return [p for p in d.iterdir() if p.stat().st_mtime > cutoff]\n",
            [2],
            id="comprehension_over_enumeration_internal_stat_is_flagged",
        ),
        pytest.param(
            "def f(d):\n"
            "    try:\n"
            "        return sorted(d.glob('*'), key=lambda p: p.stat().st_mtime)\n"
            "    except OSError:\n"
            "        return None\n",
            [],
            id="sorted_min_max_key_lambda_guarded_by_try_except_is_not_flagged",
        ),
        pytest.param(
            "def f(d):\n"
            "    for p in d.glob('*.json'):\n"
            "        try:\n"
            "            p.stat()\n"
            "        except Exception:\n"
            "            pass\n",
            [],
            id="guarded_by_broad_exception_handler_is_not_flagged",
        ),
    ],
)
def test_enumeration_stat_detection_rule(source: str, expected_lines: list[int]) -> None:
    """Pin the detection logic itself against literal source fixtures — the same
    discipline test_distinct_layers_extraction applies to its own AST rule — so
    the rule is pinned, not just its current verdict against the live tree."""
    tree = ast.parse(source)
    aliases = _gather_import_aliases(tree)
    violations = _find_enumeration_stat_violations(tree, aliases)
    assert sorted(lineno for lineno, _ in violations) == expected_lines


def test_no_unguarded_stat_on_enumeration_derived_path_outside_funnel() -> None:
    """No src file may stat/read a path obtained by enumerating a directory
    (os.walk/os.scandir/os.listdir/.glob/.rglob/.iterdir/.scandir) without
    routing it through core.fs_observation. Violations-by-default, explicit
    allowlist below — each entry requires a rationale and a tracking issue.
    """
    allowed_files = {path for path, _ in _ENUMERATION_STAT_ALLOWLIST}
    rationale_by_file = dict(_ENUMERATION_STAT_ALLOWLIST)
    violations: list[str] = []

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        if py_file in allowed_files:
            continue
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue
        aliases = _gather_import_aliases(tree)
        for lineno, detail in _find_enumeration_stat_violations(tree, aliases):
            violations.append(
                f"  {py_file.relative_to(SRC_ROOT.parent.parent)}:{lineno}: {detail}"
            )

    assert not violations, (
        "Enumeration-derived paths stat'd/read without routing through "
        "core.fs_observation.observe_path_mode/safe_mtime. Route through the funnel — or "
        "add an allowlist entry to _ENUMERATION_STAT_ALLOWLIST with rationale and a "
        "tracking issue — for:\n"
        + "\n".join(violations)
        + "\n\nAllowlisted files and why:\n"
        + "\n".join(
            f"  {path.relative_to(SRC_ROOT.parent.parent)}: {rationale}"
            for path, rationale in rationale_by_file.items()
        )
    )


# Issue #4770: directory_tree_digest silently dropped subtrees whose scandir()
# failed mid-walk because os.walk(followlinks=False) received no onerror callback.
# Path.rglob() has the identical silent-suppression contract. strict_walk()
# (core/io.py) is the one funnel that fails loudly on a race instead; every raw
# os.walk/os.fwalk/Path.rglob() call used for identity- or tamper-evidence
# enumeration must go through it. (path, rationale) rows, matching
# _DETACHED_SPAWN_ALLOWLIST's shape — each entry documents the specific
# mitigation that makes that site a genuinely different risk profile, not an
# oversight.
_TREE_ENUMERATION_ALLOWLIST: list[tuple[Path, str]] = [
    (
        SRC_ROOT / "exploration" / "profile.py",
        "feeds activation_digest, compared via capture_repository_snapshot's "
        "deliberate double-capture-and-compare (start/end walk); a transient "
        "one-sided omission is caught as SnapshotCaptureStatus.STALE, not "
        "silently accepted",
    ),
    (
        SRC_ROOT / "exploration" / "snapshot.py",
        "_untracked_special_paths's leaf lstat() is unguarded and raises "
        "FileNotFoundError uncaught, propagating to capture_repository_snapshot's "
        "outer except (OSError, ...) -> SnapshotCaptureStatus.FAILED; fails loud, "
        "not silently wrong",
    ),
    (
        SRC_ROOT / "execution" / "headless" / "_headless_helpers.py",
        "_stat_snapshot feeds a before/after write-detection heuristic "
        "(fs_writes_detected), not a persisted/security identity value; a "
        "one-sided omission only skews toward reporting writes-detected, the "
        "safe direction for that check",
    ),
    (
        SRC_ROOT / "workspace" / "_projected_artifact" / "_generation_publication.py",
        "_fsync_tree_contents computes no digest/hash/value at all — pure fsync "
        "durability side effect over a private, exclusive staging directory "
        "nothing else touches; a leaf vanish there raises uncaught (crash, not "
        "silent corruption)",
    ),
    (
        SRC_ROOT / "core" / "_plugin_artifact_identity.py",
        "_classify_bytecode_contamination is diagnostic-only, called after a "
        "digest mismatch has already been raised, purely to embellish the error "
        "message with a bytecode-contamination hint; omission only affects "
        "message wording, not the validation outcome",
    ),
    (
        SRC_ROOT / "recipe" / "_api_cache.py",
        "_compute_content_hash is a dev-cache staleness-invalidation hash for "
        "the package's own source tree, not a security/tamper boundary; already "
        "tolerates per-file OSError by explicit design (except OSError: continue)",
    ),
    (
        SRC_ROOT / "recipe" / "rules" / "rules_pseudocode_sync.py",
        "_find_frozenset_constants is a dev-tooling consistency helper scanning "
        "the repo's own rule source to keep pseudocode docs in sync with code "
        "constants; not identity/tamper-critical, best-effort by nature (already "
        "wraps parse failures in a logged continue)",
    ),
    (
        SRC_ROOT / "cli" / "install" / "_marketplace.py",
        "one-time, explicitly idempotent CLI migration script (renames a "
        "directory, rewrites YAML text); a vanished file during the walk simply "
        "isn't migrated this run and the operation is safe to rerun per its own "
        "docstring — not an identity/tamper check",
    ),
    (
        SRC_ROOT / "hooks" / "skill_load_post_hook.py",
        "_resolve_manifest_path only locates a manifest sidecar for later "
        "validation elsewhere in the pipeline; it does not itself compute or "
        "compare an identity/tamper value",
    ),
]

_TREE_ENUMERATION_TARGETS = {("os", "walk"), ("os", "fwalk")}


def _resolve_tree_enumeration_target(
    node: ast.Call, aliases: dict[str, tuple[str, str | None]]
) -> tuple[str, str] | None:
    """Resolve a Call to (module, attr) if it targets os.walk/os.fwalk, honoring
    import aliases (``import os as o``, ``from os import walk``)."""
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module, _ = aliases.get(func.value.id, (func.value.id, None))
        target = (module, func.attr)
        return target if target in _TREE_ENUMERATION_TARGETS else None
    if isinstance(func, ast.Name):
        alias_entry = aliases.get(func.id)
        if alias_entry is not None and alias_entry[1] is not None:
            target = (alias_entry[0], alias_entry[1])
            return target if target in _TREE_ENUMERATION_TARGETS else None
    return None


def test_no_bare_tree_enumeration_outside_strict_walk(tmp_path: Path) -> None:
    """No src file may call os.walk/os.fwalk/Path.rglob() for identity- or
    tamper-evidence directory enumeration — both silently drop entries whose
    scandir() fails mid-walk. Route through core.strict_walk() instead, which
    fails loudly (TreeVanishedError) on a race — or add a rationale-carrying
    allowlist entry for a site with a genuinely different risk profile.

    Known blind spots of _gather_import_aliases-based static resolution:
    getattr(module, "walk")/globals().get(...)-style dynamic dispatch,
    TYPE_CHECKING-guarded re-exports, and star imports (from os import *) all
    defeat static alias tracking and are not caught by this rule.
    """
    allowed_files = {path for path, _ in _TREE_ENUMERATION_ALLOWLIST}
    rationale_by_file = dict(_TREE_ENUMERATION_ALLOWLIST)
    violations: list[str] = []

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        if py_file in allowed_files:
            continue
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue
        aliases = _gather_import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = _resolve_tree_enumeration_target(node, aliases)
            if target is not None:
                module, attr = target
                violations.append(
                    f"  {py_file.relative_to(SRC_ROOT.parent.parent)}:{node.lineno}: "
                    f"{module}.{attr}() call outside strict_walk()"
                )
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "rglob"
                and node.func.value is not None
            ):
                violations.append(
                    f"  {py_file.relative_to(SRC_ROOT.parent.parent)}:{node.lineno}: "
                    f".rglob() call outside strict_walk()"
                )

    # Prove the rule actually fires before asserting the real-source-tree pass:
    # a deliberately-planted violation fixture must be caught.
    planted = tmp_path / "planted_violation.py"
    planted.write_text('import os\n\ndef f():\n    for _ in os.walk("/tmp"):\n        pass\n')
    planted_tree = ast.parse(planted.read_text())
    planted_aliases = _gather_import_aliases(planted_tree)
    planted_hits = [
        node
        for node in ast.walk(planted_tree)
        if isinstance(node, ast.Call)
        and _resolve_tree_enumeration_target(node, planted_aliases) is not None
    ]
    assert planted_hits, "planted-violation fixture failed to trip the rule's own detector"

    assert not violations, (
        "Raw os.walk/os.fwalk/Path.rglob() calls found outside core.strict_walk(). "
        "Route through strict_walk() — or add an allowlist entry to "
        "_TREE_ENUMERATION_ALLOWLIST with rationale (issue #4770) — for:\n"
        + "\n".join(violations)
        + "\n\nAllowlisted files and why:\n"
        + "\n".join(
            f"  {path.relative_to(SRC_ROOT.parent.parent)}: {rationale}"
            for path, rationale in rationale_by_file.items()
        )
    )


def test_no_direct_termination_dispatch_ifelse_in_run_managed() -> None:
    """run_managed_async must not contain an if/elif chain that inspects
    TerminationReason.* or signals.process_exited directly.

    The dispatch must be delegated to decide_termination_action.
    """
    process_py = SRC_ROOT / "execution" / "process" / "__init__.py"
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
        "from autoskillit.execution.linux_tracing import (\n"
        "    start_linux_tracing, resolve_trace_target)\n"
        "target = resolve_trace_target(\n"
        "    root_pid=proc.pid, expected_basename='claude')\n"
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
        "from autoskillit.execution.linux_tracing import (\n"
        "    start_linux_tracing, trace_target_from_pid)\n"
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


# ── ARCH-009: _is_plugin_installed banned from session launch path ──────────


def test_no_is_plugin_installed_in_session_launch() -> None:
    """_run_interactive_session must not call _is_plugin_installed.

    _is_plugin_installed runs 'claude plugin list' as a subprocess (up to 10s).
    This pre-launch delay widens the MCP first-call race window.
    Replacement: backend-aware prefix detection against MARKETPLACE_PREFIX.
    """
    source = Path("src/autoskillit/cli/session/_session_launch.py").read_text()
    tree = ast.parse(source)
    calls = [
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert "_is_plugin_installed" not in calls, (
        "_session_launch.py calls _is_plugin_installed — replace with "
        "detect_autoskillit_mcp_prefix(backend.capabilities) == MARKETPLACE_PREFIX"
    )


def test_no_is_plugin_installed_in_cook() -> None:
    """cook() must not call _is_plugin_installed.

    Same rationale as test_no_is_plugin_installed_in_session_launch.
    """
    source = Path("src/autoskillit/cli/session/_session_cook.py").read_text()
    tree = ast.parse(source)
    calls = [
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert "_is_plugin_installed" not in calls, (
        "_session_cook.py calls _is_plugin_installed — replace with "
        "detect_autoskillit_mcp_prefix(backend.capabilities) == MARKETPLACE_PREFIX"
    )


def test_expand_functions_call_validators() -> None:
    """expand_wps and expand_assignments must call their respective validators (ARCH-010)."""
    src = Path("src/autoskillit/planner/manifests.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "expand_wps":
            body_source = ast.dump(node)
            assert "validate_refined_assignments" in body_source
        if isinstance(node, ast.FunctionDef) and node.name == "expand_assignments":
            body_source = ast.dump(node)
            assert "validate_refined_plan" in body_source


def test_fcntl_import_allowlist() -> None:
    """Only explicitly allowlisted modules may import fcntl.

    Unauthorized fcntl usage bypasses the CampaignStateMutator lock gateway,
    creating cross-process race conditions on state files. This test enforces
    that only the established lock utilities are used.
    """
    from tests.fleet.test_state_lock_contract import _FCNTL_ALLOWED_RELATIVE_PATHS

    FCNTL_ALLOWED_MODULES = _FCNTL_ALLOWED_RELATIVE_PATHS | {
        "execution/session/_managed_headless_session_lineage_records.py",
        "hooks/guards/open_kitchen_guard.py",
        "hooks/_join_ledger.py",
        "cli/session/_session_reload.py",
    }
    violations: list[str] = []
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "fcntl":
                        rel = py_file.relative_to(SRC_ROOT)
                        if str(rel) not in FCNTL_ALLOWED_MODULES:
                            violations.append(f"  {rel}:{node.lineno}: imports fcntl")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "fcntl":
                    rel = py_file.relative_to(SRC_ROOT)
                    if str(rel) not in FCNTL_ALLOWED_MODULES:
                        violations.append(f"  {rel}:{node.lineno}: from fcntl import ...")

    assert not violations, (
        "Unauthorized fcntl imports found — all fcntl usage must go through "
        "CampaignStateMutator or the other allowlisted lock utilities:\n" + "\n".join(violations)
    )


def test_codex_unlocked_config_mutators_are_private_to_prelaunch() -> None:
    """Only the composed prelaunch transaction may import unlocked mutators."""
    unlocked_names = {
        "_ensure_codex_mcp_registered_unlocked",
        "_sync_hooks_to_codex_config_unlocked",
    }
    allowed_path = SRC_ROOT / "execution" / "backends" / "_codex_prelaunch.py"
    violations: list[str] = []

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        if py_file == allowed_path:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name in unlocked_names:
                    rel = py_file.relative_to(SRC_ROOT)
                    violations.append(f"  {rel}:{node.lineno}: imports {alias.name}")

    assert not violations, (
        "Unlocked Codex config mutators bypass the composed source-config lock; "
        "only _codex_prelaunch.py may import them:\n" + "\n".join(violations)
    )


def test_no_build_cmd_accepts_output_format_value_string() -> None:
    """No cmd builder should accept output_format_value: str — use OutputFormat enum (ARCH-011)."""
    for src_path in (
        SRC_ROOT / "execution" / "commands.py",
        SRC_ROOT / "execution" / "backends" / "claude.py",
    ):
        source = src_path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name.startswith("build_")
                and node.name.endswith("_cmd")
            ):
                param_names = [a.arg for a in node.args.args + node.args.kwonlyargs]
                assert "output_format_value" not in param_names, (
                    f"{node.name} in {src_path.name} still accepts"
                    " 'output_format_value' (raw string). "
                    f"Use 'output_format: OutputFormat' instead."
                )


# ── ARCH-010: StrEnum-to-string comparison ────────────────────────────────────


def test_arch010_detects_strenum_field_compared_to_uppercase_string(tmp_path: Path) -> None:
    """ARCH-010 fires when a known StrEnum field is compared against a raw string literal."""
    f = tmp_path / "bad.py"
    f.write_text('result.status == "completion"\n')
    violations = _scan(f)
    arch010 = [v for v in violations if v.rule_id == "ARCH-010"]
    assert arch010, (
        f"Expected ARCH-010 violation for 'status == \"completion\"', got: {violations}"
    )
    assert "status" in arch010[0].message
    assert "'completion'" in arch010[0].message


def test_arch010_detects_severity_error_uppercase_string(tmp_path: Path) -> None:
    """ARCH-010 fires for severity == "ERROR" (the original vacuous comparison bug)."""
    f = tmp_path / "bad.py"
    f.write_text('f.severity == "ERROR"\n')
    violations = _scan(f)
    arch010 = [v for v in violations if v.rule_id == "ARCH-010"]
    assert arch010, f"Expected ARCH-010 violation for 'severity == \"ERROR\"', got: {violations}"
    assert "severity" in arch010[0].message


def test_arch010_accepts_enum_member_comparison(tmp_path: Path) -> None:
    """ARCH-010 does NOT fire when comparing against an enum member (no raw string)."""
    f = tmp_path / "good.py"
    f.write_text("result.status == ChannelBStatus.COMPLETION\n")
    violations = _scan(f)
    arch010 = [v for v in violations if v.rule_id == "ARCH-010"]
    assert not arch010, f"ARCH-010 should not fire for enum member comparison: {arch010}"


def test_arch010_accepts_value_attribute_comparison(tmp_path: Path) -> None:
    """ARCH-010 does NOT fire for f.severity.value == "error" (explicit .value access)."""
    f = tmp_path / "ok.py"
    f.write_text('f.severity.value == "error"\n')
    violations = _scan(f)
    arch010 = [v for v in violations if v.rule_id == "ARCH-010"]
    assert not arch010, f"ARCH-010 should not fire for .value comparison: {arch010}"


def test_arch010_accepts_non_strenum_field(tmp_path: Path) -> None:
    """ARCH-010 does NOT fire for non-StrEnum fields."""
    f = tmp_path / "ok.py"
    f.write_text('result.name == "something"\n')
    violations = _scan(f)
    arch010 = [v for v in violations if v.rule_id == "ARCH-010"]
    assert not arch010, f"ARCH-010 should not fire for non-StrEnum field: {arch010}"


# ── ARCH-010 test-file parametrized sweep ──────────────────────────────────────


_TEST_FILES = sorted((Path(__file__).parent.parent).rglob("*.py"))
_TEST_IDS = [str(f.relative_to(Path(__file__).parent.parent)) for f in _TEST_FILES]


class TestArch010Enforcement:
    """ARCH-010 enforcement for test files (covers the original bug location)."""

    @pytest.mark.parametrize(
        "test_file",
        _TEST_FILES,
        ids=_TEST_IDS,
    )
    def test_no_strenum_string_compare_in_tests(self, test_file: Path) -> None:
        """ARCH-010: test files must not compare StrEnum fields against raw string literals."""
        # Exempt calibration snippets in test_ast_rules.py itself
        if test_file.resolve() == Path(__file__).resolve():
            pytest.skip(
                "exempt: calibration snippets in this file are intentional ARCH-010 violations"
            )
        violations = _scan_strenum_compare(test_file)
        assert not violations, (
            f"ARCH-010 violations in {test_file.relative_to(Path(__file__).parent.parent)}:\n"
            + "\n".join(f"  {v}" for v in violations)
        )


# ── ARCH-011: pyi stub re-export only ─────────────────────────────────────────


def test_pyi_stub_only_reexports() -> None:
    """ARCH-011: __init__.pyi stub files must contain only 'from .X import Y as Y' lines.

    lazy_loader.attach_stub() uses _StubVisitor which only processes ImportFrom nodes.
    Any other statement type (def, class, assign) is silently ignored, causing the
    symbol to be absent from __all__ and invisible at runtime.
    """
    violations: list[str] = []
    for pyi_file in SRC_ROOT.rglob("__init__.pyi"):
        source = pyi_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(pyi_file))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                if node.level != 1:
                    violations.append(
                        f"  {_rel(pyi_file)}:{node.lineno}: "
                        f"import level {node.level} (must be 1: from .X import Y as Y)"
                    )
                for alias in node.names:
                    if alias.asname != alias.name:
                        violations.append(
                            f"  {_rel(pyi_file)}:{node.lineno}: "
                            f"missing 'as' form: {alias.name} "
                            f"(must be {alias.name} as {alias.name})"
                        )
            else:
                violations.append(
                    f"  {_rel(pyi_file)}:{node.lineno}: "
                    f"{type(node).__name__} not allowed — only 'from .X import Y as Y' lines. "
                    f"lazy_loader.attach_stub() silently ignores {type(node).__name__} statements."
                )
    assert not violations, (
        "ARCH-011: __init__.pyi stubs must contain only relative re-export imports "
        "(from .module import Name as Name):\n" + "\n".join(violations)
    )


# ── ARCH-012: frozen-vs-live version comparison ────────────────────────────────

_FROZEN_VERSION_NAMES = frozenset({"__version__", "AUTOSKILLIT_INSTALLED_VERSION"})


def _is_frozen_version_ref(node: ast.expr) -> bool:
    """True for a bare ``__version__``/``AUTOSKILLIT_INSTALLED_VERSION`` name,
    or an attribute access ending in one of those (``autoskillit.__version__``).
    """
    if isinstance(node, ast.Name):
        return node.id in _FROZEN_VERSION_NAMES
    if isinstance(node, ast.Attribute):
        return (
            isinstance(node.value, ast.Name)
            and node.value.id == "autoskillit"
            and node.attr in _FROZEN_VERSION_NAMES
        )
    return False


def _is_live_metadata_version_call(node: ast.expr) -> bool:
    """True for ``importlib.metadata.version(...)`` or ``metadata.version(...)``.

    Deliberately does NOT match a bare ``version(...)`` call (even where
    ``from importlib.metadata import version`` is in scope) — narrowing to
    the attribute-chain form is sufficient to reproduce the one real hit
    this rule exists to catch and avoids matching unrelated same-named
    functions elsewhere in the tree.
    """
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "version":
        return False
    value = node.func.value
    if isinstance(value, ast.Name) and value.id == "metadata":
        return True
    return isinstance(value, ast.Attribute) and value.attr == "metadata"


def _check_frozen_vs_live_version_compare(src_dir: Path) -> list[str]:
    """
    ARCH-012: Detect a frozen version reference compared against a live
    ``importlib.metadata.version()`` read — reading the same fact at two
    different times and asserting the readings agree (issue #4597).

    Two shapes are detected within one function's scope:
      1. Direct: ``frozen_ref != importlib.metadata.version(...)``
      2. Same-function dataflow: ``x = importlib.metadata.version(...)``
         followed later in the same function by a comparison of ``x``
         against a frozen reference (the actual pre-fix
         ``assert_generator_process_fresh()`` shape: the live read was
         bound to a local before being compared).

    Does not track dataflow across function or module boundaries — see
    the residual-coverage note in the rectify plan for issue #4597: a
    frozen value stored on a dataclass field and compared many lines
    later is not reachable by this local pattern match, and does not
    need to be for the rule's true hit set.
    """
    violations: list[str] = []
    for py_file in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            live_vars: set[str] = set()
            for stmt in ast.walk(func):
                if isinstance(stmt, ast.Assign) and _is_live_metadata_version_call(stmt.value):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            live_vars.add(target.id)
                elif (
                    isinstance(stmt, ast.AnnAssign)
                    and stmt.value is not None
                    and isinstance(stmt.target, ast.Name)
                    and _is_live_metadata_version_call(stmt.value)
                ):
                    live_vars.add(stmt.target.id)
            for node in ast.walk(func):
                if not isinstance(node, ast.Compare):
                    continue
                operands = [node.left, *node.comparators]
                frozen_hit = any(_is_frozen_version_ref(o) for o in operands)
                live_hit = any(
                    _is_live_metadata_version_call(o)
                    or (isinstance(o, ast.Name) and o.id in live_vars)
                    for o in operands
                )
                if frozen_hit and live_hit:
                    violations.append(
                        f"{py_file.relative_to(src_dir.parent.parent)}:{node.lineno}: "
                        f"frozen version reference compared against a live "
                        f"importlib.metadata.version() read -- use the sealed "
                        f"InstallBinding instead (core.InstallBinding."
                        f"matches_current_state())"
                    )
    return violations


def test_arch012_no_frozen_version_vs_live_metadata_comparison(tmp_path: Path) -> None:
    """ARCH-012 fires on the exact pre-fix authority.py shape (issue #4597)."""
    f = tmp_path / "bad.py"
    f.write_text(
        "import importlib.metadata\n"
        "import autoskillit\n"
        "\n"
        "def assert_generator_process_fresh():\n"
        "    disk_version = importlib.metadata.version('autoskillit')\n"
        "    if disk_version != autoskillit.__version__:\n"
        "        raise StaleGeneratorError('stale')\n"
    )
    violations = _check_frozen_vs_live_version_compare(tmp_path)
    assert violations, "Expected ARCH-012 to flag the reproduced authority.py shape"
    assert "bad.py:" in violations[0]
    assert "sealed" in violations[0] and "InstallBinding" in violations[0]


def test_arch012_ignores_third_party_version_comparisons(tmp_path: Path) -> None:
    f = tmp_path / "allowed.py"
    f.write_text(
        "import importlib.metadata\n"
        "import requests\n"
        "\n"
        "def versions_match():\n"
        "    return requests.__version__ == importlib.metadata.version('requests')\n"
    )

    assert _check_frozen_vs_live_version_compare(tmp_path) == []


def test_arch012_has_no_violations_in_real_source_tree() -> None:
    """ARCH-012 scans the complete production tree without exemptions."""
    violations = _check_frozen_vs_live_version_compare(SRC_ROOT)
    assert not violations, f"ARCH-012 false positive(s) on real src/ tree: {violations}"


def test_no_bare_check_invocation_outside_run_check() -> None:
    """Every check invocation inside _collect_doctor_results must route through
    _run_check via results.extend(...) — a bare call, or an append(...) instead
    of extend(...), means one check's exception (or a nested-list bug) can
    crash the other 54 unrelated checks (see #4768)."""
    doctor_init = SRC_ROOT / "cli" / "doctor" / "__init__.py"
    tree = ast.parse(doctor_init.read_text())
    aliases = _gather_import_aliases(tree)

    collect_node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_collect_doctor_results"
    )

    violations: list[str] = []
    for node in ast.walk(collect_node):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "extend"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "results"
        ):
            continue
        if not node.args or not isinstance(node.args[0], ast.Call):
            violations.append(
                f"  __init__.py:{node.lineno}: results.{node.func.attr} arg is not a call"
            )
            continue
        inner = node.args[0]
        # Bare-name check first (mirrors _is_funnel_call) — a from-import alias
        # table would otherwise resolve the bare `_run_check` name itself to
        # "_doctor_types._run_check" and never match the literal below.
        resolved: str | None
        if isinstance(inner.func, ast.Name):
            resolved = inner.func.id
        else:
            dotted = _dotted_name(inner.func, aliases)
            resolved = dotted.rsplit(".", 1)[-1] if dotted else None
        if resolved not in {"_run_check", "run_check"}:
            violations.append(
                f"  __init__.py:{node.lineno}: bare check invocation, not wrapped in _run_check"
            )
        elif node.func.attr != "extend":
            violations.append(
                f"  __init__.py:{node.lineno}: results.append(_run_check(...)) must be "
                "results.extend(...) — _run_check always returns a list"
            )

    assert not violations, (
        "Every check invocation inside _collect_doctor_results must be "
        "results.extend(_run_check(functools.partial(...))):\n" + "\n".join(violations)
    )
