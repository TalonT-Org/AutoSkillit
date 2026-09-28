"""Keep generated-home writes and verification at their intended boundaries."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from tests.contracts._ast_helpers import call_name, callers_by_function

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
_BACKEND_PROTOCOL = _SRC_ROOT / "core" / "types" / "protocols" / "_type_protocols_backend.py"
_EXPECTED_CALLERS = Counter(
    {
        (
            "ensure_pre_launch",
            "workspace/session_skills/_materialization.py",
            "_setup_generated_session",
        ): 1,
        (
            "ensure_pre_launch",
            "workspace/session_skills/_materialization.py",
            "_restore_session",
        ): 1,
        (
            "ensure_pre_launch",
            "cli/session/_session_launch.py",
            "prepare_interactive_launch",
        ): 1,
        ("ensure_pre_launch", "execution/backends/claude.py", "probe_launch_readiness"): 1,
        (
            "probe_launch_readiness",
            "cli/session/_session_launch.py",
            "prepare_interactive_launch",
        ): 1,
        (
            "configure_managed_session_dir",
            "workspace/session_skills/_materialization.py",
            "_configure_managed_session_route",
        ): 1,
        ("codex_prelaunch_transaction", "execution/backends/codex.py", "ensure_pre_launch"): 1,
        (
            "verify_managed_session_dir",
            "cli/session/_session_backend.py",
            "verify_launch_home",
        ): 1,
        (
            "verify_managed_session_dir",
            "server/_managed_join_attestation.py",
            "_verified_live_catalog",
        ): 1,
        (
            "read_managed_session_catalog",
            "server/_managed_join_attestation.py",
            "_verified_live_catalog",
        ): 1,
        (
            "projected_manifest_path",
            "server/_managed_join_attestation.py",
            "_write_managed_parent_binding",
        ): 1,
        (
            "sync_hooks_to_codex_config",
            "cli/_init_helpers.py",
            "_register_backend_integrations",
        ): 1,
    }
)


def _managed_route_methods() -> set[str]:
    tree = ast.parse(
        _BACKEND_PROTOCOL.read_text(encoding="utf-8"), filename=str(_BACKEND_PROTOCOL)
    )
    protocol = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ManagedRouteHomeBackend"
    )
    return {
        node.name
        for node in protocol.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _production_calls() -> tuple[Counter[tuple[str, str, str]], list[tuple[str, int, str]]]:
    inventory: Counter[tuple[str, str, str]] = Counter()
    dynamic_calls: list[tuple[str, int, str]] = []
    route_methods = _managed_route_methods()
    names = route_methods | {
        "ensure_pre_launch",
        "probe_launch_readiness",
        "project_managed_route",
        "codex_prelaunch_transaction",
        "sync_hooks_to_codex_config",
    }
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        relpath = path.relative_to(_SRC_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relpath)
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            if name == "getattr" and len(node.args) >= 2:
                method_arg = node.args[1]
                if (
                    isinstance(method_arg, ast.Constant)
                    and isinstance(method_arg.value, str)
                    and method_arg.value in route_methods
                ):
                    dynamic_calls.append((relpath, node.lineno, method_arg.value))
            if name not in names:
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                parent = parents.get(parent)
            if parent is not None:
                inventory[(name, relpath, parent.name)] += 1
    return inventory, dynamic_calls


def test_generated_home_writer_and_verifier_callers_are_inventoried() -> None:
    calls, dynamic_calls = _production_calls()
    assert not dynamic_calls, f"managed-route getattr calls bypass inventory: {dynamic_calls}"
    assert calls == _EXPECTED_CALLERS


def _keyword_call_sites(symbol: str) -> list[tuple[str, str, int, frozenset[str]]]:
    """Every call to ``symbol`` under ``_SRC_ROOT``, with the keyword arg names used."""
    sites: list[tuple[str, str, int, frozenset[str]]] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        relpath = path.relative_to(_SRC_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relpath)
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or call_name(node) != symbol:
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                parent = parents.get(parent)
            keywords = frozenset(kw.arg for kw in node.keywords if kw.arg is not None)
            enclosing = parent.name if parent is not None else "<module>"
            sites.append((relpath, enclosing, node.lineno, keywords))
    return sites


def test_ensure_pre_launch_session_dir_calls_pass_plugin_dir_explicitly() -> None:
    session_dir_sites = [
        site for site in _keyword_call_sites("ensure_pre_launch") if "session_dir" in site[3]
    ]
    assert session_dir_sites, "no ensure_pre_launch(session_dir=...) call sites found in src/"
    missing = [
        f"{relpath}:{lineno} ({func})"
        for relpath, func, lineno, keywords in session_dir_sites
        if "plugin_dir" not in keywords
    ]
    assert not missing, f"ensure_pre_launch(session_dir=...) without plugin_dir=: {missing}"


def test_configure_managed_session_dir_calls_pass_plugin_dir_explicitly() -> None:
    sites = _keyword_call_sites("configure_managed_session_dir")
    assert sites, "no configure_managed_session_dir(...) call sites found in src/"
    missing = [
        f"{relpath}:{lineno} ({func})"
        for relpath, func, lineno, keywords in sites
        if "plugin_dir" not in keywords
    ]
    assert not missing, f"configure_managed_session_dir(...) without plugin_dir=: {missing}"


_PLUGIN_SOURCE_RELPATH = "core/types/install/_type_plugin_source.py"


def test_session_hook_root_constructed_only_via_from_binding() -> None:
    plugin_source = _SRC_ROOT / "core" / "types" / "install" / "_type_plugin_source.py"
    tree = ast.parse(plugin_source.read_text(encoding="utf-8"), filename=str(plugin_source))
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SessionHookRoot"
    )
    own_constructions: Counter[str] = Counter()
    for method in class_node.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(method):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"SessionHookRoot", "cls"}
            ):
                own_constructions[method.name] += 1
    assert own_constructions, "SessionHookRoot has no direct-construction call site"
    assert set(own_constructions) == {"from_binding"}, dict(own_constructions)

    external_constructions = callers_by_function(_SRC_ROOT, symbol="SessionHookRoot")
    assert set(external_constructions) <= {
        (_PLUGIN_SOURCE_RELPATH, "from_binding"),
    }, dict(external_constructions)


def test_codex_exposes_managed_projection_through_protocol_method() -> None:
    codex_path = _SRC_ROOT / "execution" / "backends" / "codex.py"
    tree = ast.parse(codex_path.read_text(encoding="utf-8"), filename=str(codex_path))
    backend = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "CodexBackend"
    )
    assert any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "configure_managed_session_dir"
            for target in node.targets
        )
        and isinstance(node.value, ast.Name)
        and node.value.id == "project_managed_route"
        for node in backend.body
    )
