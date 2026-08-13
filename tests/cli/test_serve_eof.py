"""Real-process coverage for normal stdio EOF shutdown."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._subprocess_ready import wait_for_subprocess_ready

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def test_stdio_eof_exits_and_cleans_lifespan_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    home_dir = tmp_path / "home"
    state_dir.mkdir()
    home_dir.mkdir()
    env = {
        **os.environ,
        "AUTOSKILLIT_STATE_DIR": str(state_dir),
        "HOME": str(home_dir),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "autoskillit"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=tmp_path,
    )
    sentinel = state_dir / "kitchen_state" / f"server_ready_{process.pid}.sentinel"
    try:
        wait_for_subprocess_ready(process, sentinel, deadline_s=10.0)
        assert process.stdin is not None
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate(timeout=10.0)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        raise

    diagnostics = (
        f"stdout={stdout[-4000:].decode(errors='replace')!r}\n"
        f"stderr={stderr[-4000:].decode(errors='replace')!r}"
    )
    assert process.returncode == 0, diagnostics
    assert not sentinel.exists(), diagnostics

    active_kitchens = home_dir / ".autoskillit" / "active_kitchens.json"
    if active_kitchens.exists():
        registry = json.loads(active_kitchens.read_text())
        assert registry.get("kitchens") == [], diagnostics
