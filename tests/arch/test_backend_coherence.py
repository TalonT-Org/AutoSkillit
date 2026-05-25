"""Architectural tests for backend coherence enforcement."""

from __future__ import annotations

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_capability_queries_match_command_backend(minimal_ctx):
    """_resolve_pty_mode and _resolve_session_log_dir respect the backend on ctx."""
    from autoskillit.execution.backends import BACKEND_REGISTRY
    from autoskillit.execution.headless import _resolve_pty_mode, _resolve_session_log_dir

    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        minimal_ctx.backend = backend
        assert _resolve_pty_mode(backend) == backend.capabilities.pty_required, (
            f"PTY mode mismatch for {name}"
        )
        log_dir = _resolve_session_log_dir("/tmp/fake", backend)
        if not backend.capabilities.channel_b_capable:
            assert log_dir is None, f"Expected None log_dir for {name}"
        else:
            assert log_dir is not None, f"Expected log_dir path for {name}"


def test_all_experimental_features_with_infrastructure_swap_have_alignment_guard():
    """Features that swap core infrastructure must declare requires_backend_alignment."""
    import inspect

    from autoskillit.core.types._type_constants_features import FEATURE_REGISTRY
    from autoskillit.server import _factory

    source = inspect.getsource(_factory.make_context)
    tree = ast.parse(source)

    backend_assignments: list[ast.Assign] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "backend":
                    backend_assignments.append(node)

    swapped_features: set[str] = set()
    for if_node in ast.walk(tree):
        if not isinstance(if_node, ast.If):
            continue
        for assign in backend_assignments:
            if any(_node_contains(child, assign) for child in if_node.body):
                feat = _extract_feature_name(if_node.test)
                if feat:
                    swapped_features.add(feat)

    for feat_name in swapped_features:
        if feat_name in FEATURE_REGISTRY:
            defn = FEATURE_REGISTRY[feat_name]
            assert defn.requires_backend_alignment, (
                f"Feature {feat_name!r} swaps ctx.backend in make_context() "
                f"but lacks requires_backend_alignment=True in FEATURE_REGISTRY"
            )


def _node_contains(parent: ast.AST, target: ast.AST) -> bool:
    if parent is target:
        return True
    for child in ast.walk(parent):
        if child is target:
            return True
    return False


def _extract_feature_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
        for kw in node.keywords:
            if (
                kw.arg == "name"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ):
                return kw.value.value
    return None


def test_codex_backend_requires_backend_alignment():
    """codex_backend must have requires_backend_alignment=True."""
    from autoskillit.core.types._type_constants_features import FEATURE_REGISTRY

    defn = FEATURE_REGISTRY["codex_backend"]
    assert defn.requires_backend_alignment is True


@pytest.mark.parametrize(
    "feature_name",
    [name for name in ("fleet", "planner", "providers")],
)
def test_non_infrastructure_features_do_not_require_alignment(feature_name):
    """Features that don't swap infrastructure should not set requires_backend_alignment."""
    from autoskillit.core.types._type_constants_features import FEATURE_REGISTRY

    defn = FEATURE_REGISTRY[feature_name]
    assert defn.requires_backend_alignment is False
