"""Tests for SessionType resolver and constants."""

from __future__ import annotations

import warnings

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Group A — SessionType resolver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_val, expected, suppress_deprecation, strict_no_deprecation",
    [
        ("orchestrator", "ORCHESTRATOR", False, False),
        ("skill", "SKILL", False, False),
        ("ORCHESTRATOR", "ORCHESTRATOR", False, False),
        (None, "SKILL", False, False),
        ("bogus", "SKILL", True, False),
        ("fleet", "FLEET", False, False),
        ("FLEET", "FLEET", False, False),
        ("fleet", "FLEET", False, True),
    ],
    ids=[
        "orchestrator",
        "skill",
        "case-insensitive",
        "unset",
        "invalid-defaults-skill",
        "fleet",
        "fleet-case-insensitive",
        "fleet-no-warning",
    ],
)
def test_session_type_resolver(
    monkeypatch, env_val, expected, suppress_deprecation, strict_no_deprecation
):
    from autoskillit.core import SessionType, session_type

    if env_val is None:
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    else:
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", env_val)
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

    if strict_no_deprecation:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            result = session_type()
    elif suppress_deprecation:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = session_type()
    else:
        result = session_type()

    assert result is SessionType[expected]


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
    assert result is SessionType.SKILL


def test_no_warning_when_both_unset(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = session_type()
    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert result is SessionType.SKILL
    assert len(deprecation_warnings) == 0


# ---------------------------------------------------------------------------
# Group C (partial) — Constants
# ---------------------------------------------------------------------------


def test_session_type_enum_values_match_constants():
    from autoskillit.core import (
        SESSION_TYPE_ORCHESTRATOR,
        SESSION_TYPE_SKILL,
        SessionType,
    )

    assert SessionType.ORCHESTRATOR.value == SESSION_TYPE_ORCHESTRATOR
    assert SessionType.SKILL.value == SESSION_TYPE_SKILL


def test_session_type_env_var_constant():
    from autoskillit.core import SESSION_TYPE_ENV_VAR

    assert SESSION_TYPE_ENV_VAR == "AUTOSKILLIT_SESSION_TYPE"


# ---------------------------------------------------------------------------
# Deprecation compat — legacy "leaf" value
# ---------------------------------------------------------------------------


def test_leaf_value_maps_to_skill_with_deprecation_warning(monkeypatch):
    from autoskillit.core import SessionType, session_type

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
    with pytest.warns(DeprecationWarning, match="leaf") as warning_list:
        result = session_type()
    assert result is SessionType.SKILL
    assert "SKILL" in str(warning_list[0].message)


# ---------------------------------------------------------------------------
# Fleet alias tests — T1 shims
# ---------------------------------------------------------------------------


def test_session_type_invalid_session_type_emits_warning(monkeypatch):
    from autoskillit.core import session_type

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
    with pytest.warns(DeprecationWarning, match="Invalid"):
        session_type()


def test_session_type_enum_fleet_value():
    from autoskillit.core import SessionType

    assert SessionType.FLEET.value == "fleet"


def test_session_type_fleet_constant_matches_enum():
    from autoskillit.core import SessionType
    from autoskillit.core.types._type_constants import SESSION_TYPE_FLEET

    assert SessionType.FLEET.value == SESSION_TYPE_FLEET


# ---------------------------------------------------------------------------
# Group D — Module placement
# ---------------------------------------------------------------------------


def test_session_type_is_defined_in_type_helpers():
    """session_type() must live in _type_helpers, not _type_enums."""
    import importlib
    import inspect

    from autoskillit.core.types._type_helpers import session_type as fn_helpers

    type_enums = importlib.import_module("autoskillit.core.types._type_enums")
    importlib.reload(type_enums)
    assert not hasattr(type_enums, "session_type"), (
        "session_type must not be defined in _type_enums after relocation"
    )

    assert inspect.getmodule(fn_helpers).__name__ == "autoskillit.core.types._type_helpers"
    assert inspect.getfile(fn_helpers).endswith("_type_helpers.py")


def test_session_type_docstring_references_orchestration_levels():
    """SessionType docstring must reference L1/L2/L3 level labels."""
    from autoskillit.core import SessionType

    doc = SessionType.__doc__ or ""
    assert "L3" in doc
    assert "L2" in doc
    assert "L1" in doc
    assert "L0" in doc
    assert "FLEET" in doc
    assert "ORCHESTRATOR" in doc
    assert "SKILL" in doc
    assert "mid-tier" not in doc
    assert "bottom-tier" not in doc
    assert "Tier discriminator" not in doc
