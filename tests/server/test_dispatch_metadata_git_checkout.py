"""Projection binding admission follows the invocation's semantic declarations."""

from __future__ import annotations

import pytest

from autoskillit.core import SkillExecutionRole
from autoskillit.server.tools._execution_helpers import (
    GitCheckoutRequiredError,
    build_fresh_projection_context,
    invocation_requires_git_checkout,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _resolve_invocation(tool_ctx, name: str):
    return tool_ctx.skill_resolver.resolve_invocation(
        name,
        tool_ctx.project_dir,
        SkillExecutionRole.SESSION,
        visibility=tool_ctx.config.skill_visibility_spec(),
        recipe_packs=tool_ctx.active_recipe_packs,
        recipe_features=tool_ctx.active_recipe_features,
    )


@pytest.mark.parametrize("skill_name", ["implement-worktree-no-merge", "resolve-failures"])
def test_declared_git_writes_require_a_git_checkout(
    tool_ctx, git_checkout, git_linked_worktree, tmp_path, skill_name: str
) -> None:
    invocation = _resolve_invocation(tool_ctx, skill_name)
    non_git = tmp_path / "autoskillit-runs" / "worktrees"
    non_git.mkdir(parents=True)

    with pytest.raises(GitCheckoutRequiredError):
        build_fresh_projection_context(str(non_git), invocation)

    build_fresh_projection_context(str(git_checkout), invocation)
    build_fresh_projection_context(str(git_linked_worktree), invocation)


def test_invocation_without_git_writes_can_bind_non_git_cwd(tool_ctx, tmp_path) -> None:
    invocation = _resolve_invocation(tool_ctx, "investigate")
    non_git = tmp_path / "plain-directory"
    non_git.mkdir()

    build_fresh_projection_context(str(non_git), invocation)


@pytest.mark.parametrize(
    ("skill_name", "expected"),
    [
        ("implement-worktree-no-merge", True),
        ("resolve-failures", True),
        ("investigate", False),
    ],
)
def test_invocation_git_requirement_matches_semantic_plan_declarations(
    tool_ctx, skill_name: str, expected: bool
) -> None:
    invocation = _resolve_invocation(tool_ctx, skill_name)

    assert invocation_requires_git_checkout(invocation) is expected
    assert invocation_requires_git_checkout(invocation) is any(
        plan.git_metadata_writes for plan in invocation.semantic_plans
    )
