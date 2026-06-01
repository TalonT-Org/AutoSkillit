"""Integration test: create_and_publish_branch with clone-isolated origin topology.

Verifies that the MCP tool resolves the correct remote (upstream) when origin
has been rewritten to a file:// URL by _ensure_origin_isolated.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autoskillit.server.tools.tools_git import create_and_publish_branch
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _setup_isolated_clone(tmp_path: Path) -> tuple[Path, Path]:
    """Create a bare remote and a clone with the origin-isolated topology.

    Returns (bare_remote, clone_path).
    """
    bare = tmp_path / "remote.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    (source / "file.txt").write_text("content")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "init")
    _git(source, "remote", "add", "origin", str(bare))
    _git(source, "push", "-u", "origin", "HEAD")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(bare), str(clone)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(clone), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(clone), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )

    _git(clone, "remote", "set-url", "origin", f"file://{clone}")
    _git(clone, "remote", "add", "upstream", str(bare))

    return bare, clone


class TestCreateAndPublishBranchIsolation:
    """create_and_publish_branch must resolve the real remote, not file:// origin."""

    @pytest.mark.anyio
    async def test_detects_collision_via_upstream(
        self, tool_ctx_kitchen_open, tmp_path: Path
    ) -> None:
        bare, clone = _setup_isolated_clone(tmp_path)

        _git(clone, "checkout", "-b", "impl/42")
        (clone / "change.txt").write_text("work")
        _git(clone, "add", ".")
        _git(clone, "commit", "-m", "work")
        _git(clone, "push", "upstream", "impl/42")
        _git(clone, "checkout", "main")

        tool_ctx_kitchen_open.runner.push(_make_result(0, "abc123\trefs/heads/impl/42\n", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "main\n", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))

        result_str = await create_and_publish_branch(
            issue_slug="42",
            run_name="impl",
            issue_number="42",
            work_dir=str(clone),
            remote_url=str(bare),
        )
        json.loads(result_str)

        calls = tool_ctx_kitchen_open.runner.call_args_list
        ls_remote_call = calls[0]
        assert ls_remote_call[0][1] == "ls-remote", (
            f"First subprocess call should be ls-remote, got: {ls_remote_call[0]}"
        )
        remote_arg = ls_remote_call[0][2]
        assert remote_arg == "upstream", f"ls-remote should target 'upstream', not '{remote_arg}'"

    @pytest.mark.anyio
    async def test_falls_back_to_origin_without_upstream(
        self, tool_ctx_kitchen_open, tmp_path: Path
    ) -> None:
        """When no upstream remote exists, origin is used as fallback."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )
        (repo / "f.txt").write_text("x")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "init")

        bare = tmp_path / "remote.git"
        bare.mkdir()
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        _git(repo, "remote", "add", "origin", str(bare))
        _git(repo, "push", "-u", "origin", "HEAD")

        tool_ctx_kitchen_open.runner.push(_make_result(2, "", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "main\n", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))

        await create_and_publish_branch(
            issue_slug="42",
            run_name="impl",
            issue_number="42",
            work_dir=str(repo),
            remote_url=str(bare),
        )

        calls = tool_ctx_kitchen_open.runner.call_args_list
        ls_remote_call = calls[0]
        remote_arg = ls_remote_call[0][2]
        assert remote_arg == "origin", (
            f"Without upstream, ls-remote should target 'origin', got '{remote_arg}'"
        )
