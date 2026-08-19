"""Shared test constants and helpers for tests/workspace/."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from autoskillit.core import (
    BackendCapabilities,
    BackendConventions,
    ClaudeDirectoryConventions,
    HookTrustPolicy,
    PreLaunchReadiness,
    RepositoryProfileId,
    SkillExecutionRole,
    ValidatedAddDir,
    pkg_root,
)

_CODEX_CAPABILITIES = BackendCapabilities(
    channel_b_capable=False,
    pty_required=False,
    session_resume_capable=True,
    skill_injection_capable=True,
    supports_thinking_blocks=False,
    supports_claude_format_stdout=False,
    exit_code_is_terminal=True,
    mcp_config_capable=True,
    food_truck_capable=True,
    completion_record_types=frozenset({"turn.completed", "turn.failed", "error"}),
    session_record_types=frozenset({"item.completed"}),
    required_session_files=frozenset({"config.toml"}),
    session_dir_symlinks=frozenset({"sessions", "archived_sessions"}),
    skills_subdir="skills",
    session_dir_persistent=True,
    supports_model_invocation_gating=False,
    hook_trust_policy=HookTrustPolicy.REVIEW_EACH_SESSION,
)


class _BodyFailure(Exception):
    pass


class _DeletionFailure(Exception):
    pass


class _ReleaseFailure(Exception):
    pass


def _make_codex_backend() -> MagicMock:
    from autoskillit.execution.backends import CodexBackend

    b = MagicMock()
    b.name = "codex"
    b.capabilities = _CODEX_CAPABILITIES
    b.conventions.skills_subdir = ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR
    b.ensure_pre_launch.return_value = PreLaunchReadiness((), {})
    b.setup_session_dir.return_value = None
    b.validate_session_layout.return_value = []
    b.adapt_skill_semantics.side_effect = CodexBackend().adapt_skill_semantics
    b.exploration_dispatch_renderer = CodexBackend().exploration_dispatch_renderer
    return b


def _stub_backend(
    name: str,
    *,
    session_dir_persistent: bool = True,
    persistent_session_root_subdir: Path | None,
) -> MagicMock:
    """Minimal stub with .name/.capabilities/.conventions built from real dataclasses."""
    backend = MagicMock()
    backend.name = name
    backend.capabilities = BackendCapabilities(session_dir_persistent=session_dir_persistent)
    backend.conventions = BackendConventions(
        persistent_session_root_subdir=persistent_session_root_subdir
    )
    return backend


def _catalog_context(
    manager,
    *,
    backend=None,
    names: frozenset[str] | None = None,
    role: SkillExecutionRole = SkillExecutionRole.SESSION,
):
    from autoskillit.workspace import DefaultSkillResolver, EffectiveSkillCatalog

    project_root = manager._root
    catalog = DefaultSkillResolver().list_effective(
        project_root,
        role,
    )
    if names is not None:
        catalog = EffectiveSkillCatalog(
            skills=tuple(member for member in catalog.skills if member.name in names),
            execution_role=role,
        )
    else:
        catalog = EffectiveSkillCatalog(
            skills=tuple(member for member in catalog.skills if not member.exploration_vectors),
            execution_role=role,
        )
    resolved_exploration_profile = (
        RepositoryProfileId.AUTOSKILLIT
        if any(member.exploration_vectors for member in catalog.skills)
        else None
    )
    context = manager._provider.catalog_projection_context(
        catalog,
        project_root,
        backend=backend,
        durable_scripts_root=pkg_root(),
        resolved_exploration_profile=resolved_exploration_profile,
    )
    return catalog, context


def _materialize(
    manager,
    session_id: str,
    *,
    backend=None,
    names: frozenset[str] | None = None,
) -> ValidatedAddDir:
    catalog, context = _catalog_context(manager, backend=backend, names=names)
    return manager.init_session(session_id, catalog, context)


def _managed(
    manager,
    session_id: str,
    *,
    backend,
    names: frozenset[str] | None = None,
    role: SkillExecutionRole = SkillExecutionRole.SESSION,
):
    from autoskillit.workspace import compile_session_skill_catalog

    catalog, context = _catalog_context(manager, backend=backend, names=names, role=role)
    compilation = compile_session_skill_catalog(catalog, backend)
    return manager.managed_session(session_id, compilation, context)
