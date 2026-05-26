from __future__ import annotations

import subprocess

import pytest

from autoskillit.core._version_snapshot import collect_version_snapshot

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    collect_version_snapshot.cache_clear()
    yield
    collect_version_snapshot.cache_clear()


def test_codex_version_populated_when_backend_is_codex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core._version_snapshot as mod

    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "codex")

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="1.2.3", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    result = collect_version_snapshot()
    assert result["codex_version"] != ""


def test_codex_version_empty_for_claude_code_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core._version_snapshot as mod

    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "claude-code")

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    result = collect_version_snapshot()
    assert result["codex_version"] == ""


def test_codex_version_empty_when_backend_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core._version_snapshot as mod

    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)

    def _no_codex_call(cmd, **kwargs):
        if cmd[0] == "codex":
            raise AssertionError("codex should not be called when backend is unset")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", _no_codex_call)
    result = collect_version_snapshot()
    assert result["codex_version"] == ""


def test_no_cross_contamination_claude_code_version_empty_for_codex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core._version_snapshot as mod

    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "codex")

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="1.2.3", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    result = collect_version_snapshot()
    assert result["claude_code_version"] == ""


def test_codex_version_key_present_in_snapshot_result() -> None:
    result = collect_version_snapshot()
    assert "codex_version" in result
