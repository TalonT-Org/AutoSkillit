"""B9: the Taskfile sweep and cleanup_stale derive the same TTL from the same constant and
compare the same stat field."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import SESSION_STALE_SECONDS
from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKFILE = REPO_ROOT / "Taskfile.yml"


def test_taskfile_sweep_routes_through_the_shared_constant() -> None:
    """cleanup-shm must invoke sweep-sessions (which reads SESSION_STALE_SECONDS), not a bare
    `find ... -mmin` with its own independent, un-shared TTL literal."""
    taskfile = load_yaml(TASKFILE)
    cleanup_shm_cmds = "\n".join(str(c) for c in taskfile["tasks"]["cleanup-shm"]["cmds"])

    assert "pytest_tmp_lifecycle.py sweep-sessions" in cleanup_shm_cmds
    assert not re.search(r"find\b.*-mmin", cleanup_shm_cmds), (
        "cleanup-shm must not hardcode its own -mmin TTL; it must route through the "
        "sweep-sessions subcommand, which shares SESSION_STALE_SECONDS with cleanup_stale"
    )


def test_cleanup_stale_default_is_the_shared_constant() -> None:
    import inspect

    from autoskillit.workspace.session_skills import DefaultSessionSkillManager

    sig = inspect.signature(DefaultSessionSkillManager.cleanup_stale)
    assert sig.parameters["max_age_seconds"].default == SESSION_STALE_SECONDS


def test_sweep_sessions_uses_st_mtime_not_st_atime(tmp_path: Path) -> None:
    """The shared TTL must be compared against the same stat field on both sides -- st_mtime,
    since /dev/shm is commonly mounted noatime, which leaves an atime gate inert."""
    import importlib.util
    import os
    import time

    script = REPO_ROOT / "scripts" / "pytest_tmp_lifecycle.py"
    spec = importlib.util.spec_from_file_location("pytest_tmp_lifecycle", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    sessions_root = tmp_path / "autoskillit-sessions"
    stale_dir = sessions_root / "headless-old"
    fresh_dir = sessions_root / "headless-new"
    stale_dir.mkdir(parents=True)
    fresh_dir.mkdir(parents=True)
    old = time.time() - SESSION_STALE_SECONDS - 3600
    # Backdate mtime only; leave atime at "now" -- if the sweep gated on atime this stale
    # dir would incorrectly survive.
    os.utime(stale_dir, (time.time(), old))

    import argparse

    module._sweep_sessions(argparse.Namespace(root=tmp_path))

    assert not stale_dir.exists()
    assert fresh_dir.exists()
