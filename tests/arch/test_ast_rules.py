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
  - cli/app.py, cli/_doctor.py, cli/_chefs_hat.py: may use print() for user-facing terminal output
"""

from __future__ import annotations

import ast
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
        "Use _atomic_write(path, content) from autoskillit.core.io instead:\n"
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


def test_arch004_violation_str_includes_ds002(tmp_path: Path) -> None:
    """Regression guard: ARCH-004 violation str must include DS-002 after the fix."""
    f = tmp_path / "bad.py"
    f.write_text("import asyncio\nval = asyncio.PIPE\n")
    violations = _scan(f)
    pipe_violations = [v for v in violations if "asyncio.PIPE" in v.message]
    assert pipe_violations
    s = str(pipe_violations[0])
    assert "DS-002" in s
    assert "[ARCH-004 / process-flow / DS-002]" in s


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
        ("_chefs_hat.py", "chefs_hat"),
        ("_llm_triage.py", "_triage_batch"),
        ("commands.py", "build_interactive_cmd"),
        ("commands.py", "build_headless_cmd"),
        ("commands.py", "build_subrecipe_cmd"),
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


def test_monkeypatch_targets_do_not_bypass_package_reexports() -> None:
    """Every monkeypatch.setattr path must target the namespace production code resolves.

    When autoskillit.X re-exports 'name' from autoskillit.X.submodule via __init__.py,
    patching autoskillit.X.submodule.name does NOT affect autoskillit.X.name.
    All patches of the form autoskillit.X.submodule.name, where X is a sub-package
    that re-exports 'name' FROM that exact submodule, are wrong and must be corrected.

    Note: patching autoskillit.X.B.name where X imports 'name' from a DIFFERENT submodule
    (not B) is correct -- it targets the local binding in B, which is the namespace that
    module B's own functions resolve.
    """
    import importlib
    import inspect
    import re

    # Match string literals in monkeypatch.setattr("autoskillit.A.B.C", ...)
    # where A is a sub-package, B is a submodule, C is the name.
    pattern = re.compile(
        r'monkeypatch\.setattr\s*\(\s*["\']'
        r"(autoskillit\.\w+\.\w+\.\w+)"
        r'["\']'
    )

    violations: list[str] = []
    tests_dir = Path(__file__).parent.parent

    for test_file in sorted(tests_dir.glob("**/test_*.py")):
        source = test_file.read_text()
        for match in pattern.finditer(source):
            full_path = match.group(1)
            # Split: autoskillit . pkg . submodule . name
            parts = full_path.split(".")
            if len(parts) != 4:
                continue
            _, pkg, submod, name = parts
            parent_pkg = f"autoskillit.{pkg}"
            try:
                parent_mod = importlib.import_module(parent_pkg)
            except ImportError:
                continue
            if not hasattr(parent_mod, name):
                continue
            # Refine: only flag if the parent pkg actually imports 'name' FROM this
            # exact submodule. If it imports 'name' from a different module (e.g.
            # autoskillit.migration imports applicable_migrations from .loader, not
            # .engine), then the patch targets a local binding in 'submod' -- which
            # is the correct mock target for module-level imports in that submodule.
            try:
                parent_source = inspect.getsource(parent_mod)
                tree = ast.parse(parent_source)
            except Exception:
                # Can't inspect source -- conservatively flag as violation.
                imports_from_this_submod = True
            else:
                imports_from_this_submod = False
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    is_relative_from_submod = node.level == 1 and node.module == submod
                    is_absolute_from_submod = (
                        node.level == 0 and node.module == f"autoskillit.{pkg}.{submod}"
                    )
                    if is_relative_from_submod or is_absolute_from_submod:
                        for alias in node.names:
                            if (alias.asname or alias.name) == name:
                                imports_from_this_submod = True
                                break
                    if imports_from_this_submod:
                        break
            if imports_from_this_submod:
                line_no = source[: match.start()].count("\n") + 1
                violations.append(
                    f"{test_file.name}:{line_no}: patches {full_path!r} "
                    f"but '{name}' is re-exported at '{parent_pkg}.{name}'. "
                    f"Patch '{parent_pkg}.{name}' instead."
                )

    assert not violations, "Monkeypatch paths bypass package re-exports:\n" + "\n".join(
        f"  {v}" for v in violations
    )


# ── P14-2: Sub-package __init__.py facade enforcement ─────────────────────────


def test_hooks_are_stdlib_only() -> None:
    """Hook scripts must not import from autoskillit.* — they run outside the venv."""
    hooks_dir = SRC_ROOT / "hooks"
    violations: list[str] = []
    for py_file in sorted(hooks_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("autoskillit")
            ):
                violations.append(f"  {py_file.name}:{node.lineno}: imports from {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("autoskillit"):
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
