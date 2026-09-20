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
