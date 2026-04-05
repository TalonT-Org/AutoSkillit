"""Contract tests for run-experiment SKILL.md — data provenance lifecycle."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "run-experiment"
    / "SKILL.md"
)


def test_blocked_hypotheses_token_documented() -> None:
    text = SKILL_PATH.read_text()
    assert "blocked_hypotheses" in text


def test_data_manifest_preflight_check() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "data manifest" in lower
    assert "pre-flight" in lower or "preflight" in lower
