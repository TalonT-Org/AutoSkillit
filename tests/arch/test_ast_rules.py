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
  - cli/app.py, cli/_doctor.py: may use print() for user-facing terminal output
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tests.arch._rules import (
    _ASYNCIO_PIPE_EXEMPT,
    _BROAD_EXCEPT_EXEMPT,
    _DISPATCH_TABLE_EXEMPT_FUNCTIONS,
    _LOGGER_METHODS,
    _PRINT_EXEMPT,
    _RULE,
    _SENSITIVE_KEYWORDS,
    RuleDescriptor,
    Violation,
)

SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"

_BROAD_EXCEPTION_TYPES: frozenset[str] = frozenset({"Exception", "BaseException"})


def _has_log_call(body: list[ast.stmt]) -> bool:
    """Return True if body contains any logger.<method>(…) call."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _LOGGER_METHODS
        ):
            return True
    return False


def _has_reraise(body: list[ast.stmt]) -> bool:
    """Return True if body contains any raise statement (re-raise pattern)."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return True
    return False


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(Path(__file__).parent.parent.parent))
    except ValueError:
        return str(path)


class ArchitectureViolationVisitor(ast.NodeVisitor):
    def __init__(self, filepath: Path) -> None:
        self.filepath = filepath
        self.violations: list[Violation] = []
        self._print_exempt = filepath.name in _PRINT_EXEMPT
        self._asyncio_pipe_exempt = filepath.name in _ASYNCIO_PIPE_EXEMPT
        self._broad_except_exempt = filepath.name in _BROAD_EXCEPT_EXEMPT

    def _add(self, node: ast.AST, rule: RuleDescriptor, message: str) -> None:
        self.violations.append(
            Violation(
                self.filepath,
                node.lineno,  # type: ignore[attr-defined]
                node.col_offset,  # type: ignore[attr-defined]
                message,
                rule.rule_id,
                rule.lens,
            )
        )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Rule ARCH-004 (visitor): asyncio.PIPE is banned outside process.py."""
        if (
            not self._asyncio_pipe_exempt
            and node.attr == "PIPE"
            and isinstance(node.value, ast.Name)
            and node.value.id == "asyncio"
        ):
            self._add(
                node,
                _RULE["ARCH-004"],
                "asyncio.PIPE is banned; use create_temp_io() from process_lifecycle",
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Rule ARCH-001 (visitor): no print() — ruff cannot enforce this in production-only files
        if not self._print_exempt and isinstance(node.func, ast.Name) and node.func.id == "print":
            self._add(node, _RULE["ARCH-001"], "print() call — use logger instead")

        # Rule ARCH-002 (visitor): no sensitive kwargs in logger calls — not expressible in ruff
        if isinstance(node.func, ast.Attribute) and node.func.attr in _LOGGER_METHODS:
            for kw in node.keywords:
                if kw.arg and any(s in kw.arg.lower() for s in _SENSITIVE_KEYWORDS):
                    self._add(
                        node, _RULE["ARCH-002"], f"sensitive kwarg '{kw.arg}' passed to logger"
                    )

        # Rule ARCH-005 (visitor): get_logger must be called with __name__
        func = node.func
        func_name = func.id if isinstance(func, ast.Name) else None
        if func_name == "get_logger" and node.args:
            first_arg = node.args[0]
            if not (isinstance(first_arg, ast.Name) and first_arg.id == "__name__"):
                self._add(
                    node,
                    _RULE["ARCH-005"],
                    "get_logger() must be called with __name__, not a literal or other value",
                )

        # Rule ARCH-006 (visitor): no f-string with sensitive variable names in logger args
        if isinstance(func, ast.Attribute) and func.attr in _LOGGER_METHODS:
            for arg in node.args:
                if isinstance(arg, ast.JoinedStr):  # f-string
                    for fv in ast.walk(arg):
                        if isinstance(fv, ast.FormattedValue):
                            val = fv.value
                            var_name = None
                            if isinstance(val, ast.Name):
                                var_name = val.id
                            elif isinstance(val, ast.Attribute):
                                var_name = val.attr
                            if var_name and any(
                                kw in var_name.lower() for kw in _SENSITIVE_KEYWORDS
                            ):
                                self._add(
                                    node,
                                    _RULE["ARCH-006"],
                                    f"f-string log message interpolates sensitive variable "
                                    f"'{var_name}' — use structlog kwargs instead",
                                )

        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Rule ARCH-003 (visitor): broad except without logger or re-raise → silent swallow."""
        is_broad = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id in _BROAD_EXCEPTION_TYPES
        )
        if (
            is_broad
            and not self._broad_except_exempt
            and not _has_log_call(node.body)
            and not _has_reraise(node.body)
        ):
            type_label = ast.unparse(node.type) if node.type else "bare except"
            self._add(
                node,
                _RULE["ARCH-003"],
                f"broad except ({type_label}) without any logger call"
                " — add logger.warning/error with exc_info=True",
            )
        self.generic_visit(node)


