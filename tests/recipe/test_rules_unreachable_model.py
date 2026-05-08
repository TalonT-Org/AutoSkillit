from __future__ import annotations

import pytest

from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_unreachable_steps_detects_orphan() -> None:
    wf = _make_workflow(
        {
            "start": {"tool": "run_cmd", "on_success": "done"},
            "orphan": {"tool": "run_cmd", "on_success": "done"},
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert any(f.rule == "unreachable-step" and "orphan" in f.message for f in findings)


def test_unreachable_steps_first_step_clean() -> None:
    wf = _make_workflow(
        {
            "start": {"tool": "run_cmd", "on_success": "done"},
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "unreachable-step" and "start" in f.step_name for f in findings)


def test_model_on_non_skill_triggers() -> None:
    wf = _make_workflow(
        {
            "check": {"tool": "test_check", "model": "sonnet", "on_success": "done"},
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert any(f.rule == "model-on-non-skill-step" for f in findings)


def test_model_on_non_skill_clean() -> None:
    wf = _make_workflow(
        {
            "do": {"tool": "run_skill", "model": "sonnet", "on_success": "done"},
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "model-on-non-skill-step" for f in findings)
