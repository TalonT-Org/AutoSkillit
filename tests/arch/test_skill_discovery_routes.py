"""Keep declared skill-discovery routes live, explicit, and time-bounded."""

from __future__ import annotations

import ast
from datetime import date

import pytest

from autoskillit.core import SkillDiscoveryRouteDef
from tests.arch._deferred_debt import (
    TrackedDeferral,
    assert_deferrals_have_regression_tests,
    assert_entries_still_apply,
    assert_not_stale,
    assert_rationale_present,
)
from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


ACCEPTED_DEPRECATED_SKILL_DISCOVERY_ROUTES: dict[str, TrackedDeferral] = {
    "codex_managed_home_skills_alias": TrackedDeferral(
        issue=4717,
        rationale=(
            "Upstream Codex TUI exposes no config key, CLI flag, env var, or reachable RPC for "
            "extra skill roots; the non-deprecated roots require a per-session cwd or HOME, which "
            "breaks cook's checkout-as-cwd contract. Prelaunch attestation is the runtime guard."
        ),
        added_date=date(2026, 9, 15),
        regression_test=(
            "tests/integration/test_codex_skill_discovery_canary.py::"
            "test_installed_codex_discovers_the_session_catalog_through_the_declared_interactive_route"
        ),
    ),
    "codex_projected_home_skills": TrackedDeferral(
        issue=4717,
        rationale=(
            "Same upstream constraint as the managed alias; projected homes attest "
            "the direct root."
        ),
        added_date=date(2026, 9, 15),
        regression_test=(
            "tests/execution/backends/test_codex_config_validation.py::"
            "test_projected_interactive_validator_accepts_canonical_home_without_managed_topology"
        ),
    ),
}


def _module_constants(tree: ast.Module) -> set[str]:
    constants: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            constants.update(
                target.id for target in statement.targets if isinstance(target, ast.Name)
            )
    return constants


def _is_static_route_value(value: ast.expr, module_constants: set[str]) -> bool:
    if isinstance(value, ast.Constant | ast.JoinedStr):
        return True
    if isinstance(value, ast.Name):
        return value.id in module_constants
    if not isinstance(value, ast.Attribute) or not isinstance(value.value, ast.Name):
        return False
    return (
        value.value.id
        in {
            "SkillDiscoveryMechanism",
            "UpstreamSupportStatus",
        }
        | module_constants
    )


def _route_constructions() -> set[tuple[str, int, str]]:
    """Collect only static module-level ``SkillDiscoveryRouteDef`` definitions."""
    constructions: set[tuple[str, int, str]] = set()
    for source_path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        module_constants = _module_constants(tree)
        relative_path = source_path.relative_to(SRC_ROOT).as_posix()
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "SkillDiscoveryRouteDef"
            ):
                continue
            assignment = next(
                (
                    statement
                    for statement in tree.body
                    if isinstance(statement, ast.Assign) and statement.value is node
                ),
                None,
            )
            assert assignment is not None, (
                f"{relative_path}:{node.lineno}: SkillDiscoveryRouteDef must be a "
                "module-level assignment"
            )
            assert all(
                _is_static_route_value(keyword.value, module_constants)
                for keyword in node.keywords
            ), (
                f"{relative_path}:{node.lineno}: SkillDiscoveryRouteDef keyword values "
                "must be static"
            )
            name_keyword = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "name"), None
            )
            assert isinstance(name_keyword, ast.Constant) and isinstance(
                name_keyword.value, str
            ), (
                f"{relative_path}:{node.lineno}: SkillDiscoveryRouteDef name must be "
                "a string literal"
            )
            constructions.add((relative_path, node.lineno, name_keyword.value))
    return constructions


