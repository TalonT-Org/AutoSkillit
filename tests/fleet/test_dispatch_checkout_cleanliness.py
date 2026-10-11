"""Ensure a real fleet dispatch adds no git-visible AutoSkillit state."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from autoskillit.core.io import _AUTOSKILLIT_GITIGNORE_ENTRIES
from autoskillit.fleet import execute_dispatch
from tests._git_topology import commit_file, init_checkout
from tests.fleet._helpers import (
    _noop_quota_refresher,
    _setup_dispatch,
    _simple_prompt_builder,
)

pytestmark = [
    pytest.mark.layer("fleet"),
    pytest.mark.medium,
    pytest.mark.feature("fleet"),
]


@pytest.fixture(autouse=True)
def _isolate_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _autoskillit_status(project_dir: Path) -> set[str]:
    output = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            ".autoskillit",
        ],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {line for line in output.splitlines() if line.strip()}


@pytest.mark.anyio
async def test_dispatch_adds_nothing_git_visible_under_autoskillit(
    tool_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tool_ctx.project_dir
    init_checkout(project_dir)
    commit_file(
        project_dir,
        ".autoskillit/.gitignore",
        "\n".join(_AUTOSKILLIT_GITIGNORE_ENTRIES) + "\n",
        "add AutoSkillit ignore entries",
    )
    _setup_dispatch(tool_ctx, monkeypatch)
    before = _autoskillit_status(project_dir)

    await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="test-recipe",
        task="task",
        ingredients=None,
        dispatch_name="dispatch",
        timeout_sec=None,
        prompt_builder=_simple_prompt_builder,
        quota_refresher=_noop_quota_refresher,
    )

    assert len(tool_ctx.executor.dispatch_calls) == 1
    records_dir = project_dir / ".autoskillit" / "managed-headless-session-lineage" / "records"
    assert records_dir.is_dir() and any(records_dir.iterdir())
    assert _autoskillit_status(project_dir) - before == set()
