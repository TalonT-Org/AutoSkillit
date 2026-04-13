"""Contract tests for stage-data SKILL.md — pre-flight resource feasibility gate."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "stage-data"
    / "SKILL.md"
)


def test_stage_data_skill_exists() -> None:
    assert SKILL_PATH.exists()


def test_stage_data_emits_verdict_token() -> None:
    text = SKILL_PATH.read_text()
    assert "verdict =" in text


def test_stage_data_emits_resource_report_token() -> None:
    text = SKILL_PATH.read_text()
    assert "resource_report =" in text


def test_stage_data_reads_data_manifest() -> None:
    text = SKILL_PATH.read_text()
    assert "data_manifest" in text


def test_stage_data_checks_disk_space() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "disk" in lower
    assert "df" in lower or "disk space" in lower


def test_stage_data_checks_network_connectivity() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "network" in lower or "connectivity" in lower or "reachab" in lower


def test_stage_data_creates_directory_structure() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "mkdir" in lower or "directory structure" in lower or "create" in lower
    assert "data dir" in lower


def test_stage_data_documents_pass_verdict() -> None:
    text = SKILL_PATH.read_text()
    assert "PASS" in text


def test_stage_data_documents_warn_verdict() -> None:
    text = SKILL_PATH.read_text()
    assert "WARN" in text


def test_stage_data_documents_fail_verdict() -> None:
    text = SKILL_PATH.read_text()
    assert "FAIL" in text


def test_stage_data_categories_include_research() -> None:
    text = SKILL_PATH.read_text()
    assert "categories" in text
    assert "research" in text
