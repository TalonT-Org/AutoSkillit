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


def test_triage_uses_capability_field():
    """server/_misc.py triage gate must read triage_capable, not compare backend name."""
    import inspect

    from autoskillit.server import _misc

    source = inspect.getsource(_misc._apply_triage_gate)
    tree = ast.parse(source)
    has_triage_capable = any(
        isinstance(node, ast.Attribute) and node.attr == "triage_capable"
        for node in ast.walk(tree)
    )
    assert has_triage_capable, "_apply_triage_gate must read .triage_capable capability field"
    has_string_compare = any(
        isinstance(node, ast.Compare)
        and any(
            isinstance(c, ast.Constant) and c.value in {"claude-code", "codex"}
            for c in [node.left, *node.comparators]
        )
        for node in ast.walk(tree)
    )
    assert not has_string_compare, "_apply_triage_gate must not compare backend name strings"


def test_skills_subdir_uses_capability_field():
    """session_skills.py must read backend.capabilities.skills_subdir, not module constants."""
    import inspect
    import textwrap

    from autoskillit.workspace import session_skills

    source = inspect.getsource(session_skills.DefaultSessionSkillManager.init_session)
    tree = ast.parse(textwrap.dedent(source))
    has_skills_subdir = any(
        isinstance(node, ast.Attribute) and node.attr == "skills_subdir" for node in ast.walk(tree)
    )
    assert has_skills_subdir, "init_session must read .skills_subdir capability field"


def test_applicable_guards_uses_capability_field():
    """skill_load_guard must check AUTOSKILLIT_APPLICABLE_GUARDS env var, not backend name."""
    from autoskillit.core import paths

    guard_path = paths.pkg_root() / "hooks" / "guards" / "skill_load_guard.py"
    source = guard_path.read_text()
    assert "AUTOSKILLIT_APPLICABLE_GUARDS" in source, (
        "skill_load_guard must check AUTOSKILLIT_APPLICABLE_GUARDS env var"
    )
    assert 'casefold() == "codex"' not in source, (
        "skill_load_guard must not compare backend name directly"
    )


def test_all_backends_have_name_constant():
    """Every BACKEND_REGISTRY key must have a corresponding AGENT_BACKEND_* constant."""
    import inspect

    from autoskillit.core.types import _type_constants_env
    from autoskillit.execution.backends import BACKEND_REGISTRY

    source = inspect.getsource(_type_constants_env)
    tree = ast.parse(source)
    assignment_targets = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }

    for key in BACKEND_REGISTRY:
        expected_name = f"AGENT_BACKEND_{key.upper().replace('-', '_')}"
        assert expected_name in assignment_targets, (
            f"BACKEND_REGISTRY key {key!r} has no matching constant in "
            f"_type_constants_env.py — add {expected_name} = {key!r}"
        )


def test_backend_doctor_coverage():
    """_doctor_runtime must use capability-field dispatch, not backend-name strings."""
    import inspect

    from autoskillit.cli.doctor import _doctor_runtime

    source = inspect.getsource(_doctor_runtime)
    tree = ast.parse(source)
    has_capability_field = any(
        isinstance(node, ast.Attribute) and node.attr in {"version_check_command", "process_name"}
        for func_node in ast.walk(tree)
        if isinstance(func_node, ast.FunctionDef)
        and any(arg.arg == "backend" for arg in func_node.args.args + func_node.args.kwonlyargs)
        for node in ast.walk(func_node)
    )
    assert has_capability_field, (
        "Expected capability-driven dispatch (version_check_command/process_name) "
        "in _doctor_runtime; backend-name string comparisons were replaced by P4."
    )


def test_all_backends_have_init_hook():
    """cli/_init_helpers.py must import from the execution backends layer."""
    import inspect

    from autoskillit.cli import _init_helpers

    source = inspect.getsource(_init_helpers)
    tree = ast.parse(source)
    has_execution_import = any(
        isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("autoskillit.execution")
        for node in ast.walk(tree)
    )
    assert has_execution_import, (
        "cli/_init_helpers.py does not import from 'autoskillit.execution'. "
        "After P4-A2, _register_all must dispatch via the execution layer — "
        "ensure an import from 'autoskillit.execution.*' is present "
        "in cli/_init_helpers.py."
    )


def test_known_backend_names_matches_registry():
    """KNOWN_BACKEND_NAMES (IL-0) must equal BACKEND_REGISTRY keys (IL-1)."""
    from autoskillit.core import KNOWN_BACKEND_NAMES
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert set(BACKEND_REGISTRY.keys()) == KNOWN_BACKEND_NAMES, (
        "When a new backend is added to BACKEND_REGISTRY, KNOWN_BACKEND_NAMES "
        "in core/types/_type_constants_env.py must also be updated."
    )
