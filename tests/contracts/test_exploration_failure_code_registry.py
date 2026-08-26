"""Contract: every exploration failure "code" literal must be enum-registered.

AST-scans tools_exploration.py for every dict-literal `"code": <value>` pair
and every `_failure(...)` call argument. A bare string literal that is not a
registered ExplorationFailureCode value fails this test — the opaque
`except Exception: return _failure("exploration_provisioning_failed")`
catch-all this contract replaces (#4684) cannot silently reappear as a
hand-typed literal that bypasses the registry.

References that already flow through the enum (``ExplorationFailureCode.X``
or a module constant assigned from it) resolve as AST Attribute/Name nodes,
not Constant nodes, and are skipped — they are already typed by
construction: a drifted enum member would raise AttributeError at import
time, not silently produce an unregistered string.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core import (
    BROKER_AUTHORITY_STATUSES,
    EXPLORATION_FAILURE_CODES,
    ExplorationFailureCode,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "autoskillit" / "server" / "tools" / "tools_exploration.py"
_SRC_STATUS = _REPO_ROOT / "src" / "autoskillit" / "server" / "tools" / "tools_status.py"
_SRC_ROOT = _REPO_ROOT / "src" / "autoskillit"
_TESTS_ROOT = _REPO_ROOT / "tests"

_FORWARD_SCAN_TARGETS: tuple[tuple[Path, frozenset[str]], ...] = (
    (_SRC, EXPLORATION_FAILURE_CODES),
    (_SRC_STATUS, BROKER_AUTHORITY_STATUSES),
)


def _code_dict_literal_values(tree: ast.Module) -> list[ast.expr]:
    """Return the value expression of every dict-literal `"code": <value>` pair."""
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "code" and value is not None:
                found.append(value)
    return found


def _failure_call_args(tree: ast.Module) -> list[ast.expr]:
    """Return the sole positional argument of every `_failure(...)` call."""
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_failure"
            and len(node.args) == 1
        ):
            found.append(node.args[0])
    return found


def _literal_code_value(expr: ast.expr) -> str | None:
    """Return the bare string literal, or None if the expression isn't one."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def _subscript_key_assignment_values(tree: ast.Module, key: str) -> list[ast.expr]:
    """Return the RHS expression of every `<name>[<key>] = <rhs>` assignment.

    A dict-literal or ``_failure(...)`` call is not the only shape a code/status
    literal can escape the registry through — ``status["broker_authority"] = ...``
    is a Subscript-assignment the two matchers above cannot see at all.
    """
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.targets[0].slice, ast.Constant)
            and node.targets[0].slice.value == key
        ):
            found.append(node.value)
    return found


def test_every_code_literal_is_a_registered_exploration_failure_code() -> None:
    violations: list[str] = []
    for path, registry in _FORWARD_SCAN_TARGETS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        candidates = [
            *_code_dict_literal_values(tree),
            *_failure_call_args(tree),
            *_subscript_key_assignment_values(tree, "broker_authority"),
        ]
        violations.extend(
            f"{path.name}:{expr.lineno}: {literal!r} is not a registered code/status"
            for expr in candidates
            if (literal := _literal_code_value(expr)) is not None
            if literal not in registry
        )
    assert not violations, (
        "Unregistered exploration failure code or broker authority literal(s) — "
        f"register it in ExplorationFailureCode or BrokerAuthorityStatus first: {violations}"
    )


def test_broker_authority_assignment_is_scanned() -> None:
    """Non-vacuousness guard for the subscript matcher above: prove it actually
    finds tools_status.py's status["broker_authority"] assignment sites rather
    than silently matching nothing."""
    tree = ast.parse(_SRC_STATUS.read_text(encoding="utf-8"), filename=str(_SRC_STATUS))
    candidates = _subscript_key_assignment_values(tree, "broker_authority")
    assert len(candidates) >= 4, (
        f'expected at least 4 status["broker_authority"] assignment sites in '
        f"tools_status.py (one per BrokerAuthorityStatus branch: "
        f"SESSION_TYPE_INELIGIBLE, STORE_UNAVAILABLE, AVAILABLE, NO_SESSION_BOUND), "
        f"found {len(candidates)}"
    )


def _codes_referenced(tree: ast.AST, member_value_by_name: dict[str, str]) -> set[str]:
    """Every registered code value referenced in *tree*, by bare string literal
    or by an ``ExplorationFailureCode.<MEMBER>`` attribute access — the latter
    is exactly the reference shape the source-literal contract above
    deliberately skips (it resolves as an AST ``Attribute``, not a ``Constant``)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in EXPLORATION_FAILURE_CODES:
                found.add(node.value)
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ExplorationFailureCode"
            and node.attr in member_value_by_name
        ):
            found.add(member_value_by_name[node.attr])
    return found


def test_every_exploration_failure_code_has_an_emitter() -> None:
    """Reverse direction the source-literal contract above lacks: every
    registered code must be produced by at least one AST-discoverable site in
    src/, counting both a bare literal and an ``ExplorationFailureCode.X``
    attribute reference. Models the bidirectional shape of
    ``test_fleet_error_code_enum_has_expected_codes``
    (tests/fleet/test_error_envelope.py)."""
    member_value_by_name = {member.name: member.value for member in ExplorationFailureCode}
    emitted: set[str] = set()
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        emitted |= _codes_referenced(tree, member_value_by_name)
    missing = sorted(EXPLORATION_FAILURE_CODES - emitted)
    assert not missing, f"ExplorationFailureCode member(s) never emitted in src/: {missing}"


def test_every_exploration_failure_code_has_a_test_reference() -> None:
    """Every registered code must be referenced somewhere under tests/.

    Catches drift the forward-direction scan cannot: an enum member with an
    emitter but zero test coverage — exactly the ``INVALID_SOURCE_IDENTITY``
    gap #4756 found, live and unreferenced by any test despite being raised
    at ``pipeline/exploration_context/_store.py`` and mapped at
    ``tools_exploration.py``. Scoped to the whole file rather than to
    individual ``assert`` statements: several tests in
    ``test_enable_exploration_failure_codes.py`` share one
    ``_bind_raising(..., expected_code=...)`` helper that performs the actual
    assertion, so the code value appears as a call argument at the parametrized
    call site, not textually inside an ``ast.Assert`` node."""
    member_value_by_name = {member.name: member.value for member in ExplorationFailureCode}
    referenced: set[str] = set()
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        referenced |= _codes_referenced(tree, member_value_by_name)
    missing = sorted(EXPLORATION_FAILURE_CODES - referenced)
    assert not missing, (
        f"ExplorationFailureCode member(s) never referenced anywhere under tests/: {missing}"
    )
