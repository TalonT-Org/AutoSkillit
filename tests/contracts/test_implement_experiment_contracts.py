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
    # The ALWAYS block must reference writing test files
    always_section = text.split("**ALWAYS:**")[1].split("##")[0] if "**ALWAYS:**" in text else text
    lower = always_section.lower()
    assert "test" in lower and ("write" in lower or "creat" in lower), (
        "implement-experiment/SKILL.md ALWAYS list must direct the agent to write test files "
        "alongside experiment scripts"
    )


def test_implement_experiment_step4_mentions_test_files() -> None:
    """implement-experiment Step 4 must mention creating test_ files."""
    text = SKILL_PATH.read_text()
    assert "test_" in text, (
        "implement-experiment/SKILL.md Step 4 must reference creating test_ files "
        "(e.g., test_analysis.py) alongside each experiment script"
    )


def test_implement_experiment_allows_pytest_collect_only() -> None:
    """implement-experiment must allow running pytest --collect-only as a verification step."""
    text = SKILL_PATH.read_text()
    assert "collect-only" in text or "collect_only" in text, (
        "implement-experiment/SKILL.md must include 'pytest --collect-only' as a "
        "test discovery verification step (distinct from running the full test suite)"
    )
