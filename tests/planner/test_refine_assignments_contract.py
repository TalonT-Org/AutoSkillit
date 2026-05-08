"""
Contract conformance tests for planner-refine-assignments skill registration.
"""

from pathlib import Path

import pytest
import yaml

pytestmark = [
    pytest.mark.layer("planner"),
    pytest.mark.small,
    pytest.mark.feature("planner"),
]

SKILL_NAME = "planner-refine-assignments"
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
        "Create src/autoskillit/skills_extended/planner-refine-assignments/SKILL.md"
    )
    return SKILL_MD_PATH.read_text()


class TestContractRegistration:
    def test_skill_entry_exists(self, skill_contract: dict) -> None:
        """skill_contracts.yaml must have an entry for planner-refine-assignments."""
        assert isinstance(skill_contract, dict), (
            f"{SKILL_NAME!r} contract entry must be a dict, got {type(skill_contract)}"
        )

    def test_write_behavior_always(self, skill_contract: dict) -> None:
        """write_behavior must be 'always' — prevents silent no-write success."""
        assert skill_contract.get("write_behavior") == "always", (
            "write_behavior must be 'always' for planner-refine-assignments. "
            "Without it, a session that never writes is marked successful."
        )

    def test_phase_refined_path_output_present(self, skill_contract: dict) -> None:
        """outputs must declare phase_refined_path."""
        outputs = skill_contract.get("outputs", [])
        names = [o.get("name") for o in outputs]
        assert "phase_refined_path" in names, f"phase_refined_path not in outputs. Found: {names}"

    def test_phase_refined_path_type_is_file_path(self, skill_contract: dict) -> None:
        """phase_refined_path output must have type file_path."""
        outputs = skill_contract.get("outputs", [])
        token = next((o for o in outputs if o.get("name") == "phase_refined_path"), None)
        assert token is not None
        assert token.get("type") == "file_path", (
            f"phase_refined_path type must be 'file_path', got {token.get('type')!r}. "
            "Path-contamination detection requires file_path type."
        )

    def test_expected_output_pattern_present(self, skill_contract: dict) -> None:
        """expected_output_patterns must include a pattern matching phase_refined_path."""
        patterns = skill_contract.get("expected_output_patterns", [])
        assert any("phase_refined_path" in p for p in patterns), (
            f"No expected_output_pattern referencing phase_refined_path. Found: {patterns}"
        )

    def test_three_inputs_declared(self, skill_contract: dict) -> None:
        """Three inputs: context_file, refined_plan_path, output_dir."""
        inputs = skill_contract.get("inputs", [])
        assert len(inputs) == 3, (
            f"Expected exactly 3 inputs, got {len(inputs)}: {[i.get('name') for i in inputs]}"
        )
        input_names = {i.get("name") for i in inputs}
        assert input_names == {"context_file", "refined_plan_path", "output_dir"}, (
            f"Unexpected input names: {input_names}"
        )


class TestSkillMdPresence:
    def test_skill_md_exists(self, skill_md: str) -> None:
        """SKILL.md must exist under skills_extended/planner-refine-assignments/."""
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

    def test_skill_contains_phase_refined_path(self, skill_md: str) -> None:
        """SKILL.md must document the phase_refined_path output token."""
        assert "phase_refined_path" in skill_md, (
            "SKILL.md must document 'phase_refined_path = <path>' output token."
        )

    def test_skill_contains_peer_summaries(self, skill_md: str) -> None:
        """SKILL.md must reference peer_summaries from the context file."""
        assert "peer_summaries" in skill_md, (
            "SKILL.md must reference 'peer_summaries' — the per-phase context file provides "
            "id/name/goal stubs for all assignments in other phases."
        )

    def test_skill_md_has_l0_response_fields(self, skill_md: str) -> None:
        """SKILL.md must describe L0 response fields for assignments."""
        assert "assignment_id" in skill_md, (
            "SKILL.md must document L0 structured response field: assignment_id."
        )
        assert "changes" in skill_md, (
            "SKILL.md must document L0 structured response field: changes."
        )
        assert "dependency_corrections" in skill_md, (
            "SKILL.md must document L0 structured response field: dependency_corrections."
        )
        assert "wp_proposal_adjustments" in skill_md, (
            "SKILL.md must document L0 structured response field: wp_proposal_adjustments."
        )

    def test_skill_md_has_batch_limit_spec(self, skill_md: str) -> None:
        """SKILL.md must specify the L0 batch ceiling of 6."""
        assert "Spawn more than 6" in skill_md, (
            "SKILL.md must specify the maximum parallel L0 batch size of 6 "
            "(expected literal phrase 'Spawn more than 6')."
        )

    def test_skill_md_has_wp_conflict_policy(self, skill_md: str) -> None:
        """SKILL.md must document the WP conflict resolution policy (earlier assignment wins)."""
        assert "earlier" in skill_md.lower(), (
            "SKILL.md must document that the earlier assignment_id wins WP ownership conflicts."
        )

    def test_skill_md_has_partial_failure_handling(self, skill_md: str) -> None:
        """SKILL.md must describe partial-failure behavior (N-1 assignments on L0 failure)."""
        assert "CRITICAL" in skill_md or "partial" in skill_md.lower(), (
            "SKILL.md must describe partial-failure handling — "
            "proceed with N-1 suggestions and log CRITICAL if an L0 fails."
        )
