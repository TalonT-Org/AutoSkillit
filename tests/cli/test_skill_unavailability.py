"""Focused CLI surfacing tests for refused session skills."""

from __future__ import annotations

import pytest

from autoskillit.cli.session._session_launch import (
    append_skill_unavailability,
    render_skill_unavailability,
)
from autoskillit.core import SkillSemanticOperation, SkillUnavailabilityPayload
from autoskillit.workspace import SkillUnavailableMetadata

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _payload(
    *unavailable: SkillUnavailableMetadata,
) -> SkillUnavailabilityPayload:
    return {
        "backend": "codex",
        "unavailable": tuple(item.to_payload() for item in unavailable),
    }


def test_render_skill_unavailability_groups_and_sorts(capsys: pytest.CaptureFixture[str]) -> None:
    render_skill_unavailability(
        _payload(
            SkillUnavailableMetadata(
                skill="zeta",
                backend="codex",
                operation=SkillSemanticOperation.REQUIRED_JOIN,
                diagnostic="fixed join unavailable",
            ),
            SkillUnavailableMetadata(
                skill="beta",
                backend="codex",
                operation=SkillSemanticOperation.CHILD_SPAWN,
                diagnostic="child spawn unavailable",
            ),
            SkillUnavailableMetadata(
                skill="alpha",
                backend="codex",
                operation=SkillSemanticOperation.CHILD_SPAWN,
                diagnostic="child spawn unavailable",
            ),
        )
    )

    assert capsys.readouterr().out.splitlines() == [
        "2 skills unavailable on this backend (child_spawn: child spawn unavailable): alpha, beta",
        "1 skills unavailable on this backend (required_join: fixed join unavailable): zeta",
    ]


def test_append_skill_unavailability_preserves_none_and_appends_canonical_block() -> None:
    payload = _payload(
        SkillUnavailableMetadata(
            skill="investigate",
            backend="codex",
            operation=SkillSemanticOperation.REQUIRED_JOIN,
            diagnostic="fixed join unavailable",
        )
    )

    assert append_skill_unavailability(None, payload) is None
    prompt = append_skill_unavailability("base prompt  \n", payload)

    assert prompt == (
        "base prompt\n\n"
        '<autoskillit_skill_unavailability>{"backend":"codex","unavailable":'
        '[{"backend":"codex","diagnostic":"fixed join unavailable",'
        '"operation":"required_join","skill":"investigate"}]}'
        "</autoskillit_skill_unavailability>"
    )
    assert prompt.count("<autoskillit_skill_unavailability>") == 1
    assert append_skill_unavailability(prompt, payload) == prompt


def test_append_skill_unavailability_leaves_prompt_without_refusals_unchanged() -> None:
    assert append_skill_unavailability("base prompt  \n", _payload()) == "base prompt  \n"


def test_skill_unavailability_payload_has_only_documented_wire_keys() -> None:
    payload = _payload(
        SkillUnavailableMetadata(
            skill="investigate",
            backend="codex",
            operation=SkillSemanticOperation.REQUIRED_JOIN,
            diagnostic="fixed join unavailable",
        )
    )

    assert set(payload) == {"backend", "unavailable"}
    refusal = payload["unavailable"][0]
    assert set(refusal) == {"skill", "backend", "operation", "diagnostic"}
