"""Contract tests for the environment-setup skill design document.

These tests verify the design document exists and contains all required
sections per issue #838. They fail until the design doc is written.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DESIGN_DOC = (
    Path(__file__).resolve().parents[2] / "docs" / "design" / "environment-setup-skill-design.md"
)


@pytest.fixture
def design_content() -> str:
    assert DESIGN_DOC.is_file(), f"Design doc missing: {DESIGN_DOC}"
    return DESIGN_DOC.read_text()


class TestDesignDocExists:
    def test_design_doc_file_exists(self) -> None:
        assert DESIGN_DOC.is_file(), (
            f"Expected design doc at {DESIGN_DOC}. "
            "Create docs/design/environment-setup-skill-design.md per issue #838."
        )


class TestDesignDocCompleteness:
    """Verify the design doc addresses all seven design requirements from #838."""

    def test_defines_env_mode_enum_values(self, design_content: str) -> None:
        for value in ("none", "docker", "micromamba-host", "unavailable"):
            assert value in design_content, f"Design doc must define env_mode value '{value}'"

    def test_specifies_structured_output_tokens(self, design_content: str) -> None:
        for token in ("env_mode", "env_report", "verdict"):
            assert token in design_content, (
                f"Design doc must specify structured output token '{token}'"
            )

    def test_specifies_order_up_sentinel(self, design_content: str) -> None:
        assert "%%ORDER_UP%%" in design_content

    def test_defines_verdict_routing(self, design_content: str) -> None:
        for verdict in ("PASS", "WARN", "FAIL"):
            assert verdict in design_content, (
                f"Design doc must define verdict routing for '{verdict}'"
            )

    def test_references_dockerfile_template(self, design_content: str) -> None:
        assert "Dockerfile.template" in design_content, (
            "Design doc must reference the canonical Dockerfile template"
        )

    def test_pins_base_image_version(self, design_content: str) -> None:
        assert "mambaorg/micromamba:1.0-bullseye-slim" in design_content, (
            "Design doc must pin the Dockerfile base image version"
        )

    def test_defines_recipe_step_yaml(self, design_content: str) -> None:
        assert "setup_environment" in design_content, (
            "Design doc must define the setup_environment recipe step"
        )

    def test_defines_viability_rules(self, design_content: str) -> None:
        content_lower = design_content.lower()
        assert "conda-forge" in content_lower, (
            "Design doc must discuss conda-forge channel viability"
        )
        assert "cuda" in content_lower, (
            "Design doc must discuss CUDA as a non-viable fallback case"
        )

    def test_defines_downstream_consumption(self, design_content: str) -> None:
        assert "run_experiment" in design_content or "run-experiment" in design_content, (
            "Design doc must explain how run-experiment consumes env_mode"
        )
        assert "implement_phase" in design_content or "implement-experiment" in design_content, (
            "Design doc must explain how implement-experiment consumes env_mode"
        )

    def test_skill_name_defined(self, design_content: str) -> None:
        assert "setup-environment" in design_content, (
            "Design doc must name the skill 'setup-environment'"
        )
