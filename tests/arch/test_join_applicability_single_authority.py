"""Pin join applicability to its authority and close the call-site inventories.

Import-linter checks dependencies; this AST scan enforces the call-site invariants.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import pytest

pytestmark = pytest.mark.medium

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

_EXPECTED_DIRECT_ADMISSION_CALLERS = frozenset(
    {
        "server/tools/tools_kitchen/_declare_join_batch.py::_admit_join_binding",
        "server/tools/tools_execution/_fixed_batch_handlers.py::_admit_managed_parent_binding",
    }
)

_EXPECTED_HOOK_JOIN_SURFACES = frozenset(
    {
        "hooks/guards/join_claim_guard.py::_resolve_required_join_session",
        "hooks/guards/join_settle_guard.py::_resolve_required_join_session",
        "hooks/guards/join_followup_guard.py::_resolve_required_join_session",
        "hooks/guards/join_stop_guard.py::main",
        "hooks/guards/background_exec_guard.py::_join_bound_denial",
        "hooks/skill_load_post_hook.py::_join_context_parts",
    }
)

_EXPECTED_WAVE_OPENERS = frozenset(
    {
        "server/tools/tools_kitchen/_declare_join_batch.py::_declare_join_batch_handler",
        # The managed supervisor owns the full wave lifecycle, so it cannot strand a wave.
        "server/tools/tools_execution/_managed_fixed_batch.py::run",
    }
)

_EXPECTED_COOK_BYPASS_CALLERS = frozenset(
    {"server/tools/tools_kitchen/_declare_join_batch.py::_declare_join_batch_handler"}
)


class _CallCollector(ast.NodeVisitor):
    def __init__(self, names: frozenset[str] | set[str]) -> None:
        self.names = names
        self.function_stack: list[str] = []
        self.calls: defaultdict[str, list[tuple[str, int]]] = defaultdict(list)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        callee: str | None = None
        if isinstance(node.func, ast.Name) and node.func.id in self.names:
            callee = node.func.id
        elif isinstance(node.func, ast.Attribute) and node.func.attr in self.names:
            callee = node.func.attr
        elif (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in self.names
        ):
            callee = node.args[1].value

        if callee is not None:
            function_name = self.function_stack[-1] if self.function_stack else "<module>"
            self.calls[function_name].append((callee, node.lineno))
        self.generic_visit(node)


def _calls(source: str, names: frozenset[str] | set[str]) -> dict[str, list[tuple[str, int]]]:
    collector = _CallCollector(names)
    collector.visit(ast.parse(source))
    return dict(collector.calls)


def _call_inventory(
    names: frozenset[str] | set[str], *, excluded_paths: frozenset[str] = frozenset()
) -> dict[str, list[tuple[str, int]]]:
    inventory: dict[str, list[tuple[str, int]]] = {}
    for path in _SOURCE_ROOT.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        relative = path.relative_to(_SOURCE_ROOT).as_posix()
        if relative in excluded_paths:
            continue
        for function_name, calls in _calls(path.read_text(), names).items():
            inventory[f"{relative}::{function_name}"] = calls
    return inventory


def test_join_requirement_primitives_are_confined_to_the_authority() -> None:
    callers = _call_inventory(
        {"session_join_required", "session_join_admission", "admit_join"},
        excluded_paths=frozenset({"hooks/_runtime/_hook_settings.py"}),
    )

    assert set(callers) == _EXPECTED_DIRECT_ADMISSION_CALLERS, (
        "route this surface through hook_join_applicability / record_session_cook_join_bypass, "
        "or add it here with a justification."
    )


def test_hook_join_surfaces_route_through_the_applicability_authority() -> None:
    callers = _call_inventory(
        {"hook_join_applicability"},
        excluded_paths=frozenset({"hooks/_runtime/_hook_settings.py"}),
    )

    assert set(callers) == _EXPECTED_HOOK_JOIN_SURFACES


def test_native_wave_openers_consult_cook_applicability_before_opening() -> None:
    names = {"declare_batch", "open_or_replay", "record_session_cook_join_bypass"}
    inventory: dict[str, list[tuple[str, int]]] = {}
    for path in _SOURCE_ROOT.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        relative = path.relative_to(_SOURCE_ROOT).as_posix()
        if relative == "hooks/_join_ledger.py" or relative.startswith("hooks/_join/"):
            continue
        for function_name, calls in _calls(path.read_text(), names).items():
            inventory[f"{relative}::{function_name}"] = calls

    assert set(inventory) == _EXPECTED_WAVE_OPENERS

    checked_opener = (
        "server/tools/tools_kitchen/_declare_join_batch.py::_declare_join_batch_handler"
    )
    calls = inventory[checked_opener]
    bypass_lines = [line for callee, line in calls if callee == "record_session_cook_join_bypass"]
    opener_lines = [line for callee, line in calls if callee == "declare_batch"]
    assert bypass_lines and opener_lines
    assert bypass_lines[0] < opener_lines[0]


def test_cook_predicate_is_consumed_only_through_the_authority() -> None:
    predicate_callers = _call_inventory(
        {"is_authenticated_top_level_cook", "is_authenticated_top_level_cook_session"},
        excluded_paths=frozenset(
            {
                "hooks/_runtime/_hook_settings.py",
                "hooks/_runtime/_session_registry_bridge.py",
            }
        ),
    )
    bypass_callers = _call_inventory({"record_session_cook_join_bypass"})

    assert not predicate_callers
    assert set(bypass_callers) == _EXPECTED_COOK_BYPASS_CALLERS


def test_hook_modules_reading_join_state_are_inventoried() -> None:
    excluded = {
        "hooks/_runtime/_hook_settings.py",
        "hooks/_session_binding.py",
        "hooks/_join_ledger.py",
        "hooks/_runtime/__init__.py",
    }
    expected_modules = {entry.split("::", maxsplit=1)[0] for entry in _EXPECTED_HOOK_JOIN_SURFACES}
    observed_modules: set[str] = set()

    for path in (_SOURCE_ROOT / "hooks").rglob("*.py"):
        if path.name == "__init__.py":
            continue
        relative = path.relative_to(_SOURCE_ROOT).as_posix()
        if relative in excluded or relative.startswith("hooks/_join/"):
            continue
        tree = ast.parse(path.read_text())
        reads_join_required = any(
            isinstance(node, ast.Attribute)
            and node.attr == "join_required"
            and isinstance(node.ctx, ast.Load)
            for node in ast.walk(tree)
        )
        imports_join_ledger = any(
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.rsplit(".", maxsplit=1)[-1] == "_join_ledger"
            for node in ast.walk(tree)
        )
        if reads_join_required or imports_join_ledger:
            observed_modules.add(relative)

    assert observed_modules <= expected_modules, (
        "hook modules reading join state must route through an inventoried surface: "
        f"{sorted(observed_modules - expected_modules)}"
    )


def test_detector_canaries_cover_call_forms_scopes_and_line_order() -> None:
    source = (
        'session_join_required("module", "sid")\n'
        "def outer():\n"
        '    session_join_admission("cwd", "sid")\n'
        '    authority.admit_join("cwd", "sid")\n'
        '    getattr(authority, "hook_join_applicability")("cwd", "sid")\n'
        "    def inner():\n"
        '        session_join_required("cwd", "sid")\n'
        "def opener():\n"
        '    record_session_cook_join_bypass({}, "cwd", "sid")\n'
        '    declare_batch("sid", [])\n'
    )
    calls = _calls(
        source,
        {
            "session_join_required",
            "session_join_admission",
            "admit_join",
            "hook_join_applicability",
            "record_session_cook_join_bypass",
            "declare_batch",
        },
    )

    assert calls["<module>"] == [("session_join_required", 1)]
    assert calls["outer"] == [
        ("session_join_admission", 3),
        ("admit_join", 4),
        ("hook_join_applicability", 5),
    ]
    assert calls["inner"] == [("session_join_required", 7)]
    assert "session_join_required" not in dict(calls["outer"])
    opener_calls = calls["opener"]
    assert opener_calls[0] == ("record_session_cook_join_bypass", 9)
    assert opener_calls[1] == ("declare_batch", 10)
    assert opener_calls[0][1] < opener_calls[1][1]
