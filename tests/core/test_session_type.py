"""Tests for SessionType resolver and constants."""

from __future__ import annotations

import warnings

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Group A — SessionType resolver
# ---------------------------------------------------------------------------


def test_session_type_returns_franchise(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert session_type() is SessionType.FLEET


def test_session_type_returns_orchestrator(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    assert session_type() is SessionType.ORCHESTRATOR


def test_session_type_returns_leaf(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
    assert session_type() is SessionType.LEAF


def test_session_type_case_insensitive(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "ORCHESTRATOR")
    assert session_type() is SessionType.ORCHESTRATOR


def test_session_type_defaults_to_leaf_when_unset(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    assert session_type() is SessionType.LEAF


def test_session_type_defaults_to_leaf_on_invalid(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "bogus")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert session_type() is SessionType.LEAF


def test_session_type_invalid_emits_deprecation_warning(monkeypatch):
    from autoskillit.core import session_type

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "bogus")
    with pytest.warns(DeprecationWarning, match="Invalid AUTOSKILLIT_SESSION_TYPE"):
        session_type()


def test_transitional_bridge_headless_without_type_warns(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    with pytest.warns(DeprecationWarning, match="AUTOSKILLIT_HEADLESS=1 without"):
        result = session_type()
    assert result is SessionType.LEAF


def test_no_warning_when_both_unset(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = session_type()
    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert result is SessionType.LEAF
    assert len(deprecation_warnings) == 0


# ---------------------------------------------------------------------------
# Group C (partial) — Constants
# ---------------------------------------------------------------------------


def test_session_type_enum_values_match_constants():
    from autoskillit.core import (
        SESSION_TYPE_FRANCHISE,
        SESSION_TYPE_LEAF,
        SESSION_TYPE_ORCHESTRATOR,
        SessionType,
    )

    assert SessionType.FRANCHISE.value == SESSION_TYPE_FRANCHISE
    assert SessionType.ORCHESTRATOR.value == SESSION_TYPE_ORCHESTRATOR
    assert SessionType.LEAF.value == SESSION_TYPE_LEAF


def test_session_type_env_var_constant():
    from autoskillit.core import SESSION_TYPE_ENV_VAR

    assert SESSION_TYPE_ENV_VAR == "AUTOSKILLIT_SESSION_TYPE"


# ---------------------------------------------------------------------------
# Fleet alias tests — T1 shims
# ---------------------------------------------------------------------------


def test_session_type_returns_fleet(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    assert session_type() is SessionType.FLEET


def test_session_type_fleet_case_insensitive(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "FLEET")
    assert session_type() is SessionType.FLEET


def test_session_type_franchise_alias_emits_deprecation_warning(monkeypatch):
    from autoskillit.core import session_type

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
    with pytest.warns(DeprecationWarning, match="deprecated.*fleet"):
        session_type()


def test_session_type_fleet_emits_no_warning(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        result = session_type()
    assert result is SessionType.FLEET


def test_session_type_enum_fleet_value():
    from autoskillit.core import SessionType

    assert SessionType.FLEET.value == "fleet"


def test_session_type_fleet_constant_matches_enum():
    from autoskillit.core import SessionType
    from autoskillit.core._type_constants import SESSION_TYPE_FLEET

    assert SessionType.FLEET.value == SESSION_TYPE_FLEET
