from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestSkillCommandMissingPrefixRule:
    """Tests for the skill-command-missing-prefix semantic rule."""

    def test_scp1_prose_run_skill_warns(self) -> None:
        """SCP1: run_skill with prose skill_command → WARNING finding."""
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_skill",
                    "with": {"skill_command": "Fix the auth bug in main.py", "cwd": "/tmp"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert any(
            f.rule == "skill-command-missing-prefix" and f.severity == Severity.WARNING
            for f in findings
        ), "Expected skill-command-missing-prefix WARNING for prose skill_command"

    def test_scp2_prose_run_skill_warns(self) -> None:
        """SCP2: prose skill_command with on_failure step → WARNING finding.

        Distinguishing input: step has both on_success and on_failure transitions,
        and skill_command is a plain prose string without /autoskillit: prefix.
        """
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_skill",
                    "with": {"skill_command": "Investigate the bug", "cwd": "/tmp"},
                    "on_success": "done",
                    "on_failure": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert any(f.rule == "skill-command-missing-prefix" for f in findings)

    def test_scp3_autoskillit_prefix_no_warning(self) -> None:
        """SCP3: /autoskillit:investigate → no skill-command-missing-prefix warning."""
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:investigate error", "cwd": "/tmp"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "skill-command-missing-prefix" for f in findings)

    def test_scp4_bare_slash_local_skill_no_warning(self) -> None:
        """SCP4: /audit-arch (local skill, starts with /) → no warning."""
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/audit-arch", "cwd": "/tmp"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "skill-command-missing-prefix" for f in findings)

    def test_scp5_dynamic_prefix_no_warning(self) -> None:
        """SCP5: /audit-${{ inputs.audit_type }} → no warning (starts with /)."""
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_skill",
                    "with": {
                        "skill_command": "/audit-${{ inputs.audit_type }}",
                        "cwd": "/tmp",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "skill-command-missing-prefix" for f in findings)

    def test_scp6_non_skill_tool_no_warning(self) -> None:
        """SCP6: run_cmd step (not run_skill) → rule does not fire."""
        wf = _make_workflow(
            {
                "step": {
                    "tool": "run_cmd",
                    "with": {"cmd": "ls -la", "cwd": "/tmp"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "skill-command-missing-prefix" for f in findings)
