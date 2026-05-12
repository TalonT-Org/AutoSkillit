"""Contract tests for plan-experiment SKILL.md — data provenance lifecycle."""

import re
from pathlib import Path

from autoskillit.recipe._skill_placeholder_parser import extract_validation_rule_block

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "plan-experiment"
    / "SKILL.md"
)
EXPERIMENT_TYPES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "recipes"
    / "experiment-types"
)
REVIEW_DESIGN_SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-design"
    / "SKILL.md"
)


def test_data_manifest_in_frontmatter_schema() -> None:
    text = SKILL_PATH.read_text()
    assert "data_manifest" in text


def test_data_manifest_required_fields() -> None:
    text = SKILL_PATH.read_text()
    parts = text.lower().split("### data_manifest")
    assert len(parts) > 1, "### data_manifest heading not found in SKILL.md"
    after_manifest = parts[1][:2000]
    for field in ("source_type", "acquisition", "verification", "hypothesis"):
        assert field in after_manifest, f"data_manifest missing field: {field}"


def test_directive_data_acquisition_requirement() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "directive" in lower
    assert "acquisition" in lower


def test_plan_experiment_includes_tests_directory_in_layout() -> None:
    """plan-experiment/SKILL.md Experiment Directory Layout must include a tests/ folder."""
    content = SKILL_PATH.read_text()
    # The directory layout template must show a tests/ directory
    assert "tests/" in content, (
        "plan-experiment/SKILL.md Experiment Directory Layout must include a tests/ "
        "subfolder so the agent knows to plan test infrastructure"
    )


def test_plan_experiment_has_test_infrastructure_phase() -> None:
    """plan-experiment/SKILL.md Implementation Phases must include a test infrastructure phase."""
    content = SKILL_PATH.read_text()
    lower = content.lower()
    assert "test infrastructure" in lower, (
        "plan-experiment/SKILL.md must include a 'Test Infrastructure' phase in the "
        "Implementation Phases section so agents plan test creation alongside scripts"
    )


def test_plan_experiment_environment_mentions_pytest() -> None:
    """plan-experiment/SKILL.md must mention pytest in the environment specification."""
    content = SKILL_PATH.read_text()
    assert "pytest" in content, (
        "plan-experiment/SKILL.md must reference pytest in the environment section so "
        "agents know to include it in environment.yml for test runner availability"
    )


def test_plan_experiment_layout_includes_dockerfile() -> None:
    """plan-experiment/SKILL.md Experiment Directory Layout must include Dockerfile."""
    content = SKILL_PATH.read_text()
    assert "Dockerfile" in content, (
        "plan-experiment/SKILL.md Experiment Directory Layout must include a Dockerfile "
        "so the agent plans Docker container build as part of the experiment"
    )


def test_plan_experiment_layout_includes_taskfile() -> None:
    """plan-experiment/SKILL.md Experiment Directory Layout must include Taskfile.yml."""
    content = SKILL_PATH.read_text()
    assert "Taskfile.yml" in content, (
        "plan-experiment/SKILL.md Experiment Directory Layout must include Taskfile.yml "
        "with standardized build-env / run-experiment / test tasks"
    )


def test_experiment_type_enum_lists_all_registry_types() -> None:
    """All experiment types from the registry appear in the template enum."""

    registry_types = {p.stem for p in EXPERIMENT_TYPES_DIR.glob("*.yaml")}
    assert len(registry_types) > 0, "experiment-types registry dir is empty or missing"
    text = SKILL_PATH.read_text()
    m = re.search(r"experiment_type:\s*\{one of:\s*([^}]+)\}", text, re.IGNORECASE)
    assert m, "experiment_type enum not found in SKILL.md template"
    enum_str = m.group(1)
    enum_types = {t.strip() for t in enum_str.split(",")}
    missing = registry_types - enum_types
    assert not missing, f"experiment_type enum missing registry types: {missing}"


def test_source_type_enum_includes_literature_and_database() -> None:
    """source_type enum includes literature and database values."""

    text = SKILL_PATH.read_text()
    m = re.search(r"source_type:\s*synthetic\s*#\s*([^\n]+)", text, re.IGNORECASE)
    assert m, "source_type enum comment not found in SKILL.md"
    comment = m.group(1)
    assert "literature" in comment, "source_type enum missing 'literature'"
    assert "database" in comment, "source_type enum missing 'database'"


