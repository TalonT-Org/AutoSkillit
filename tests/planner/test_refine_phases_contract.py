"""
Contract conformance tests for planner-refine-phases skill registration.
"""

from pathlib import Path

import pytest
import yaml

pytestmark = [
    pytest.mark.layer("planner"),
    pytest.mark.small,
    pytest.mark.feature("planner"),
]

SKILL_NAME = "planner-refine-phases"
CONTRACTS_PATH = (
    Path(__file__).parent.parent.parent / "src/autoskillit/recipe/skill_contracts.yaml"
)
SKILL_MD_PATH = (
    Path(__file__).parent.parent.parent / f"src/autoskillit/skills_extended/{SKILL_NAME}/SKILL.md"
)


@pytest.fixture(scope="module")
def contracts() -> dict:
    return yaml.safe_load(CONTRACTS_PATH.read_text())


@pytest.fixture(scope="module")
def skill_contract(contracts: dict) -> dict:
    skills = contracts.get("skills", {})
    assert SKILL_NAME in skills, (
        f"{SKILL_NAME!r} not found in skill_contracts.yaml. "
        f"Known planner skills: {[k for k in skills if 'planner' in k]}"
    )
    return skills[SKILL_NAME]


@pytest.fixture(scope="module")
def skill_md() -> str:
    assert SKILL_MD_PATH.exists(), (
        f"SKILL.md not found at {SKILL_MD_PATH}. "
        "Create src/autoskillit/skills_extended/planner-refine-phases/SKILL.md"
    )
    return SKILL_MD_PATH.read_text()


class TestContractRegistration:
    def test_skill_entry_exists(self, skill_contract: dict) -> None:
        """skill_contracts.yaml must have an entry for planner-refine-phases."""
        assert isinstance(skill_contract, dict), (
            f"{SKILL_NAME!r} contract entry must be a dict, got {type(skill_contract)}"
        )

    def test_write_behavior_always(self, skill_contract: dict) -> None:
        """write_behavior must be 'always' — prevents silent no-write success."""
        assert skill_contract.get("write_behavior") == "always", (
            "write_behavior must be 'always' for planner-refine-phases. "
            "Without it, a session that never writes is marked successful."
        )

    def test_refined_plan_path_output_present(self, skill_contract: dict) -> None:
        """outputs must declare refined_plan_path."""
        outputs = skill_contract.get("outputs", [])
        names = [o.get("name") for o in outputs]
        assert "refined_plan_path" in names, f"refined_plan_path not in outputs. Found: {names}"

    def test_refined_plan_path_type_is_file_path(self, skill_contract: dict) -> None:
        """refined_plan_path output must have type file_path."""
        outputs = skill_contract.get("outputs", [])
        token = next((o for o in outputs if o.get("name") == "refined_plan_path"), None)
        assert token is not None
        assert token.get("type") == "file_path", (
            f"refined_plan_path type must be 'file_path', got {token.get('type')!r}. "
            "Path-contamination detection requires file_path type."
        )

    def test_expected_output_pattern_present(self, skill_contract: dict) -> None:
        """expected_output_patterns must include a pattern matching refined_plan_path."""
        patterns = skill_contract.get("expected_output_patterns", [])
        assert any("refined_plan_path" in p for p in patterns), (
            f"No expected_output_pattern referencing refined_plan_path. Found: {patterns}"
        )


class TestSkillMdPresence:
    def test_skill_md_exists(self, skill_md: str) -> None:
        """SKILL.md must exist under skills_extended/planner-refine-phases/."""
        assert "## Workflow" in skill_md, (
            "SKILL.md must contain a '## Workflow' section — "
            "file exists but appears empty or structurally incomplete."
        )

    def test_skill_md_has_categories_planner(self, skill_md: str) -> None:
        """Frontmatter must declare categories: [planner]."""
        assert "categories: [planner]" in skill_md, (
            "SKILL.md must include 'categories: [planner]' in frontmatter. "
            "Required for feature-gate and skill-category routing."
        )

    def test_skill_md_has_never_always_block(self, skill_md: str) -> None:
        """SKILL.md must contain a NEVER/ALWAYS constraint block."""
        assert "NEVER" in skill_md and "ALWAYS" in skill_md, (
            "SKILL.md must include a NEVER/ALWAYS constraint block following "
            "the pattern of all other planner skills."
        )

    def test_skill_md_has_write_path_restriction(self, skill_md: str) -> None:
        """SKILL.md NEVER block must restrict writes to AUTOSKILLIT_TEMP/planner/."""
        assert "AUTOSKILLIT_TEMP" in skill_md and "planner/" in skill_md, (
            "SKILL.md NEVER block must restrict write paths to {{AUTOSKILLIT_TEMP}}/planner/."
        )

    def test_skill_md_has_output_token(self, skill_md: str) -> None:
        """SKILL.md must document the refined_plan_path output token."""
        assert "refined_plan_path" in skill_md, (
            "SKILL.md must document 'refined_plan_path = <path>' output token."
        )

    def test_skill_md_has_l0_validation_instructions(self, skill_md: str) -> None:
        """SKILL.md must describe L0 response validation (phase_id, changes, conflicts)."""
        assert "phase_id" in skill_md and "changes" in skill_md and "conflicts" in skill_md, (
            "SKILL.md must document L0 structured response fields: phase_id, changes, conflicts."
        )

    def test_skill_md_has_partial_failure_handling(self, skill_md: str) -> None:
        """SKILL.md must describe partial-failure behavior (N-1 phases on L0 failure)."""
        assert "CRITICAL" in skill_md or "partial" in skill_md.lower(), (
            "SKILL.md must describe partial-failure handling — "
            "proceed with N-1 suggestions and log CRITICAL if an L0 fails."
        )
