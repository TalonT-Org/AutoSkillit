"""
Contract conformance tests for planner-elaborate-wps skill registration.
"""

from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [
    pytest.mark.layer("planner"),
    pytest.mark.small,
    pytest.mark.feature("planner"),
]

SKILL_NAME = "planner-elaborate-wps"
CONTRACTS_PATH = (
    Path(__file__).parent.parent.parent / "src/autoskillit/recipe/skill_contracts.yaml"
)
SKILL_MD_PATH = (
    Path(__file__).parent.parent.parent / f"src/autoskillit/skills_extended/{SKILL_NAME}/SKILL.md"
)
ASSIGN_SKILL_MD_PATH = (
    Path(__file__).parent.parent.parent
    / "src/autoskillit/skills_extended/planner-elaborate-assignments/SKILL.md"
)


@pytest.fixture(scope="module")
def contracts() -> dict:
    return load_yaml(CONTRACTS_PATH)


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
        "Create src/autoskillit/skills_extended/planner-elaborate-wps/SKILL.md"
    )
    return SKILL_MD_PATH.read_text()


@pytest.fixture(scope="module")
def assign_skill_md() -> str:
    assert ASSIGN_SKILL_MD_PATH.exists(), (
        f"SKILL.md not found at {ASSIGN_SKILL_MD_PATH}. "
        "Create src/autoskillit/skills_extended/planner-elaborate-assignments/SKILL.md"
    )
    return ASSIGN_SKILL_MD_PATH.read_text()


class TestContractRegistration:
    def test_skill_entry_exists(self, skill_contract: dict) -> None:
        assert isinstance(skill_contract, dict), (
            f"{SKILL_NAME!r} contract entry must be a dict, got {type(skill_contract)}"
        )

    def test_write_behavior_always(self, skill_contract: dict) -> None:
        assert skill_contract.get("write_behavior") == "always", (
            "write_behavior must be 'always' for planner-elaborate-wps. "
            "Without it, a session that never writes is marked successful."
        )

    def test_phase_wps_result_dir_output_present(self, skill_contract: dict) -> None:
        outputs = skill_contract.get("outputs", [])
        names = [o.get("name") for o in outputs]
        assert "phase_wps_result_dir" in names, (
            f"phase_wps_result_dir not in outputs. Found: {names}"
        )

    def test_phase_wps_result_dir_type_is_directory_path(self, skill_contract: dict) -> None:
        outputs = skill_contract.get("outputs", [])
        token = next((o for o in outputs if o.get("name") == "phase_wps_result_dir"), None)
        assert token is not None
        assert token.get("type") == "directory_path", (
            f"phase_wps_result_dir type must be 'directory_path', got {token.get('type')!r}."
        )

    def test_expected_output_pattern_present(self, skill_contract: dict) -> None:
        patterns = skill_contract.get("expected_output_patterns", [])
        assert any("phase_wps_result_dir" in p for p in patterns), (
            f"No expected_output_pattern referencing phase_wps_result_dir. Found: {patterns}"
        )

    def test_two_inputs_declared(self, skill_contract: dict) -> None:
        inputs = skill_contract.get("inputs", [])
        assert len(inputs) == 2, (
            f"Expected exactly 2 inputs, got {len(inputs)}: {[i.get('name') for i in inputs]}"
        )
        input_names = {i.get("name") for i in inputs}
        assert input_names == {"context_file", "planner_dir"}, (
            f"Unexpected input names: {input_names}"
        )


