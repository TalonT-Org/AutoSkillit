"""Closed-world outcome-ledger coverage for terminal workspace tool returns."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

TOOLS_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "server"
    / "tools"
    / "tools_workspace.py"
)
COMMIT_OUTCOME_PATH = TOOLS_PATH.with_name("_commit_outcome.py")


@dataclass(frozen=True)
class _ReturnInventory:
    helpers: int
    outer_finish: int
    outer_unavailable: int
    helper_returns: int
    ledger_record_calls: int
    outcome_authority_calls: int = field(default=0, repr=False)


_EXPECTED_INVENTORIES = {
    "test_check": _ReturnInventory(
        helpers=1,
        outer_finish=6,
        outer_unavailable=1,
        helper_returns=1,
        ledger_record_calls=1,
    ),
    "commit_files": _ReturnInventory(
        helpers=1,
        outer_finish=11,
        outer_unavailable=1,
        helper_returns=1,
        ledger_record_calls=0,
        outcome_authority_calls=1,
    ),
}


class _ReturnCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.returns: list[ast.Return] = []

    def visit_Return(self, node: ast.Return) -> None:
        self.returns.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return


def _returns_in(statements: list[ast.stmt]) -> list[ast.Return]:
    collector = _ReturnCollector()
    for statement in statements:
        collector.visit(statement)
    return collector.returns


def _calls_local_finish(node: ast.Return) -> bool:
    return (
        isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_finish"
    )


def _outer_unavailable_returns(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[ast.Return]:
    """Return sites in the tool's outer ``except Exception`` handler."""
    allowed: set[ast.Return] = set()
    for statement in function.body:
        if not isinstance(statement, ast.Try):
            continue
        for handler in statement.handlers:
            if not (isinstance(handler.type, ast.Name) and handler.type.id == "Exception"):
                continue
            allowed.update(_returns_in(handler.body))
    return allowed


def _ledger_record_calls(helper: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    calls = 0
    for node in ast.walk(helper):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "record"
        ):
            continue
        if ast.unparse(node.func.value).endswith("workspace_outcome_ledger"):
            calls += 1
    return calls


def _outcome_authority_calls(helper: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_finish_commit_response"
        for node in ast.walk(helper)
    )


def _audit_function(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    expected: _ReturnInventory,
) -> list[str]:
    # Walk all descendants so a helper nested inside a ``try`` (the current
    # ``test_check`` / ``commit_files`` shape) is still discovered. The
    # reviewer-suggested direct-child restriction is incompatible with the
    # current source layout — restructuring the tools to hoist _finish out of
    # the try block is the prerequisite for that change.
    helpers = [
        node
        for node in ast.walk(function)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_finish"
    ]
    outer_returns = _returns_in(function.body)
    unavailable_returns = _outer_unavailable_returns(function)
    violations = [
        f"{function.name}: ordinary outer return at line {node.lineno} must call local _finish"
        for node in outer_returns
        if not _calls_local_finish(node) and node not in unavailable_returns
    ]
    helper_returns = sum(len(_returns_in(helper.body)) for helper in helpers)
    ledger_calls = sum(_ledger_record_calls(helper) for helper in helpers)
    outcome_calls = sum(_outcome_authority_calls(helper) for helper in helpers)
    observed = _ReturnInventory(
        helpers=len(helpers),
        outer_finish=sum(_calls_local_finish(node) for node in outer_returns),
        outer_unavailable=sum(node in unavailable_returns for node in outer_returns),
        helper_returns=helper_returns,
        ledger_record_calls=ledger_calls,
        outcome_authority_calls=outcome_calls,
    )
    if observed != expected:
        violations.append(
            f"{function.name}: return/helper inventory changed: "
            f"expected {expected!r}; observed {observed!r}"
        )
    return violations


def _target_functions(source: str) -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(source)
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in _EXPECTED_INVENTORIES
    }


