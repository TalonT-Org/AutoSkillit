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
