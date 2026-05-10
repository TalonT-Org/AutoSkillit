"""Contract tests for plan-experiment SKILL.md — data provenance lifecycle."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "plan-experiment"
    / "SKILL.md"
)


def test_data_manifest_in_frontmatter_schema() -> None:
    text = SKILL_PATH.read_text()
    assert "data_manifest" in text


def test_data_manifest_required_fields() -> None:
    text = SKILL_PATH.read_text()
    after_manifest = text.lower().split("data_manifest")[1][:2000]
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
    """All 12 experiment types from the registry appear in the template enum."""
    import re

    EXPERIMENT_TYPES_DIR = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "recipes"
        / "experiment-types"
    )
    registry_types = {p.stem for p in EXPERIMENT_TYPES_DIR.glob("*.yaml")}
    text = SKILL_PATH.read_text()
    m = re.search(r"experiment_type:\s*\{one of:\s*([^}]+)\}", text, re.IGNORECASE)
    assert m, "experiment_type enum not found in SKILL.md template"
    enum_str = m.group(1)
    enum_types = {t.strip() for t in enum_str.split(",")}
    missing = registry_types - enum_types
    assert not missing, f"experiment_type enum missing registry types: {missing}"


def test_source_type_enum_includes_literature_and_database() -> None:
    """source_type enum includes literature and database values."""
    import re

    text = SKILL_PATH.read_text()
    m = re.search(r"source_type:\s*synthetic\s*#\s*([^\n]+)", text, re.IGNORECASE)
    assert m, "source_type enum comment not found in SKILL.md"
    comment = m.group(1)
    assert "literature" in comment, "source_type enum missing 'literature'"
    assert "database" in comment, "source_type enum missing 'database'"


def test_field_requirements_table_covers_all_registry_types() -> None:
    """Field requirements table has a column for every registry experiment type."""
    import re

    EXPERIMENT_TYPES_DIR = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "recipes"
        / "experiment-types"
    )
    registry_types = {p.stem for p in EXPERIMENT_TYPES_DIR.glob("*.yaml")}
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
    import re

    text = SKILL_PATH.read_text()
    m = re.search(r"V3:\s*([^\n]+)", text)
    assert m, "V3 rule not found in SKILL.md"
    v3_line = m.group(1)
    assert "qualitative" in v3_line.lower(), (
        "V3 rule must exempt qualitative_interpretive from statistical_plan requirement"
    )


def test_v9_mentions_literature_and_database_source_types() -> None:
    """V9 rule text mentions both literature and database source types."""
    import re

    text = SKILL_PATH.read_text()
    m = re.search(r"V9:.+?(?=\nV[0-9]+:|\n---|\Z)", text, re.DOTALL)
    assert m, "V9 rule not found in SKILL.md"
    v9_block = m.group(0)
    assert "literature" in v9_block.lower(), "V9 must mention literature source type"
    assert "database" in v9_block.lower(), "V9 must mention database source type"
