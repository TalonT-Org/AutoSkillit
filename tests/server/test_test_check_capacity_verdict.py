"""B6: capacity exhaustion routes through test_check's existing infrastructure_missing verdict."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_workspace import test_check
from tests.fakes import InMemoryTestRunner

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]
test_check.__test__ = False  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_capacity_exhaustion_yields_infrastructure_missing(tool_ctx, tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "Taskfile.yml").write_text("version: '3'\n")
    tool_ctx.tester = InMemoryTestRunner(
        check_infrastructure_result=(
            "/dev/shm: 100 bytes free of 1000 total -- run `task cleanup-shm` to reclaim "
            "stale pytest generations, or free space on /dev/shm manually"
        )
    )

    data = json.loads(await test_check(str(worktree)))

    assert data["passed"] is False
    assert data["infrastructure_missing"] is True
    assert "cleanup-shm" in data["error"]