def test_field_requirements_table_covers_all_registry_types() -> None:
    """Field requirements table has a column for every registry experiment type."""

    registry_types = {p.stem for p in EXPERIMENT_TYPES_DIR.glob("*.yaml")}
    assert len(registry_types) > 0, "experiment-types registry dir is empty or missing"
    text = SKILL_PATH.read_text()
    m = re.search(
        r"Field requirements by experiment type:(.+?)(?:\n\n|\Z)", text, re.DOTALL | re.IGNORECASE
    )
    assert m, "Field requirements table not found in SKILL.md"
    table_block = m.group(1)
    header_line = table_block.split("\n")[1]
    col_tokens = [t.strip().rstrip("|") for t in header_line.split("|")]
    col_tokens = [t for t in col_tokens if t]
    abbrevs = {
        "causal_inference": "causal_inf",
        "configuration_study": "config_study",
        "evidence_synthesis": "evid_synth",
        "factorial_design": "fact_design",
        "instrument_validation": "instr_valid",
        "observational_correlational": "obs_corr",
        "qualitative_interpretive": "qual_interp",
        "robustness_audit": "robust_audit",
        "simulation_modeling": "sim_model",
        "single_subject": "single_subj",
    }
    covered = set(col_tokens[1:])
    missing = []
    for rtype in registry_types:
        abbrev = abbrevs.get(rtype, rtype)
        if abbrev not in covered:
            missing.append(rtype)
    assert not missing, f"Field requirements table missing columns for: {missing}"


def test_v3_exempts_qualitative_interpretive() -> None:
    """V3 exempts qualitative_interpretive from statistical_plan requirement."""

    text = SKILL_PATH.read_text()
    m = re.search(r"V3:\s*([^\n]+)", text)
    assert m, "V3 rule not found in SKILL.md"
    v3_line = m.group(1)
    assert "qualitative" in v3_line.lower(), (
        "V3 rule must exempt qualitative_interpretive from statistical_plan requirement"
    )


def test_v9_mentions_literature_and_database_source_types() -> None:
    """V9 rule text mentions both literature and database source types."""

    text = SKILL_PATH.read_text()
    v9_block = extract_validation_rule_block(text, "V9")
    assert v9_block is not None, "V9 rule not found in SKILL.md"
    assert "literature" in v9_block.lower(), "V9 must mention literature source type"
    assert "database" in v9_block.lower(), "V9 must mention database source type"


