"""Guard backend-semantic admission callers against dropping refusal evidence."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "autoskillit"
_ADAPT = "adapt_skill_semantics"
_COMPILE = "compile_session_skill_catalog"

# Exact production inventory. The counter preserves multiple admission calls in
# one function, while avoiding a brittle line-number ratchet.
_EXPECTED_CALLERS = Counter(
    {
        (_COMPILE, "cli/session/_session_cook.py", "cook"): 1,
        (_COMPILE, "cli/session/_session_order.py", "order"): 1,
        (_COMPILE, "workspace/session_skills.py", "_compile_reachable_profile_skill_catalog"): 1,
        (_COMPILE, "workspace/session_skills.py", "materialize_profile_skills"): 1,
        (_COMPILE, "workspace/session_skills.py", "_materialize_session"): 3,
        (
            _COMPILE,
            "server/tools/_serve_helpers.py",
            "_project_orchestrator_sous_chef",
        ): 1,
        (_COMPILE, "cli/fleet/_fleet_run.py", "_execute_fleet_run"): 1,
        (_COMPILE, "cli/fleet/_fleet_session.py", "_launch_fleet_session"): 1,
        (_ADAPT, "cli/doctor/_doctor_config.py", "_check_standing_backend_pins_feasibility"): 1,
        (_ADAPT, "server/tools/_preflight.py", "check_skill_semantic_feasibility"): 1,
        (_ADAPT, "workspace/_projected_artifact/authority.py", "_plan"): 1,
        (
            _ADAPT,
            "workspace/_projected_artifact/materialization.py",
            "project_agent_skill_document",
        ): 1,
        (_ADAPT, "workspace/session_skills.py", "compile_session_skill_catalog"): 1,
        (_ADAPT, "workspace/session_skills.py", "_materialize_session"): 1,
        (_ADAPT, "workspace/skill_projection.py", "build_skill_projection_binding"): 1,
    }
)

# These helpers carry the structured compilation to the generated-home path,
# where write_skill_unavailability_metadata() publishes its refusal metadata.
_COMPILATION_CONSUMERS = frozenset(
    {
        "CompiledSessionSkillCatalog",
        "_launch_cook_session",
        "append_skill_unavailability",
        "managed_session",
        "render_skill_unavailability",
        "write_skill_unavailability_metadata",
    }
)

# Each exemption names the admission kind, module, enclosing function, and why
# that function cannot honestly surface a refusal. Validation below rejects
# rationale-free, absent, and no-longer-needed exemptions.
_REFUSAL_EXEMPTIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        _ADAPT,
        "workspace/_projected_artifact/materialization.py",
        "project_agent_skill_document",
        (
            "Document rendering receives an already-admitted skill and has no structured "
            "refusal return; validate_for() remains its enforcement-only fail-closed path."
        ),
    ),
)


def _validated_exemption_keys(
    exemptions: tuple[tuple[str, str, str, str], ...],
    inventory: Counter[tuple[str, str, str]],
    unconsumed: list[tuple[tuple[str, str, str], str]],
) -> frozenset[tuple[str, str, str]]:
    blank_rationales = [entry[:3] for entry in exemptions if not entry[3].strip()]
    assert not blank_rationales, (
        f"Refusal exemptions require non-blank rationales: {blank_rationales}"
    )
    keys = [entry[:3] for entry in exemptions]
    assert len(keys) == len(set(keys)), f"Duplicate refusal exemptions: {keys}"
    unconsumed_keys = {key for key, _message in unconsumed}
    stale = [key for key in keys if key not in inventory or key not in unconsumed_keys]
    assert not stale, (
        f"Stale refusal exemptions have no caller or now consume refusal evidence: {stale}"
    )
    return frozenset(keys)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _contains(root: ast.AST, target: ast.AST) -> bool:
    return any(node is target for node in ast.walk(root))


def _walk_function_scope(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterator[ast.AST]:
    """Walk one function body without borrowing evidence from nested callables."""
    pending: list[ast.AST] = list(reversed(function.body))
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        pending.extend(reversed(list(ast.iter_child_nodes(node))))


def _bound_name(function: ast.AST, call: ast.Call) -> str | None:
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    for node in _walk_function_scope(function):
        value: ast.AST | None = None
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            value = node.value
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value = node.value
            targets = [node.target]
        elif isinstance(node, ast.NamedExpr):
            value = node.value
            targets = [node.target]
        if value is not None and _contains(value, call):
            for target in targets:
                if isinstance(target, ast.Name):
                    return target.id
    return None


def _name_reaches_call(variable: str, call: ast.Call) -> bool:
    return any(isinstance(node, ast.Name) and node.id == variable for node in ast.walk(call))


def _consumes_adaptation(function: ast.AST, call: ast.Call) -> bool:
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    variable = _bound_name(function, call)
    if variable is None:
        return False
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "validate_refusal_for"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == variable
        for node in _walk_function_scope(function)
    )


def _consumes_compilation(function: ast.AST, call: ast.Call) -> bool:
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    variable = _bound_name(function, call)
    if variable is None:
        return any(
            isinstance(node, ast.Call)
            and _call_name(node) in _COMPILATION_CONSUMERS
            and _contains(node, call)
            for node in _walk_function_scope(function)
        )
    if any(
        isinstance(node, ast.Attribute)
        and node.attr == "unavailability_payload"
        and isinstance(node.value, ast.Name)
        and node.value.id == variable
        and any(
            isinstance(parent, ast.Call) and _contains(parent, node)
            for parent in _walk_function_scope(function)
        )
        for node in _walk_function_scope(function)
    ):
        return True
    for node in _walk_function_scope(function):
        if not (
            isinstance(node, ast.For)
            and isinstance(node.iter, ast.Attribute)
            and node.iter.attr == "unavailable"
            and isinstance(node.iter.value, ast.Name)
            and node.iter.value.id == variable
            and isinstance(node.target, ast.Name)
        ):
            continue
        if any(
            isinstance(candidate, ast.Call)
            and isinstance(candidate.func, ast.Attribute)
            and candidate.func.attr in {"debug", "info", "warning", "error", "exception"}
            and _name_reaches_call(node.target.id, candidate)
            for candidate in ast.walk(node)
        ):
            return True
    return any(
        isinstance(node, ast.Call)
        and _call_name(node) in _COMPILATION_CONSUMERS
        and _name_reaches_call(variable, node)
        for node in _walk_function_scope(function)
    )


def _scan_source(
    source: str,
    relative_path: str,
) -> tuple[
    Counter[tuple[str, str, str]],
    list[tuple[tuple[str, str, str], str]],
]:
    tree = ast.parse(source, filename=relative_path)
    parents = {
        child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
    }
    inventory: Counter[tuple[str, str, str]] = Counter()
    unconsumed: list[tuple[tuple[str, str, str], str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in {_ADAPT, _COMPILE}:
            continue
        kind = _call_name(node)
        assert kind is not None
        parent = parents.get(node)
        while parent is not None and not isinstance(
            parent, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            parent = parents.get(parent)
        if parent is None:
            key = (kind, relative_path, "<module>")
            inventory[key] += 1
            unconsumed.append(
                (key, f"{relative_path}:{node.lineno}: {kind}() is outside a function")
            )
            continue
        key = (kind, relative_path, parent.name)
        inventory[key] += 1
        consumed = (
            _consumes_adaptation(parent, node)
            if kind == _ADAPT
            else _consumes_compilation(parent, node)
        )
        if not consumed:
            unconsumed.append(
                (
                    key,
                    f"{relative_path}:{node.lineno} in {parent.name}() calls {kind}() "
                    "without consuming refusal evidence",
                )
            )
    return inventory, unconsumed


def _scan_production() -> tuple[
    Counter[tuple[str, str, str]],
    list[tuple[tuple[str, str, str], str]],
]:
    inventory: Counter[tuple[str, str, str]] = Counter()
    unconsumed: list[tuple[tuple[str, str, str], str]] = []
    for path in _SRC_ROOT.rglob("*.py"):
        relative_path = str(path.relative_to(_SRC_ROOT))
        found, source_violations = _scan_source(
            path.read_text(encoding="utf-8"),
            relative_path,
        )
        inventory.update(found)
        unconsumed.extend(source_violations)
    return inventory, unconsumed


def test_backend_semantic_admission_callers_consume_refusals() -> None:
    inventory, unconsumed = _scan_production()

    assert inventory == _EXPECTED_CALLERS, (
        "Backend-semantic admission call-site inventory drifted; reconcile the new caller "
        f"with refusal consumption. Expected {sorted(_EXPECTED_CALLERS.items())}, "
        f"found {sorted(inventory.items())}"
    )
    exemption_keys = _validated_exemption_keys(
        _REFUSAL_EXEMPTIONS,
        inventory,
        unconsumed,
    )
    violations = [message for key, message in unconsumed if key not in exemption_keys]
    assert not violations, "Unconsumed backend-semantic refusals:\n" + "\n".join(violations)


def test_refusal_exemption_requires_a_rationale() -> None:
    key = (_ADAPT, "scratch/enforcement.py", "render")

    with pytest.raises(AssertionError, match="non-blank rationales"):
        _validated_exemption_keys(
            ((*key, "  "),),
            Counter({key: 1}),
            [(key, "unconsumed")],
        )


@pytest.mark.parametrize("caller_state", ["absent", "consuming"])
def test_stale_refusal_exemption_is_rejected(caller_state: str) -> None:
    key = (_ADAPT, "scratch/enforcement.py", "render")
    inventory = Counter({key: 1}) if caller_state == "consuming" else Counter()

    with pytest.raises(AssertionError, match="Stale refusal exemptions"):
        _validated_exemption_keys(
            ((*key, "enforcement-only surface"),),
            inventory,
            [],
        )


def test_scratch_caller_that_projects_only_catalog_is_rejected() -> None:
    source = """
def unsafe_catalog_projection(catalog, backend):
    compilation = compile_session_skill_catalog(catalog, backend)
    return compilation.catalog
"""

    inventory, unconsumed = _scan_source(source, "scratch/unsafe_projection.py")

    assert inventory == Counter(
        {(_COMPILE, "scratch/unsafe_projection.py", "unsafe_catalog_projection"): 1}
    )
    assert unconsumed == [
        (
            (_COMPILE, "scratch/unsafe_projection.py", "unsafe_catalog_projection"),
            "scratch/unsafe_projection.py:3 in unsafe_catalog_projection() calls "
            "compile_session_skill_catalog() without consuming refusal evidence",
        )
    ]
