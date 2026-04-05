"""Contract tests for write-report SKILL.md — data provenance lifecycle."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "write-report"
    / "SKILL.md"
)


def test_data_scope_statement_required() -> None:
    text = SKILL_PATH.read_text()
    assert "Data Scope Statement" in text or "data scope statement" in text.lower()


def test_data_scope_in_executive_summary() -> None:
    text = SKILL_PATH.read_text()
    assert "Executive Summary" in text


def test_metrics_provenance_check() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "provenance" in lower


def test_gate_enforcement_no_substitution() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "substitut" in lower


def test_gate_enforcement_fail_state() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "fail" in lower and "gate" in lower
