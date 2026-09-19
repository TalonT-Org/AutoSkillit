"""Tests for the two-axis session shape and admission scope contracts."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    ALL_SESSION_SHAPES,
    SESSION_SCOPE_ANY,
    SessionScope,
    SessionShape,
    SessionType,
    hookdef_session_scope,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_all_session_shapes_is_the_full_product() -> None:
    assert ALL_SESSION_SHAPES == frozenset(
        SessionShape(headless, tier) for headless in (False, True) for tier in SessionType
    )


def test_session_scope_of_enumerates_admitted_shapes() -> None:
    assert SessionScope.of(headless="interactive_only").admitted == frozenset(
        shape for shape in ALL_SESSION_SHAPES if not shape.headless
    )
    assert SessionScope.of(tiers={SessionType.FLEET}).admitted == frozenset(
        shape for shape in ALL_SESSION_SHAPES if shape.tier is SessionType.FLEET
    )
    assert SessionScope.of(
        headless="headless_only", exempt_tiers={SessionType.ORCHESTRATOR}
    ).admitted == {
        SessionShape(True, SessionType.SKILL),
        SessionShape(True, SessionType.FLEET),
    }
    assert SESSION_SCOPE_ANY.admitted == ALL_SESSION_SHAPES


def test_hookdef_session_scope_converts_registry_values() -> None:
    assert hookdef_session_scope(
        "headless_only", frozenset({"orchestrator", "fleet"})
    ) == SessionScope.of(
        headless="headless_only",
        exempt_tiers={SessionType.ORCHESTRATOR, SessionType.FLEET},
    )
    with pytest.raises(ValueError):
        hookdef_session_scope("any", frozenset({"unknown"}))
