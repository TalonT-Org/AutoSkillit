from __future__ import annotations

from unittest.mock import Mock

import pytest

from autoskillit.core._version_snapshot import collect_version_snapshot

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    collect_version_snapshot.cache_clear()
    yield
    collect_version_snapshot.cache_clear()


def test_codex_version_populated_with_codex_backend() -> None:
    backend = Mock()
    backend.name = "codex"
    backend.version.return_value = "1.2.3"
    backend.list_plugins.return_value = []
    result = collect_version_snapshot(backend)
    assert result["codex_version"] == "1.2.3"


def test_codex_version_empty_when_no_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core._version_snapshot as mod

    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)

    calls: list[list[str]] = []
    original_run = mod.subprocess.run

    def _track_and_allow_claude(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        calls.append(list(cmd))
        if cmd and cmd[0] not in ("claude", "codex"):
            return original_run(*args, **kwargs)
        if cmd and cmd[0] == "codex":
            raise AssertionError("codex should not be called when backend is unset")
        raise FileNotFoundError("claude not found in test")

    monkeypatch.setattr(mod.subprocess, "run", _track_and_allow_claude)
    result = collect_version_snapshot()
    assert result["codex_version"] == ""
    assert not any(c[0] == "codex" for c in calls if c)


def test_codex_version_key_value_in_snapshot() -> None:
    backend = Mock()
    backend.name = "codex"
    backend.version.return_value = "1.0"
    backend.list_plugins.return_value = []
    result = collect_version_snapshot(backend)
    assert result["codex_version"] == "1.0"
