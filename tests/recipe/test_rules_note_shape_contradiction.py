"""Tests for the note-shape-contradiction semantic rule (synthetic steps)."""

from __future__ import annotations

import pytest

from autoskillit.recipe.registry import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_note_with_inline_append_instruction_flagged() -> None:
    """Step note instructing inline-arg concatenation is flagged when skill_inputs exist."""
    recipe = _make_workflow(
        {
            "step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:foo",
                    "skill_inputs": {"topic": "bar"},
                },
                "note": ("append the issue URL to the skill_command: '/autoskillit:foo {topic}'"),
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    matched = [f for f in findings if f.rule == "note-shape-contradiction"]
    assert len(matched) == 1
    assert matched[0].step_name == "step"


@pytest.mark.parametrize(
    ("expected_phrase", "note"),
    [
        pytest.param(
            "embed-in-skill_command",
            "embed it into the skill_command",
            id="embed",
        ),
        pytest.param(
            "concatenate-to-skill_command",
            "concatenate the topic to the skill_command",
            id="concatenate",
        ),
        pytest.param(
            "replace-in-skill_command",
            "replace {topic} in the skill_command",
            id="replace",
        ),
        pytest.param(
            "inline-arg-example-quoted",
            'Use "/autoskillit:foo {topic}" for this call',
            id="quoted-example",
        ),
        pytest.param(
            "inline-arg-example-unquoted",
            "Call /autoskillit:foo {topic} for this step",
            id="unquoted-example",
        ),
    ],
)
def test_additional_inline_append_patterns_flagged(expected_phrase: str, note: str) -> None:
    """Every detector family has a positive case independent of the append branch."""
    recipe = _make_workflow(
        {
            "step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:foo",
                    "skill_inputs": {"topic": "bar"},
                },
                "note": note,
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    matched = [f for f in findings if f.rule == "note-shape-contradiction"]
    assert len(matched) == 1
    assert expected_phrase in matched[0].message


@pytest.mark.parametrize(
    "note",
    [
        pytest.param("do NOT append it to skill_command", id="not"),
        pytest.param("never append it to skill_command", id="never"),
        pytest.param("don't append it to skill_command", id="dont"),
    ],
)
def test_negated_inline_append_instruction_clean(note: str) -> None:
    """A prohibition on inline arguments describes the correct structured shape."""
    recipe = _make_workflow(
        {
            "step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:foo",
                    "skill_inputs": {"topic": "bar"},
                },
                "note": note,
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    matched = [f for f in findings if f.rule == "note-shape-contradiction"]
    assert matched == []


def test_note_without_skill_inputs_clean() -> None:
    """Pre-migration inline-args form is internally consistent — no finding."""
    recipe = _make_workflow(
        {
            "step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:foo {bar}",
                },
                "note": "append the URL to the skill_command",
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    matched = [f for f in findings if f.rule == "note-shape-contradiction"]
    assert matched == []


def test_note_describing_skill_inputs_clean() -> None:
    """Note correctly describes the skill_inputs shape — no finding."""
    recipe = _make_workflow(
        {
            "step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:foo",
                    "skill_inputs": {"topic": "bar"},
                },
                "note": "The topic is passed via skill_inputs.topic",
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    matched = [f for f in findings if f.rule == "note-shape-contradiction"]
    assert matched == []


def test_step_without_note_clean() -> None:
    """Step with no note — no finding."""
    recipe = _make_workflow(
        {
            "step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:foo",
                    "skill_inputs": {"topic": "bar"},
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    matched = [f for f in findings if f.rule == "note-shape-contradiction"]
    assert matched == []


def test_step_without_with_args_clean() -> None:
    """Step with note but no with_args — no finding."""
    recipe = _make_workflow(
        {
            "step": {
                "tool": "run_skill",
                "note": "some text about the step",
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    matched = [f for f in findings if f.rule == "note-shape-contradiction"]
    assert matched == []


def test_dynamic_skill_command_template_clean() -> None:
    """Dynamic command-name templates are not false-positively flagged."""
    recipe = _make_workflow(
        {
            "step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:arch-lens-{slug}",
                    "skill_inputs": {"context_path": "bar"},
                },
                "note": (
                    "Iterate lenses and call /autoskillit:arch-lens-{slug} {context_path} for each"
                ),
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    matched = [f for f in findings if f.rule == "note-shape-contradiction"]
    assert matched == []