def test_workspace_tools_have_closed_world_outcome_coverage() -> None:
    functions = _target_functions(TOOLS_PATH.read_text(encoding="utf-8"))

    assert functions.keys() == _EXPECTED_INVENTORIES.keys()
    violations = [
        violation
        for name, expected in _EXPECTED_INVENTORIES.items()
        for violation in _audit_function(functions[name], expected)
    ]
    assert not violations, "Workspace outcome coverage violations:\n" + "\n".join(violations)


def test_commit_outcome_authority_records_once() -> None:
    tree = ast.parse(COMMIT_OUTCOME_PATH.read_text(encoding="utf-8"))
    helpers = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_finish_commit_response"
    ]
    assert len(helpers) == 1
    assert _ledger_record_calls(helpers[0]) == 1


_CANARY_EXPECTED = _ReturnInventory(
    helpers=1,
    outer_finish=1,
    outer_unavailable=1,
    helper_returns=1,
    ledger_record_calls=1,
)


def _canary_source(ordinary_return: str) -> str:
    return (
        "async def probe(flag):\n"
        "    def _finish(response):\n"
        "        tool_ctx.workspace_outcome_ledger.record(response)\n"
        "        return response\n"
        "    try:\n"
        "        if flag:\n"
        f"            {ordinary_return}\n"
        "    except Exception:\n"
        "        return json.dumps({})\n"
    )


def _audit_canary(source: str, expected: _ReturnInventory = _CANARY_EXPECTED) -> list[str]:
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.AsyncFunctionDef)
    return _audit_function(function, expected)


def test_detector_rejects_bare_ordinary_return() -> None:
    assert _audit_canary(_canary_source("return {}")) == [
        "probe: ordinary outer return at line 7 must call local _finish",
        "probe: return/helper inventory changed: expected "
        "_ReturnInventory(helpers=1, outer_finish=1, outer_unavailable=1, "
        "helper_returns=1, ledger_record_calls=1); observed "
        "_ReturnInventory(helpers=1, outer_finish=0, outer_unavailable=1, "
        "helper_returns=1, ledger_record_calls=1)",
    ]


def test_detector_accepts_registered_compliant_return() -> None:
    assert _audit_canary(_canary_source("return _finish({})")) == []


def test_detector_rejects_new_compliant_unregistered_return() -> None:
    source = _canary_source("return _finish({})").replace(
        "    except Exception:\n",
        "        return _finish({})\n    except Exception:\n",
    )

    assert _audit_canary(source) == [
        "probe: return/helper inventory changed: expected "
        "_ReturnInventory(helpers=1, outer_finish=1, outer_unavailable=1, "
        "helper_returns=1, ledger_record_calls=1); observed "
        "_ReturnInventory(helpers=1, outer_finish=2, outer_unavailable=1, "
        "helper_returns=1, ledger_record_calls=1)"
    ]


def _combined_output_fallback_violations(source: str) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_combined_process_output"
        ):
            continue
        fallback = node.args[2] if len(node.args) >= 3 else None
        if fallback is None:
            fallback = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "fallback"),
                None,
            )
        has_text = (
            isinstance(fallback, ast.Constant)
            and isinstance(fallback.value, str)
            and bool(fallback.value.strip())
        ) or (
            isinstance(fallback, ast.JoinedStr)
            and any(
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and bool(value.value.strip())
                for value in fallback.values
            )
        )
        if not has_text:
            violations.append(
                f"combined process output call at line {node.lineno} "
                "must supply a non-empty fallback"
            )
    return violations


def test_combined_process_output_calls_have_nonempty_fallbacks() -> None:
    assert not _combined_output_fallback_violations(TOOLS_PATH.read_text(encoding="utf-8"))


def test_empty_stream_fallback_detector_canary() -> None:
    source = 'def probe():\n    return _combined_process_output(stderr, stdout, "")\n'

    assert _combined_output_fallback_violations(source) == [
        "combined process output call at line 2 must supply a non-empty fallback"
    ]
