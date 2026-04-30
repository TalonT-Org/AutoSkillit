"""Contract tests: server._misc module."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.mark.anyio
async def test_resolve_repo_from_remote_returns_empty_for_file_url(tmp_path: Path) -> None:
    """Regression guard: file:// origin, no upstream → resolve_repo_from_remote returns ''.

    Creates a minimal git repo with only a file:// origin remote (no upstream).
    """
    from autoskillit.server._misc import resolve_repo_from_remote

    repo = tmp_path / "clone"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", f"file://{tmp_path}/bare"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    # No upstream remote — simulates file:// origin-only scenario

    result = await resolve_repo_from_remote(str(repo))
    assert result == ""


def test_resolve_repo_from_remote_exists() -> None:
    """Function renamed from infer_repo_from_remote must exist at new name."""
    import inspect

    from autoskillit.server._misc import resolve_repo_from_remote

    assert inspect.iscoroutinefunction(resolve_repo_from_remote)


def test_notify_module_exports():
    from autoskillit.server._notify import _notify, track_response_size, _get_ctx_or_none

    assert callable(_notify)
    assert callable(track_response_size)
    assert callable(_get_ctx_or_none)


def test_misc_module_exports():
    from autoskillit.server._misc import (
        _prime_quota_cache,
        _quota_refresh_loop,
        _apply_triage_gate,
        _hook_config_path,
        _HOOK_CONFIG_FILENAME,
        _HOOK_DIR_COMPONENTS,
        _extract_block,
        resolve_repo_from_remote,
    )

    assert callable(_prime_quota_cache)
    assert callable(_quota_refresh_loop)
    assert callable(_apply_triage_gate)
    assert callable(_hook_config_path)
    assert callable(_extract_block)
    assert callable(resolve_repo_from_remote)
    assert isinstance(_HOOK_CONFIG_FILENAME, str)
    assert isinstance(_HOOK_DIR_COMPONENTS, tuple)
