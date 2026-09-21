"""Direct coverage for ``resolve_binding_session_id`` (security-critical identity resolver)."""

from __future__ import annotations

import importlib

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


@pytest.fixture
def settings_module(monkeypatch: pytest.MonkeyPatch):
    """Reload the stdlib-only settings bridge under controlled env state."""
    module = importlib.import_module("autoskillit.hooks._runtime._hook_settings")
    importlib.reload(module)
    yield module
    importlib.reload(module)


def test_resolve_binding_session_id_prefers_env_var(settings_module, monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_MANAGED_JOIN_PARENT_ID", "env-parent-1")

    assert settings_module.resolve_binding_session_id({"session_id": "payload-parent-2"}) == (
        "env-parent-1"
    )


def test_resolve_binding_session_id_falls_back_to_payload_session_id(
    settings_module,
) -> None:
    assert settings_module.resolve_binding_session_id({"session_id": "payload-parent-1"}) == (
        "payload-parent-1"
    )


def test_resolve_binding_session_id_returns_empty_when_unset(settings_module) -> None:
    assert settings_module.resolve_binding_session_id({}) == ""
    assert settings_module.resolve_binding_session_id({"session_id": None}) == ""


def test_resolve_binding_session_id_ignores_non_string_session_id(settings_module) -> None:
    assert settings_module.resolve_binding_session_id({"session_id": 123}) == ""
    assert settings_module.resolve_binding_session_id({"session_id": ["array"]}) == ""


def test_resolve_binding_session_id_treats_empty_env_as_unset(
    settings_module,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_MANAGED_JOIN_PARENT_ID", "")

    assert settings_module.resolve_binding_session_id({"session_id": "payload-id"}) == (
        "payload-id"
    )


def test_resolve_binding_session_id_env_takes_priority_over_payload_session_id(
    settings_module,
    monkeypatch,
) -> None:
    """Env-managed identity always wins over the payload's session_id field."""
    monkeypatch.setenv("AUTOSKILLIT_MANAGED_JOIN_PARENT_ID", "managed-parent")

    assert (
        settings_module.resolve_binding_session_id({"session_id": "claude-original-session"})
        == "managed-parent"
    )
