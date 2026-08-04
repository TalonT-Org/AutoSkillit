"""Typed launch-authority coverage at the composed-recipe execution boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    LaunchContractError,
    LaunchResolutionRequest,
    LaunchSurface,
    LaunchValueSource,
    LaunchValueSourceKind,
    SemanticLaunchPlan,
)
from autoskillit.execution import DefaultLaunchResolver

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


_GLOBAL_SOURCE = LaunchValueSource(
    LaunchValueSourceKind.GLOBAL,
    "agent_backend.backend",
)


def _launch_request(*authorities: BackendAuthority) -> LaunchResolutionRequest:
    return LaunchResolutionRequest(
        surface=LaunchSurface.HEADLESS_SKILL,
        authority_candidates=authorities,
        semantic_plan=SemanticLaunchPlan(
            surface=LaunchSurface.HEADLESS_SKILL,
            semantic_digest="composed-recipe",
            projection_digest="post-prune-projection",
        ),
        command="/autoskillit:dry-walkthrough",
        arguments=(),
        cwd="/work/repo",
        requested_model=None,
        requested_model_source=_GLOBAL_SOURCE,
        configured_model=None,
        configured_model_source=_GLOBAL_SOURCE,
        effort=None,
        effort_source=_GLOBAL_SOURCE,
        sandbox_mode="workspace-write",
        network_access=False,
        pty_required=False,
        inherited_fd_policy="none",
        branch_identity={},
        worktree_identity={},
        executable_identity={},
        plugin_identity={},
        projection_identity={},
        artifact_paths=(),
        quota_identity={},
    )


def test_missing_or_unknown_launch_authority_fails_closed() -> None:
    """Recipe metadata cannot replace a missing or invalid backend authority."""
    resolver = DefaultLaunchResolver()

    with pytest.raises(LaunchContractError, match="explicit backend authority"):
        resolver.prepare(_launch_request())

    unknown = BackendAuthority(
        backend="unknown-backend",
        kind=BackendAuthorityKind.STEP,
        tier=BackendAuthorityTier.STEP,
        key_path="recipe.steps.composition_child_admitted.backend",
    )
    with pytest.raises(LaunchContractError, match="unknown backend.*composition_child_admitted"):
        resolver.prepare(_launch_request(unknown))


@pytest.mark.anyio
async def test_recipe_step_authority_is_derived_injected_and_propagated(
    tool_ctx_kitchen_open,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_skill derives the winning config pin and injects its typed provenance."""
    from autoskillit.config._config_dataclasses import AgentBackendConfig
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    step_name = "composition_child_admitted"
    tool_ctx_kitchen_open.config.agent_backend = AgentBackendConfig(
        backend="codex",
        recipe_overrides={"composition-parent": {step_name: "claude-code"}},
        step_overrides={step_name: "codex"},
    )
    tool_ctx_kitchen_open.recipe_name = "composition-parent"
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda _binary: "/usr/bin/claude",
    )

    await run_skill("/test-skill", str(tmp_path), step_name=step_name)

    assert len(executor.calls) == 1
    authority = executor.calls[0].backend_authority
    assert authority is not None
    assert authority.backend == "claude-code"
    assert authority.kind is BackendAuthorityKind.RECIPE
    assert authority.tier is BackendAuthorityTier.RECIPE
    assert authority.key_path == (
        "agent_backend.recipe_overrides.composition-parent.composition_child_admitted"
    )
