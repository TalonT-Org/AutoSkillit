"""Parity checks between core session scope and hook-runtime shims.

Also covers the launched-interactive identity predicates, which read the
``hook_session_shape`` result together with the launch id.
"""

from __future__ import annotations

import warnings
from unittest.mock import patch

import pytest

from autoskillit.core.types import ALL_SESSION_SHAPES, hookdef_session_scope, session_shape
from autoskillit.hook_registry import HOOK_REGISTRY
from autoskillit.hooks._runtime._hook_settings import (
    admit_hook_session_scope,
    hook_is_interactive_cook,
    hook_is_launched_interactive,
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


_LAUNCH_ID = "0123456789abcdef"


@pytest.mark.parametrize(
    ("headless", "session_type", "launch_id", "interactive_cook", "launched_interactive"),
    [
        pytest.param(None, "skill", _LAUNCH_ID, True, True, id="cook-claude"),
        pytest.param("", "skill", _LAUNCH_ID, True, True, id="cook-codex"),
        pytest.param(None, "SKILL", _LAUNCH_ID, True, True, id="session-type-uppercase"),
        pytest.param(None, "orchestrator", _LAUNCH_ID, False, True, id="order"),
        pytest.param("0", "fleet", _LAUNCH_ID, False, True, id="managed-fleet"),
        pytest.param("0", "fleet", None, False, False, id="raw-fleet"),
        pytest.param("1", "skill", _LAUNCH_ID, False, False, id="headless-skill"),
        pytest.param("1", "orchestrator", _LAUNCH_ID, False, False, id="headless-orchestrator"),
        pytest.param(None, None, None, False, False, id="plain-plugin-session"),
        pytest.param(None, None, _LAUNCH_ID, False, True, id="session-type-unset"),
        pytest.param(None, "", _LAUNCH_ID, False, True, id="session-type-empty"),
        pytest.param(None, "skill", "", False, False, id="launch-id-empty"),
    ],
)
def test_launched_interactive_identity_predicates(
    headless: str | None,
    session_type: str | None,
    launch_id: str | None,
    interactive_cook: bool,
    launched_interactive: bool,
) -> None:
    """Each row fixes all three identity variables; None leaves one out of the environment.

    Only ``AUTOSKILLIT_HEADLESS == "1"`` is headless, and an empty or uppercase
    ``AUTOSKILLIT_SESSION_TYPE`` normalizes to the skill tier, so cook identity also
    needs the explicit non-empty session type.
    """
    row = {
        "AUTOSKILLIT_HEADLESS": headless,
        "AUTOSKILLIT_SESSION_TYPE": session_type,
        "AUTOSKILLIT_LAUNCH_ID": launch_id,
    }
    env = {name: value for name, value in row.items() if value is not None}

    with patch.dict("os.environ", env, clear=True):
        assert hook_is_interactive_cook() is interactive_cook
        assert hook_is_launched_interactive() is launched_interactive
