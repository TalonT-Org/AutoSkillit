"""Attestation-related error message contracts for run_skill.

The attestation-missing and attestation-inactive messages must name their own remedy
and reference complete_recipe_initialization.
"""

from __future__ import annotations

import pytest

from autoskillit.core import (
    RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE,
    RECIPE_EXECUTION_INACTIVE_MESSAGE,
    RUN_SKILL_ATTESTATION_PARAMS,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestAttestationErrorMessages:
    """Error messages that deny run_skill attestation must name their remedy."""

    def test_attestation_missing_message_names_all_required_params(self) -> None:
        for param in RUN_SKILL_ATTESTATION_PARAMS:
            assert param in RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE, (
                f"RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE must name {param!r}"
            )

    def test_attestation_missing_message_names_remedy_tool(self) -> None:
        assert "complete_recipe_initialization" in RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE

    def test_attestation_missing_message_preserves_delivered_skill_input_shape(self) -> None:
        for required in (
            "skill_input_shapes[step_name]",
            "ordered keys",
            "unresolved_defaults",
            "replace available values in place",
            "never delete or invent a key",
            '""',
            "0",
            "False",
        ):
            assert required in RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE

    def test_inactive_message_does_not_say_standalone_mode(self) -> None:
        assert "standalone mode" not in RECIPE_EXECUTION_INACTIVE_MESSAGE.lower()

    def test_inactive_message_names_remedy_tool(self) -> None:
        assert (
            "complete_recipe_initialization" in RECIPE_EXECUTION_INACTIVE_MESSAGE
            or "open_kitchen" in RECIPE_EXECUTION_INACTIVE_MESSAGE
        )
