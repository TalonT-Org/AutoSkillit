"""Tests for canonical git remote identity resolution."""

from __future__ import annotations

import errno
import subprocess

import pytest

from autoskillit.core import resolve_repository_remote_identity_sync

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_timeout_diagnostic_preserves_detail_and_remote_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core.git_remote as mod

    def _run(command, **kwargs):
        if command[-1] == "remote.upstream.url":
            raise subprocess.TimeoutExpired(command, 15)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="https://github.com/TalonT-Org/AutoSkillit.git\n",
            stderr="",
        )

    monkeypatch.setattr(mod.subprocess, "run", _run)

    resolution = resolve_repository_remote_identity_sync(".")

    assert resolution.selected_remote == "origin"
    assert resolution.repository is not None
    timeout_diagnostic = resolution.probes[0].diagnostic
    assert timeout_diagnostic.startswith("timeout:TimeoutExpired:")
    assert "timed out after 15 seconds" in timeout_diagnostic
    assert len(timeout_diagnostic) <= 256


def test_os_error_diagnostic_is_sanitized_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core.git_remote as mod

    def _run(*args, **kwargs):
        raise PermissionError(
            errno.EACCES,
            "permission\ndenied\tby policy " + ("x" * 512),
        )

    monkeypatch.setattr(mod.subprocess, "run", _run)

    resolution = resolve_repository_remote_identity_sync(".")

    assert not resolution.usable_remote_found
    diagnostic = resolution.probes[0].diagnostic
    assert diagnostic.startswith("os_error:PermissionError:errno=13:permission denied by policy ")
    assert "\n" not in diagnostic
    assert "\t" not in diagnostic
    assert len(diagnostic) == 256
    assert diagnostic.endswith("...")