def _scan(path: Path) -> list[Violation]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [Violation(path, exc.lineno or 0, 0, f"SyntaxError: {exc.msg}")]
    visitor = ArchitectureViolationVisitor(filepath=path)
    visitor.visit(tree)
    return visitor.violations


_SOURCE_FILES = sorted(SRC_ROOT.rglob("*.py"))


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
                    f"{node.name}() dispatches on {tr_values} via if/elif — "
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
                    f"{node.name}() dispatches on {cc_values} via if/elif — "
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
        ("_triage.py", "triage_staleness"),
        ("commands.py", "build_interactive_cmd"),
        ("commands.py", "build_headless_cmd"),
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


class TestNoAsyncioRuntimePrimitives:
    """REQ-MIG-001: asyncio primitives are removed from execution/process.py call sites."""

    def test_no_asyncio_sleep_calls(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "asyncio.sleep(" not in source

    def test_no_asyncio_to_thread_calls(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "asyncio.to_thread(" not in source

    def test_no_asyncio_create_subprocess_exec(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "asyncio.create_subprocess_exec(" not in source

    def test_no_asyncio_event_instantiation(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "asyncio.Event()" not in source

    def test_no_asyncio_wait_for_calls(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "asyncio.wait_for(" not in source

    def test_no_asyncio_get_event_loop_time(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "asyncio.get_event_loop()" not in source

    def test_no_asyncio_get_running_loop_run_in_executor(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "asyncio.get_running_loop()" not in source

    def test_no_asyncio_cancelled_error_reference(self):
        """REQ-BEH-010: asyncio.CancelledError must not appear in process.py.

        anyio raises anyio.get_cancelled_exc_class() (trio.Cancelled on the trio
        backend), not asyncio.CancelledError. Catching asyncio.CancelledError in
        a finally/except block would silently miss cancellations on trio, breaking
        the anyio backend contract.
        """
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "asyncio.CancelledError" not in source


class TestAnyioPrimitivesUsed:
    """REQ-MIG-002..004: anyio primitives replace the removed asyncio calls."""

    def test_anyio_to_thread_run_sync_present(self):
        source = Path("src/autoskillit/execution/_process_kill.py").read_text()
        assert "anyio.to_thread.run_sync(" in source

    def test_anyio_sleep_present(self):
        source = Path("src/autoskillit/execution/_process_monitor.py").read_text()
        assert "anyio.sleep(" in source

    def test_time_monotonic_replaces_event_loop_time(self):
        source = Path("src/autoskillit/execution/_process_monitor.py").read_text()
        assert ".monotonic()" in source

    def test_anyio_open_process_present(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "anyio.open_process(" in source

    def test_anyio_event_present(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "anyio.Event()" in source

    def test_anyio_move_on_after_present(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "anyio.move_on_after(" in source


class TestProcTypeAnnotationUpdated:
    """REQ-MIG-005/scan_done_signals: proc annotation is anyio.abc.Process, not asyncio."""

    def test_scan_done_signals_proc_annotation_not_asyncio_subprocess(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "asyncio.subprocess.Process" not in source

    def test_scan_done_signals_proc_annotation_is_anyio(self):
        source = Path("src/autoskillit/execution/_process_race.py").read_text()
        assert "anyio.abc.Process" in source


# ── P14-2: Sub-package __init__.py facade enforcement ─────────────────────────


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