def test_subagent_b_requires_accession_verification() -> None:
    """Subagent B instructions must mandate web-search verification of database accessions."""
    import re

    text = SKILL_PATH.read_text()
    m = re.search(
        r"Subagent B.*?(?=\*\*Subagent C|\*\*Additional subagents)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    assert m, "Subagent B section not found in SKILL.md"
    subagent_b = m.group(0).lower()
    assert "web" in subagent_b, (
        "Subagent B must mention web-based verification of database accessions"
    )
    assert "search" in subagent_b or "fetch" in subagent_b, (
        "Subagent B must mandate web search or web fetch to verify database accessions "
        "before including them in data_manifest"
    )
    assert "accession" in subagent_b or "identifier" in subagent_b, (
        "Subagent B must reference accession or identifier verification"
    )


def test_v10_semantic_verification_rule_exists() -> None:
    """V10 rule must enforce semantic verification of database/literature accessions."""
    import re

    text = SKILL_PATH.read_text()
    m = re.search(r"V10:.+?(?=\nV[0-9]+:|\n---|\n```|\Z)", text, re.DOTALL)
    assert m, (
        "V10 semantic verification rule not found in SKILL.md — "
        "add a V10 rule requiring web-search confirmation of database/literature accessions"
    )
    v10_block = m.group(0).lower()
    assert "verification" in v10_block or "verified" in v10_block or "confirm" in v10_block, (
        "V10 must reference verification or confirmation of accessions"
    )
    assert "database" in v10_block, "V10 must cover database source_type entries"


def test_additional_subagents_includes_accession_verification() -> None:
    """Additional subagents section must list accession/citation verification as a category."""
    import re

    text = SKILL_PATH.read_text()
    m = re.search(
        r"\*\*Additional subagents.*?(?=\n\*\*Breadth enforcement|\n###|## )",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    assert m, "Additional subagents section not found in SKILL.md"
    section = m.group(0).lower()
    assert "accession" in section or "citation" in section or "identifier" in section, (
        "Additional subagents must list accession/citation/identifier verification "
        "as a sanctioned web-search category"
    )


def test_anti_fabrication_covers_accession_identifiers() -> None:
    """NEVER block must explicitly cover fabrication of database accession identifiers."""
    import re

    text = SKILL_PATH.read_text()
    m = re.search(r"\*\*NEVER:\*\*.*?(?=\*\*ALWAYS:\*\*)", text, re.DOTALL)
    assert m, "NEVER block not found in SKILL.md"
    never_block = m.group(0).lower()
    assert "accession" in never_block or "identifier" in never_block, (
        "NEVER block must explicitly prohibit fabrication of database accession identifiers "
        "or external dataset identifiers from training knowledge"
    )


def test_data_manifest_verification_url_field() -> None:
    """data_manifest field definitions must include a verification_url field."""
    text = SKILL_PATH.read_text()
    parts = text.lower().split("### data_manifest")
    assert len(parts) > 1, "### data_manifest heading not found in SKILL.md"
    after_manifest = parts[1][:3000]
    assert "verification_url" in after_manifest, (
        "data_manifest section must include a verification_url field "
        "to record the URL used to confirm accession existence"
    )


def test_v9_rejects_unresolved_template_placeholders() -> None:
    """V9 must contain language rejecting {variable} template syntax in acquisition fields.

    V9 must go beyond presence-only checks (non-null fields) and enforce content
    validity. The error template ``{specific missing field or hypothesis}`` in V9 is
    an authoring-time placeholder for the LLM, not a validation criterion about
    runtime placeholders. This test asserts that V9 explicitly addresses unresolved
    template placeholders as a rejection criterion.
    """

    text = SKILL_PATH.read_text()
    v9_block = extract_validation_rule_block(text, "V9")
    assert v9_block is not None, "V9 rule block not found"
    v9 = v9_block.lower()
    placeholder_signals = ["placeholder", "template", "unresolved", "brace expansion"]
    assert any(s in v9 for s in placeholder_signals), (
        "V9 must specify rejection of unresolved template/placeholder syntax in acquisition fields"
    )
    reject_signals = ["must not contain", "not permitted", "reject", "must be"]
    assert any(s in v9 for s in reject_signals), (
        "V9 must specify a prohibition for placeholder syntax, not just mention it"
    )


def test_v9_requires_gitignored_acquisition_command() -> None:
    """V9 must require gitignored entries to have an executable acquisition command.

    V9 must require gitignored entries to have an acquisition command — not just a
    non-null `location` — since download-data executes `acquisition` for gitignored
    entries identically to external entries. This test asserts that V9 has a dedicated
    bullet/clause requiring gitignored entries to have an acquisition command,
    separate from the external-only clause.
    """

    text = SKILL_PATH.read_text()
    v9_block = extract_validation_rule_block(text, "V9")
    assert v9_block is not None, "V9 rule block not found"
    v9 = v9_block.lower()
    assert "gitignored" in v9, "V9 must address source_type: gitignored entries"
    lines = v9.split("\n")
    has_gitignored_acquisition_line = any(
        "gitignored" in line
        and ("acquisition" in line or "generation" in line)
        and "location" not in line
        for line in lines
    )
    assert has_gitignored_acquisition_line, (
        "V9 must have a dedicated clause requiring gitignored entries to have an "
        "executable acquisition or generation command (not just a non-null location)"
    )


def test_v9_and_data_acquisition_source_type_consistency() -> None:
    """V9 and data_acquisition must address the same source_type variants."""
    pe_text = SKILL_PATH.read_text()
    rd_text = REVIEW_DESIGN_SKILL_PATH.read_text()

    v9_block = extract_validation_rule_block(pe_text, "V9")
    assert v9_block is not None, "V9 block not found in plan-experiment"
    v9 = v9_block.lower()

    da_start = rd_text.lower().find("data_acquisition")
    assert da_start != -1, "data_acquisition not found in review-design"
    da = rd_text[da_start : da_start + 3000].lower()

    for source_type in ("external", "gitignored"):
        assert source_type in v9, f"V9 must address source_type: {source_type}"
        assert source_type in da, f"data_acquisition must address source_type: {source_type}"

    placeholder_signals = ["placeholder", "template", "unresolved", "{"]
    assert any(s in v9 for s in placeholder_signals), (
        "V9 must mention template/placeholder validation"
    )
    assert any(s in da for s in placeholder_signals), (
        "data_acquisition must mention template/placeholder validation"
    )


def test_always_block_references_ten_validation_rules() -> None:
    """ALWAYS block must reference all 10 validation rules."""
    import re

    text = SKILL_PATH.read_text()
    m = re.search(r"\*\*ALWAYS:\*\*\n(.+?)(?=\n## |\Z)", text, re.DOTALL)
    assert m, "ALWAYS block not found in SKILL.md"
    always_block = m.group(1).lower()
    assert "10 validation rules" in always_block or "all 10 validation" in always_block, (
        "ALWAYS block must reference '10 validation rules'"
    )


def test_v10_citation_verification_rule() -> None:
    """V10 rule must exist and mention core citation verification concepts."""
    text = SKILL_PATH.read_text()
    m = re.search(r"V10:.+?(?=\nV[0-9]+:|\n---|\Z)", text, re.DOTALL)
    assert m, "V10 rule not found in SKILL.md"
    v10_block = m.group(0).lower()
    assert "accession" in v10_block, "V10 must mention accession"
    assert "citation" in v10_block or "cited" in v10_block, "V10 must mention citation"
    assert "http" in v10_block or "url" in v10_block, "V10 must mention http or url"
    assert "author" in v10_block, "V10 must mention author"
    assert "error" in v10_block, "V10 is ERROR-level and must contain the word error"


def test_v10_is_error_level() -> None:
    """V10 must be classified as ERROR-level in the ERRORs summary."""
    text = SKILL_PATH.read_text()
    errors_line_match = re.search(r"^- ERRORs?\s*\(([^)]+)\)", text, re.MULTILINE)
    assert errors_line_match, "ERRORs classification summary line not found in SKILL.md"
    errors_text = errors_line_match.group(1)
    assert "V10" in errors_text, "V10 must be listed as ERROR-level"


def test_citation_verification_agents_defined() -> None:
    """Citation Indexer, Link Validator, and Cross-Check Adversary must be defined."""
    text = SKILL_PATH.read_text().lower()
    assert "citation indexer" in text or "citation_indexer" in text, (
        "Citation Indexer subagent not defined"
    )
    assert "link validator" in text or "link_validator" in text, (
        "Link Validator subagent not defined"
    )
    assert "cross-check adversary" in text or "cross_check_adversary" in text, (
        "Cross-Check Adversary subagent not defined"
    )


def test_v10_network_failure_is_warning_not_error() -> None:
    """V10 must handle network failures as WARNING with verification: unverified."""
    text = SKILL_PATH.read_text()
    m = re.search(r"V10:.+?(?=\nV[0-9]+:|\n---|\Z)", text, re.DOTALL)
    assert m, "V10 rule not found in SKILL.md"
    v10_block = m.group(0).lower()
    assert "warning" in v10_block, "V10 must handle network failure as warning"
    assert "network" in v10_block or "unreachable" in v10_block or "unverified" in v10_block, (
        "V10 must mention network failure or unverified status"
    )


def test_v1_requires_baselines_for_applicable_types() -> None:
    """V1 must require baselines for applicable experiment types."""
    text = SKILL_PATH.read_text()
    m = re.search(r"V1:.+?(?=\nV[0-9]+:|\n---|\Z)", text, re.DOTALL)
    assert m, "V1 rule not found in SKILL.md"
    v1_block = m.group(0).lower()
    assert "baseline" in v1_block, "V1 must require baselines"


def test_v2_requires_estimand_contrast() -> None:
    """V2 must require estimand contrast for applicable experiment types."""
    text = SKILL_PATH.read_text()
    m = re.search(r"V2:.+?(?=\nV[0-9]+:|\n---|\Z)", text, re.DOTALL)
    assert m, "V2 rule not found in SKILL.md"
    v2_block = m.group(0).lower()
    assert "estimand" in v2_block or "contrast" in v2_block, "V2 must require estimand or contrast"


def test_v4_requires_spec_path_for_custom_env() -> None:
    """V4 must require spec_path for custom environment type."""
    text = SKILL_PATH.read_text()
    m = re.search(r"V4:.+?(?=\nV[0-9]+:|\n---|\Z)", text, re.DOTALL)
    assert m, "V4 rule not found in SKILL.md"
    v4_block = m.group(0).lower()
    assert "spec_path" in v4_block, "V4 must require spec_path"
