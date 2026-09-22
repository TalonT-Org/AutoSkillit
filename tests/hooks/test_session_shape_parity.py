"""Parity checks between core session scope and hook-runtime shims."""

from __future__ import annotations

import warnings

import pytest

from autoskillit.core.types import ALL_SESSION_SHAPES, hookdef_session_scope, session_shape
from autoskillit.hook_registry import HOOK_REGISTRY
from autoskillit.hooks._runtime._hook_settings import (
    admit_hook_session_scope,
    hook_session_shape,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, (False, "skill")),
        ({"AUTOSKILLIT_SESSION_TYPE": "skill"}, (False, "skill")),
        (
            {
                "AUTOSKILLIT_HEADLESS": "1",
                "AUTOSKILLIT_SESSION_TYPE": "orchestrator",
            },
            (True, "orchestrator"),
        ),
        ({"AUTOSKILLIT_SESSION_TYPE": "fleet"}, (False, "fleet")),
        ({"AUTOSKILLIT_HEADLESS": "1"}, (True, "skill")),
        ({"AUTOSKILLIT_SESSION_TYPE": "ORCHESTRATOR"}, (False, "orchestrator")),
        ({"AUTOSKILLIT_SESSION_TYPE": "FLEET"}, (False, "fleet")),
    ],
)
def test_hook_session_shape_matches_core_accessor(
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    expected: tuple[bool, str],
) -> None:
    """The stdlib shim mirrors the core accessor for every valid tier.

    Hook subprocess stderr is not a usable channel, so unlike the core accessor
    the shim intentionally does not emit the deprecation warning for a headless
    session whose tier is unset.
    """
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        core_shape = session_shape()

    assert hook_session_shape() == expected
    assert hook_session_shape() == (core_shape.headless, core_shape.tier.value)


def test_hook_session_shape_keeps_unknown_tiers_for_hook_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid tiers remain normalized raw values so guards can deny explicitly."""
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "LeAf")

    assert hook_session_shape() == (True, "leaf")


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (
            {"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_SESSION_TYPE": "orchestrator"},
            (True, "orchestrator"),
        ),
        ({"AUTOSKILLIT_SESSION_TYPE": "skill"}, (False, "skill")),
        ({}, (False, "skill")),
        ({"AUTOSKILLIT_SESSION_TYPE": "LEAF"}, (False, "leaf")),
        # Mixed- and upper-case AUTOSKILLIT_SESSION_TYPE must normalize to
        # the canonical lowercase tier (skill_load_guard.py:149 used to
        # compare the raw env var case-sensitively and now lowercases first,
        # so pins here defend the unification).
        ({"AUTOSKILLIT_SESSION_TYPE": "SKILL"}, (False, "skill")),
        ({"AUTOSKILLIT_SESSION_TYPE": "Skill"}, (False, "skill")),
        # Empty-string short-circuit (issue #5121 / D14): an explicitly-empty
        # AUTOSKILLIT_SESSION_TYPE must default to "skill" identically to the
        # unset case. Pinned by the inline comment at _hook_settings.py around
        # the 'or "skill"' trailing expression.
        ({"AUTOSKILLIT_SESSION_TYPE": ""}, (False, "skill")),
    ],
)
def test_canonical_accessor_is_hook_session_shape(
    env: dict[str, str],
    expected: tuple[bool, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T4 — hook_session_shape() returns the canonical (headless, tier) tuple.

    Pins the empty-string short-circuit documented at _hook_settings.py
    alongside the trailing 'or "skill"' expression. Future regressions of
    that short-circuit (e.g. someone "simplifying" it away) would surface
    as a tier of "" leaking through to admit_hook_session_scope and
    downstream policy code.
    """
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert hook_session_shape() == expected


def test_hook_scope_admission_matches_core_scope() -> None:
    """Each HookDef has one admission result across core and hook runtimes."""
    for hookdef in HOOK_REGISTRY:
        scope = hookdef_session_scope(
            hookdef.session_scope,
            hookdef.exempt_session_types,
        )
        for shape in ALL_SESSION_SHAPES:
            assert admit_hook_session_scope(
                hookdef.session_scope,
                hookdef.exempt_session_types,
                (shape.headless, shape.tier.value),
            ) is scope.admits(shape)
