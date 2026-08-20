"""A-10 regrowth guard: no *new* function-local ``autoskillit`` import inside
an ``except``/``finally`` block.

Issue #4597's finding #13: a deleted or replaced install tree turns one
crash into an unrecoverable one when the error-handling path itself has to
resolve a not-yet-imported ``autoskillit`` submodule. A-10 fixed today's
known instances with a startup warm (``fleet._startup_warm``); this guard is
the "stops the class from regrowing" half the plan calls for — new
function-local imports of this shape are refused unless explicitly
allowlisted with a one-line rationale, mirroring
``tests/arch/test_durable_artifact_writers_guard.py``'s
``_NON_HOOK_ALLOWLIST`` pattern.

Completeness claim (stated honestly): this is a static AST scan, keyed by
``(file, lineno)``. It cannot see imports assembled through indirection
(``importlib.import_module``, a locally-defined wrapper that itself imports),
and a later unrelated edit that shifts line numbers in an allowlisted file
will make this guard fail closed rather than silently drift — that is the
intended failure mode for an architectural guard, not a bug in it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

#: (relative file path, line number) pairs already carrying a function-local
#: ``autoskillit`` import inside an ``except``/``finally`` block, each with a
#: one-line rationale. A new entry requires the same rationale discipline;
#: these are the same sites the fleet startup warm preloads
#: preloads, so a deleted/replaced tree cannot turn the import itself into a
#: second failure.
_ALLOWLIST: frozenset[tuple[str, int]] = frozenset(
    {
        # Crash-telemetry recording needs the very types used to describe
        # the failure it is inside of.
        ("pipeline/background.py", 81),
        # Config re-read on a config-load failure path.
        ("cli/_init_helpers.py", 550),
        # Fleet state re-import on a dispatch failure/cleanup path.
        ("fleet/_api.py", 381),
        ("fleet/_api.py", 1225),
        ("fleet/_api.py", 1262),
        # Label-cleanup helper, imported only once a dispatch has actually failed.
        ("fleet/_api.py", 1308),
        # Kitchen tracker-authority import inside a server lifespan
        # teardown/cleanup path.
        ("server/_lifespan/_lifespan.py", 199),
    }
)


class _ExceptionPathImportVisitor(ast.NodeVisitor):
    """Tracks whether the current statement is nested inside an
    ``except``/``finally`` block and records ``autoskillit`` imports found
    there."""

    def __init__(self, rel_path: str, violations: list[tuple[str, int]]) -> None:
        self.rel_path = rel_path
        self.violations = violations
        self._depth = 0

    def visit_Try(self, node: ast.Try) -> None:
        for stmt in node.body:
            self.visit(stmt)
        for handler in node.handlers:
            self._depth += 1
            for stmt in handler.body:
                self.visit(stmt)
            self._depth -= 1
        for stmt in node.orelse:
            self.visit(stmt)
        self._depth += 1
        for stmt in node.finalbody:
            self.visit(stmt)
        self._depth -= 1

    def visit_Import(self, node: ast.Import) -> None:
        if self._depth > 0:
            for alias in node.names:
                if alias.name == "autoskillit" or alias.name.startswith("autoskillit."):
                    self.violations.append((self.rel_path, node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._depth > 0 and node.module is not None:
            if node.module == "autoskillit" or node.module.startswith("autoskillit."):
                self.violations.append((self.rel_path, node.lineno))
        self.generic_visit(node)


def _scan() -> list[tuple[str, int]]:
    violations: list[tuple[str, int]] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel_path = str(path.relative_to(SRC_ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _ExceptionPathImportVisitor(rel_path, violations).visit(tree)
    return violations


def test_no_new_function_local_autoskillit_import_in_except_or_finally() -> None:
    """Fails on any function-local ``autoskillit`` import inside an
    ``except``/``finally`` block that is not in ``_ALLOWLIST`` -- the case
    this guard exists to catch: a fresh instance of finding #13's class,
    added without preloading it via A-10's startup warm.
    """
    found = set(_scan())
    unexpected = found - _ALLOWLIST
    assert not unexpected, (
        "New function-local `autoskillit` import(s) found inside an "
        "except/finally block, not covered by fleet._startup_warm and not "
        f"in this guard's allowlist: {sorted(unexpected)}. Either preload "
        "the module in fleet._startup_warm and add a rationale-carrying "
        "allowlist entry here, or restructure the import to not be "
        "function-local on a failure path."
    )


def test_allowlist_entries_still_exist() -> None:
    """An allowlist entry whose site was refactored away is a silent gap:
    it grandfathers nothing and just widens what the first test permits.
    """
    found = set(_scan())
    stale = _ALLOWLIST - found
    assert not stale, (
        f"Allowlist entries no longer found by the scan: {sorted(stale)}. "
        "Remove them -- a stale entry only widens what this guard permits."
    )
