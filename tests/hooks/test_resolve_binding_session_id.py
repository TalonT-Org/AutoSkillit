"""Direct coverage for ``resolve_binding_session_id`` (security-critical identity resolver)."""

from __future__ import annotations

import importlib

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


@pytest.fixture
def resolve_binding_session_id():
    """Return the live ``resolve_binding_session_id`` function from the settings bridge."""
    module = importlib.import_module("autoskillit.hooks._runtime._hook_settings")
    return module.resolve_binding_session_id


def test_prefers_env_var(resolve_binding_session_id, monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_MANAGED_JOIN_PARENT_ID", "env-parent-1")

    assert resolve_binding_session_id({"session_id": "payload-parent-2"}) == "env-parent-1"


def test_falls_back_to_payload_session_id(resolve_binding_session_id) -> None:
    assert resolve_binding_session_id({"session_id": "payload-parent-1"}) == "payload-parent-1"


def test_returns_empty_when_unset(resolve_binding_session_id) -> None:
    assert resolve_binding_session_id({}) == ""
    assert resolve_binding_session_id({"session_id": None}) == ""


def test_ignores_non_string_session_id(resolve_binding_session_id) -> None:
    assert resolve_binding_session_id({"session_id": 123}) == ""
    assert resolve_binding_session_id({"session_id": ["array"]}) == ""


def test_treats_empty_env_as_unset(resolve_binding_session_id, monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_MANAGED_JOIN_PARENT_ID", "")

    assert resolve_binding_session_id({"session_id": "payload-id"}) == "payload-id"
