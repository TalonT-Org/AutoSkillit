"""Shared helpers for arch test suite -- AST visitor infrastructure and import analysis utils."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.arch._rules import (
    _ASYNCIO_PIPE_EXEMPT,
    _BROAD_EXCEPT_EXEMPT,
    _LOGGER_METHODS,
    _PRINT_EXEMPT,
    _RULE,
    _SENSITIVE_KEYWORDS,
    _rel,  # noqa: F401 -- re-exported for test_layer_enforcement and test_subpackage_isolation
    RuleDescriptor,
    Violation,
)

# ── Path constants ────────────────────────────────────────────────────────────
# Must be absolute for xdist compatibility -- do not use relative paths.
SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"
PROCESS_PY = SRC_ROOT / "execution" / "process.py"
PROCESS_KILL_PY = SRC_ROOT / "execution" / "_process_kill.py"
PROCESS_MONITOR_PY = SRC_ROOT / "execution" / "_process_monitor.py"
PROCESS_RACE_PY = SRC_ROOT / "execution" / "_process_race.py"

# ── Section A: AST visitor infrastructure ─────────────────────────────────────

_BROAD_EXCEPTION_TYPES: frozenset[str] = frozenset({"Exception", "BaseException"})


def _has_log_call(body: list[ast.stmt]) -> bool:
    """Return True if body contains any logger.<method>(...) call."""
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
        # Rule ARCH-001 (visitor): no print() -- ruff cannot enforce this in production-only files
        if not self._print_exempt and isinstance(node.func, ast.Name) and node.func.id == "print":
            self._add(node, _RULE["ARCH-001"], "print() call -- use logger instead")

        # Rule ARCH-002 (visitor): no sensitive kwargs in logger calls -- not expressible in ruff
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
                                    f"'{var_name}' -- use structlog kwargs instead",
                                )

        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Rule ARCH-003 (visitor): broad except without logger or re-raise -> silent swallow."""
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
                " -- add logger.warning/error with exc_info=True",
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


# ── Section B: Import analysis helpers ────────────────────────────────────────

_SOURCE_FILES = sorted(SRC_ROOT.rglob("*.py"))


def _extract_module_level_internal_imports(path: Path) -> list[tuple[str, int]]:
    """Return (imported_module_stem, lineno) for all autoskillit imports at module level.

    Only iterates tree.body (module-level statements). Import nodes inside
    function or class bodies are intentionally excluded.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    results: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "autoskillit" and len(parts) > 1:
                results.append((parts[1], node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "autoskillit" and len(parts) > 1:
                    results.append((parts[1], node.lineno))
    return results


def _is_mcp_tool_decorator(node: ast.expr) -> bool:
    """Return True if node represents @mcp.tool or @mcp.tool(...)."""
    if isinstance(node, ast.Attribute) and node.attr == "tool":
        return True
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "tool"
    ):
        return True
    return False


def _runtime_import_froms(path: Path) -> list[ast.ImportFrom]:
    """Return ImportFrom nodes not inside a TYPE_CHECKING guard."""
    tree = ast.parse(path.read_text())
    result: list[ast.ImportFrom] = []

    def _walk(stmts: list) -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.ImportFrom):
                result.append(stmt)
            elif isinstance(stmt, ast.If):
                test = stmt.test
                is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                    isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                )
                if not is_tc:
                    _walk(stmt.body)
                    _walk(stmt.orelse)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _walk(stmt.body)
            elif isinstance(stmt, ast.ClassDef):
                _walk(stmt.body)
            elif isinstance(stmt, ast.Try):
                _walk(stmt.body)
                for handler in stmt.handlers:
                    _walk(handler.body)
                _walk(stmt.orelse)
                _walk(getattr(stmt, "finalbody", []))

    _walk(tree.body)
    return result
