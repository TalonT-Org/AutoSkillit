"""Boundary contract tests: MappingProxyType env through subprocess execution.

Validates that run_managed_async() and run_managed_sync() coerce any
Mapping env to a plain dict before passing it to the external subprocess API,
so that uvloop's strict ``type(env) is dict`` check is satisfied.
"""

from __future__ import annotations

import os
import subprocess
from types import MappingProxyType
from typing import Any

import pytest

from autoskillit.execution.process import run_managed_async, run_managed_sync

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


@pytest.mark.anyio
async def test_mappingproxy_env_through_run_managed_async(tmp_path: Any) -> None:
    """MappingProxyType env must not cause TypeError in run_managed_async."""
    env = MappingProxyType({"PATH": os.environ["PATH"]})
    result = await run_managed_async(
        cmd=["echo", "ok"],
        cwd=tmp_path,
        timeout=10.0,
        env=env,
    )
    assert result.returncode == 0
    assert "ok" in result.stdout


@pytest.mark.anyio
async def test_run_managed_async_coerces_env_to_dict(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_managed_async must coerce env to dict before owned Popen spawn."""
    captured_env: dict[str, Any] = {}

    from autoskillit.execution.process import spawn_owned_process as real_spawn

    def spy_spawn(*args: Any, **kwargs: Any) -> Any:
        captured_env["env"] = kwargs.get("env")
        return real_spawn(*args, **kwargs)

    monkeypatch.setattr("autoskillit.execution.process.spawn_owned_process", spy_spawn)
    env = MappingProxyType({"PATH": os.environ["PATH"], "FOO": "bar"})
    await run_managed_async(
        cmd=["echo", "ok"],
        cwd=tmp_path,
        timeout=10.0,
        env=env,
    )
    assert type(captured_env["env"]) is dict
    assert captured_env["env"]["FOO"] == "bar"


@pytest.mark.anyio
async def test_run_managed_async_none_env_passthrough(tmp_path: Any) -> None:
    """env=None must remain None (not coerced to empty dict)."""
    result = await run_managed_async(
        cmd=["echo", "ok"],
        cwd=tmp_path,
        timeout=10.0,
        env=None,
    )
    assert result.returncode == 0


def test_run_managed_sync_coerces_env_to_dict(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_managed_sync must coerce env to dict before subprocess.Popen."""
    captured_env: dict[str, Any] = {}

    real_popen = subprocess.Popen

    class SpyPopen(real_popen):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured_env["env"] = kwargs.get("env")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("subprocess.Popen", SpyPopen)
    env = MappingProxyType({"PATH": os.environ["PATH"], "FOO": "bar"})
    run_managed_sync(
        cmd=["echo", "ok"],
        cwd=tmp_path,
        timeout=10.0,
        env=env,
    )
    assert type(captured_env["env"]) is dict
    assert captured_env["env"]["FOO"] == "bar"


def test_headless_cmd_with_mappingproxy_env() -> None:
    """ClaudeHeadlessCmd must accept MappingProxyType env without error."""
    from autoskillit.execution.commands import ClaudeHeadlessCmd

    env = MappingProxyType({"PATH": "/usr/bin"})
    cmd = ClaudeHeadlessCmd(cmd=("echo", "test"), env=env)
    assert cmd.env["PATH"] == "/usr/bin"
    assert isinstance(cmd.env, MappingProxyType)
