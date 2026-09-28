"""Admission tests for skills that declare git-metadata writes."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.mark.anyio
async def test_git_writing_skill_is_rejected_from_non_git_worktrees_directory(
    tool_ctx_kitchen_open, tmp_path
) -> None:
    non_git = tmp_path / "autoskillit-runs" / "worktrees"
    non_git.mkdir(parents=True)

    result = json.loads(
        await run_skill(
            "/autoskillit:implement-worktree-no-merge plan.md",
            cwd=str(non_git),
        )
    )

    assert result["success"] is False
    assert result["is_error"] is True
    assert result["stage"] == "preflight:git_checkout"
    assert result["retriable"] is False
    assert "implement-worktree-no-merge" in result["error"]
    assert str(non_git.resolve()) in result["error"]
    assert tool_ctx_kitchen_open.runner.call_args_list == []


@pytest.mark.anyio
async def test_git_writing_skill_from_main_checkout_passes_checkout_gate(
    tool_ctx_kitchen_open, git_checkout
) -> None:
    result = json.loads(
        await run_skill(
            "/autoskillit:implement-worktree-no-merge plan.md",
            cwd=str(git_checkout),
        )
    )

    assert result.get("stage") != "preflight:git_checkout"


@pytest.mark.anyio
async def test_git_writing_skill_from_linked_worktree_passes_checkout_gate(
    tool_ctx_kitchen_open, git_linked_worktree
) -> None:
    result = json.loads(
        await run_skill(
            "/autoskillit:implement-worktree-no-merge plan.md",
            cwd=str(git_linked_worktree),
        )
    )

    assert result.get("stage") != "preflight:git_checkout"


@pytest.mark.anyio
async def test_skill_without_git_writes_can_run_from_non_git_directory(
    tool_ctx_kitchen_open, tmp_path
) -> None:
    non_git = tmp_path / "autoskillit-runs" / "worktrees"
    non_git.mkdir(parents=True)

    result = json.loads(await run_skill("/autoskillit:investigate foo", cwd=str(non_git)))

    assert result.get("stage") != "preflight:git_checkout"


@pytest.mark.parametrize(
    ("skill_name", "enable_pack"),
    [
        ("resolve-failures", None),
        ("resolve-merge-conflicts", None),
        ("generate-report", "research"),
    ],
)
@pytest.mark.anyio
async def test_other_git_writing_skills_are_rejected_before_spawn(
    tool_ctx_kitchen_open,
    tmp_path,
    skill_name: str,
    enable_pack: str | None,
) -> None:
    if enable_pack is not None:
        tool_ctx_kitchen_open.config.packs.enabled.append(enable_pack)
    non_git = tmp_path / "autoskillit-runs" / "worktrees"
    non_git.mkdir(parents=True)

    result = json.loads(await run_skill(f"/autoskillit:{skill_name} plan.md", cwd=str(non_git)))

    assert result["stage"] == "preflight:git_checkout"
    assert tool_ctx_kitchen_open.runner.call_args_list == []


@pytest.mark.anyio
async def test_empty_cwd_uses_non_git_process_directory_for_admission(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    non_git = tmp_path / "autoskillit-runs" / "worktrees"
    non_git.mkdir(parents=True)
    monkeypatch.chdir(non_git)

    result = json.loads(
        await run_skill("/autoskillit:implement-worktree-no-merge plan.md", cwd="")
    )

    assert result["stage"] == "preflight:git_checkout"
    assert tool_ctx_kitchen_open.runner.call_args_list == []


@pytest.mark.anyio
async def test_empty_cwd_uses_git_process_directory_for_admission(
    tool_ctx_kitchen_open, monkeypatch, git_checkout
) -> None:
    monkeypatch.chdir(git_checkout)

    result = json.loads(
        await run_skill("/autoskillit:implement-worktree-no-merge plan.md", cwd="")
    )

    assert result.get("stage") != "preflight:git_checkout"