class TestSkillMdPresence:
    def test_has_workflow_section(self, skill_md: str) -> None:
        assert "## Workflow" in skill_md, (
            "SKILL.md must contain a '## Workflow' section — "
            "file exists but appears empty or structurally incomplete."
        )

    def test_has_categories_planner(self, skill_md: str) -> None:
        assert "categories: [planner]" in skill_md, (
            "SKILL.md must include 'categories: [planner]' in frontmatter. "
            "Required for feature-gate and skill-category routing."
        )

    def test_has_never_always_block(self, skill_md: str) -> None:
        assert "NEVER" in skill_md and "ALWAYS" in skill_md, (
            "SKILL.md must include a NEVER/ALWAYS constraint block following "
            "the pattern of all other planner skills."
        )

    def test_has_write_path_restriction(self, skill_md: str) -> None:
        assert "AUTOSKILLIT_TEMP" in skill_md and "planner/" in skill_md, (
            "SKILL.md NEVER block must restrict write paths to {{AUTOSKILLIT_TEMP}}/planner/."
        )

    def test_has_output_token(self, skill_md: str) -> None:
        assert "phase_wps_result_dir" in skill_md, (
            "SKILL.md must document 'phase_wps_result_dir = <path>' output token."
        )

    def test_has_l0_response_fields(self, skill_md: str) -> None:
        for field in [
            "id",
            "name",
            "goal",
            "summary",
            "technical_steps",
            "files_touched",
            "apis_defined",
            "apis_consumed",
            "depends_on",
            "deliverables",
            "acceptance_criteria",
        ]:
            assert field in skill_md, (
                f"SKILL.md must document L0 structured response field: {field}."
            )

    def test_has_batch_ceiling(self, skill_md: str) -> None:
        assert "batches of 6" in skill_md, (
            "SKILL.md must specify the maximum parallel L0 batch size of 6."
        )

    def test_has_partial_failure_handling(self, skill_md: str) -> None:
        assert "CRITICAL" in skill_md or "partial" in skill_md.lower(), (
            "SKILL.md must describe partial-failure handling — "
            "proceed with N-1 WPs and log CRITICAL if an L0 fails."
        )

    def test_has_wp_sentinels(self, skill_md: str) -> None:
        assert "wp_sentinels" in skill_md, (
            "SKILL.md must document the sentinel directory naming convention (wp_sentinels)."
        )

    def test_skill_md_contains_wp_id_format_constraint(self, skill_md: str) -> None:
        assert "P{N}-A{N}-WP{N}" in skill_md, (
            "SKILL.md must specify the WP ID format pattern P{N}-A{N}-WP{N}."
        )
        assert "numeric" in skill_md.lower(), (
            "SKILL.md must specify that IDs must be numeric only."
        )

    def test_skill_md_contains_assignment_id_format_constraint(self, assign_skill_md: str) -> None:
        assert "P{N}-A{N}" in assign_skill_md, (
            "planner-elaborate-assignments/SKILL.md must specify "
            "the Assignment ID format pattern P{N}-A{N}."
        )

    def test_l0_prompt_template_contains_write_prohibition(self, skill_md: str) -> None:
        lower = skill_md.lower()
        has_prohibition = (
            "do not use write" in lower
            or "do not use the write" in lower
            or "read-only analysis agent" in lower
            or "never:\n- use the write" in lower
            or "must never:\n- use the write" in lower
        )
        assert has_prohibition, (
            "planner-elaborate-wps/SKILL.md must include an explicit write prohibition "
            "in the L0 prompt template (Component 0). Expected text like "
            "'You are a READ-ONLY analysis agent' or 'Do NOT use Write, Edit, or Bash'."
        )

    def test_wp_elaborator_has_inseparability_rules(self) -> None:
        """wp-elaborator.md agent definition must include inseparability rules."""
        agent_path = (
            Path(__file__).parent.parent.parent / "src/autoskillit/agents/wp-elaborator.md"
        )
        assert agent_path.exists(), f"wp-elaborator.md not found at {agent_path}"
        content = agent_path.read_text()
        assert "Inseparability" in content, (
            "wp-elaborator.md must include an 'Inseparability Rules' section "
            "preventing impl/test WP splits."
        )

    def test_l0_prompt_template_contains_json_only_framing(self, skill_md: str) -> None:
        lower = skill_md.lower()
        has_json_only = (
            "return your analysis as json" in lower
            or "return findings as json only" in lower
            or "json between triple-backtick json fences only" in lower
            or ("json" in lower and "only" in lower and "return" in lower)
        )
        assert has_json_only, (
            "planner-elaborate-wps/SKILL.md must specify in the L0 prompt template "
            "that the L0 must return JSON only. Expected text like "
            "'Return your analysis as JSON... ONLY'."
        )
