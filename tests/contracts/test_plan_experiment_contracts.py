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
