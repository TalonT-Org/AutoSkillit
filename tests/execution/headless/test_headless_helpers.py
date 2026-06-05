"""Behavioral tests for _headless_helpers private helpers."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestDeriveStepNameFromSkillCommand:
    def test_strips_slash(self):
        from autoskillit.execution.headless._headless_helpers import (
            _derive_step_name_from_skill_command,
        )

        assert _derive_step_name_from_skill_command("/autoskillit:make-plan foo") == "make-plan"

    def test_strips_dollar(self):
        from autoskillit.execution.headless._headless_helpers import (
            _derive_step_name_from_skill_command,
        )

        assert _derive_step_name_from_skill_command("$autoskillit:make-plan foo") == "make-plan"

    def test_bare_slash_command(self):
        from autoskillit.execution.headless._headless_helpers import (
            _derive_step_name_from_skill_command,
        )

        assert _derive_step_name_from_skill_command("/sous-chef") == "sous-chef"

    def test_bare_dollar_command(self):
        from autoskillit.execution.headless._headless_helpers import (
            _derive_step_name_from_skill_command,
        )

        assert _derive_step_name_from_skill_command("$sous-chef") == "sous-chef"

    def test_empty_input(self):
        from autoskillit.execution.headless._headless_helpers import (
            _derive_step_name_from_skill_command,
        )

        assert _derive_step_name_from_skill_command("") == ""