def _live_routes() -> dict[str, SkillDiscoveryRouteDef]:
    from autoskillit.execution.backends import BACKEND_REGISTRY, _codex_discovery, claude

    routes: list[SkillDiscoveryRouteDef] = []
    for backend_type in BACKEND_REGISTRY.values():
        route = backend_type().conventions.managed_skill_discovery
        assert isinstance(route, SkillDiscoveryRouteDef)
        routes.append(route)
    routes.extend(
        value
        for module in (_codex_discovery, claude)
        for value in vars(module).values()
        if isinstance(value, SkillDiscoveryRouteDef)
    )

    live: dict[str, SkillDiscoveryRouteDef] = {}
    for route in routes:
        existing = live.setdefault(route.name, route)
        assert existing is route, f"duplicate route definition for {route.name}"
    return live


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_forbidden_path_context(node: ast.Constant, parent: ast.AST) -> bool:
    if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div):
        return True
    if isinstance(parent, ast.Call):
        if _call_name(parent) == "SkillDiscoveryRouteDef":
            return False
        return _call_name(parent) in {
            "Path",
            "PurePosixPath",
            "joinpath",
            "symlink",
            "symlink_to",
            "readlink",
            "lexists",
        } or any(keyword.value is node for keyword in parent.keywords)
    if isinstance(parent, ast.Compare):
        operands = (parent.left, *parent.comparators)
        return any(
            operand is not node
            and isinstance(operand, ast.Call)
            and _call_name(operand) == "readlink"
            for operand in operands
        )
    return False


def _bare_discovery_path_literals() -> list[str]:
    forbidden = {
        path
        for route in _live_routes().values()
        for path in (route.catalog_relpath, route.discovery_root_relpath)
        if path is not None
    }
    forbidden.update(
        route.alias_target for route in _live_routes().values() if route.entry_point_is_alias
    )
    launch_modules = (
        "execution/backends/codex.py",
        "execution/backends/_codex/session_commands.py",
        "execution/backends/_codex/headless_commands.py",
        "execution/backends/_codex/app_server.py",
        "execution/backends/_codex_discovery.py",
        "workspace/session_skills/_materialization.py",
        "workspace/session_skills/_manager.py",
    )
    findings: list[str] = []
    for relative_path in launch_modules:
        source_path = SRC_ROOT / relative_path
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or node.value not in forbidden:
                continue
            if _is_forbidden_path_context(node, parents[node]):
                findings.append(f"{relative_path}:{node.lineno}")
    return sorted(findings)


def test_every_route_construction_is_a_live_module_constant() -> None:
    constructions = _route_constructions()
    names = [name for _path, _lineno, name in constructions]
    assert len(names) == len(set(names)), f"duplicate route names: {sorted(names)}"
    assert set(names) == set(_live_routes())


def test_deprecated_routes_are_accepted_deferrals() -> None:
    from autoskillit.core import UpstreamSupportStatus

    live_routes = _live_routes()
    deprecated = {
        name
        for name, route in live_routes.items()
        if route.upstream_status is UpstreamSupportStatus.DEPRECATED
    }
    assert_entries_still_apply(
        ACCEPTED_DEPRECATED_SKILL_DISCOVERY_ROUTES,
        registry_name="accepted deprecated skill-discovery routes",
        live_keys=deprecated,
    )
    assert deprecated <= ACCEPTED_DEPRECATED_SKILL_DISCOVERY_ROUTES.keys()
    assert_not_stale(
        ACCEPTED_DEPRECATED_SKILL_DISCOVERY_ROUTES,
        registry_name="accepted deprecated skill-discovery routes",
    )
    assert_rationale_present(
        ACCEPTED_DEPRECATED_SKILL_DISCOVERY_ROUTES,
        registry_name="accepted deprecated skill-discovery routes",
    )
    assert_deferrals_have_regression_tests(
        ACCEPTED_DEPRECATED_SKILL_DISCOVERY_ROUTES,
        registry_name="accepted deprecated skill-discovery routes",
    )
    for name in deprecated:
        assert (
            ACCEPTED_DEPRECATED_SKILL_DISCOVERY_ROUTES[name].issue
            == live_routes[name].tracking_issue
        )


def test_supported_routes_carry_no_tracking_issue() -> None:
    from autoskillit.core import UpstreamSupportStatus

    assert all(
        route.tracking_issue is None
        for route in _live_routes().values()
        if route.upstream_status is UpstreamSupportStatus.SUPPORTED
    )


def test_no_bare_discovery_path_literals_in_launch_modules() -> None:
    assert not _bare_discovery_path_literals()
