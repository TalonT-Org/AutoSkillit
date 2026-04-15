"""Contract tests for implement-experiment SKILL.md — test infrastructure requirements."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "implement-experiment"
    / "SKILL.md"
)


def test_implement_experiment_always_includes_test_creation() -> None:
    """implement-experiment ALWAYS list must include a directive to write test files."""
    text = SKILL_PATH.read_text()
    always_section = text.split("**ALWAYS:**")[1].split("##")[0] if "**ALWAYS:**" in text else text
    assert "tests/test_" in always_section, (
        "implement-experiment/SKILL.md ALWAYS list must direct the agent to write "
        "tests/test_{name}.py files alongside experiment scripts"
    )


def test_implement_experiment_step4_mentions_test_files() -> None:
    """implement-experiment Step 4 must mention creating tests/test_ files."""
    text = SKILL_PATH.read_text()
    step4_section = (
        text.split("### Step 4")[1].split("### Step 5")[0]
        if "### Step 4" in text and "### Step 5" in text
        else text
    )
    assert "tests/test_" in step4_section, (
        "implement-experiment/SKILL.md Step 4 must reference creating tests/test_{name}.py "
        "files alongside each experiment script"
    )


def test_implement_experiment_allows_pytest_collect_only() -> None:
    """implement-experiment must allow running pytest --collect-only as a verification step."""
    text = SKILL_PATH.read_text()
    assert "collect-only" in text or "collect_only" in text, (
        "implement-experiment/SKILL.md must include 'pytest --collect-only' as a "
        "test discovery verification step (distinct from running the full test suite)"
    )


def test_implement_experiment_step3_references_docker_build() -> None:
    """Step 3 must instruct the agent to build a Docker image."""
    text = SKILL_PATH.read_text()
    step3_section = (
        text.split("### Step 3")[1].split("### Step 4")[0]
        if "### Step 3" in text and "### Step 4" in text
        else text
    )
    assert "docker build" in step3_section.lower(), (
        "implement-experiment/SKILL.md Step 3 must reference 'docker build' — "
        "Docker is the primary isolation mechanism for research execution"
    )


def test_implement_experiment_step3_references_dockerfile() -> None:
    """Step 3 must instruct the agent to write a Dockerfile."""
    text = SKILL_PATH.read_text()
    step3_section = (
        text.split("### Step 3")[1].split("### Step 4")[0]
        if "### Step 3" in text and "### Step 4" in text
        else text
    )
    assert "Dockerfile" in step3_section, (
        "implement-experiment/SKILL.md Step 3 must reference writing a Dockerfile "
        "so the container image is built from the experiment's environment.yml"
    )


def test_implement_experiment_step3_references_taskfile() -> None:
    """Step 3 must instruct the agent to write a Taskfile.yml with build-env task."""
    text = SKILL_PATH.read_text()
    step3_section = (
        text.split("### Step 3")[1].split("### Step 4")[0]
        if "### Step 3" in text and "### Step 4" in text
        else text
    )
    assert "Taskfile.yml" in step3_section or "Taskfile" in step3_section, (
        "implement-experiment/SKILL.md Step 3 must reference writing a Taskfile.yml "
        "with standardized build-env / run-experiment / test tasks"
    )


def test_implement_experiment_step6_conditional_precommit() -> None:
    """Step 6 pre-commit check must be conditional on .pre-commit-config.yaml existence."""
    text = SKILL_PATH.read_text()
    step6_section = (
        text.split("### Step 6")[1].split("### Step 7")[0]
        if "### Step 6" in text and "### Step 7" in text
        else text
    )
    assert ".pre-commit-config.yaml" in step6_section, (
        "implement-experiment/SKILL.md Step 6 must gate pre-commit on the existence "
        "of .pre-commit-config.yaml — research worktrees are not software projects"
    )


def test_implement_experiment_subagent_b_not_software_specific() -> None:
    """Subagent B must not use software-project-specific language."""
    text = SKILL_PATH.read_text()
    # Subagent B section is between "**Subagent B" and "**Additional"
    if "**Subagent B" in text and "**Additional" in text:
        subagent_b_section = text.split("**Subagent B")[1].split("**Additional")[0]
    else:
        subagent_b_section = text
    # The old text had "build system, test framework, benchmark infrastructure"
    assert "build system" not in subagent_b_section, (
        "implement-experiment/SKILL.md Subagent B must not reference 'build system' — "
        "this is software-project language inappropriate for research experiments"
    )
