"""Direct coverage for ``resolve_binding_session_id`` (security-critical identity resolver)."""

from __future__ import annotations

import importlib

import pytest

from autoskillit.hooks._runtime._hook_constants import MANAGED_JOIN_PARENT_ID_ENV_VAR

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def _resolve_binding_session_id():
    """Return the live ``resolve_binding_session_id`` function from the settings bridge."""
    module = importlib.import_module("autoskillit.hooks._runtime._hook_settings")
    return module.resolve_binding_session_id


def test_prefers_env_var(monkeypatch) -> None:
    monkeypatch.setenv(MANAGED_JOIN_PARENT_ID_ENV_VAR, "env-parent-1")

    assert _resolve_binding_session_id()({"session_id": "payload-parent-2"}) == "env-parent-1"


def test_falls_back_to_payload_session_id() -> None:
    assert _resolve_binding_session_id()({"session_id": "payload-parent-1"}) == "payload-parent-1"


def test_returns_empty_when_unset() -> None:
    assert _resolve_binding_session_id()({}) == ""
    assert _resolve_binding_session_id()({"session_id": None}) == ""


def test_ignores_non_string_session_id() -> None:
    assert _resolve_binding_session_id()({"session_id": 123}) == ""
    assert _resolve_binding_session_id()({"session_id": ["array"]}) == ""


def test_treats_empty_env_as_unset(monkeypatch) -> None:
    monkeypatch.setenv(MANAGED_JOIN_PARENT_ID_ENV_VAR, "")

    assert _resolve_binding_session_id()({"session_id": "payload-id"}) == "payload-id"
