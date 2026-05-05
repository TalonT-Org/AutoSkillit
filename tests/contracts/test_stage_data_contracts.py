"""Contract tests for stage-data SKILL.md — pre-flight resource feasibility gate."""

from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "stage-data"
    / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text()


def test_stage_data_skill_exists() -> None:
    assert SKILL_PATH.exists()


def test_stage_data_emits_verdict_token(skill_text: str) -> None:
    assert "verdict =" in skill_text


def test_stage_data_emits_resource_report_token(skill_text: str) -> None:
    assert "resource_report =" in skill_text


def test_stage_data_reads_data_manifest(skill_text: str) -> None:
    assert "data_manifest" in skill_text


def test_stage_data_checks_disk_space(skill_text: str) -> None:
    lower = skill_text.lower()
    assert "disk" in lower
    assert "df" in lower or "disk space" in lower


def test_stage_data_checks_network_connectivity(skill_text: str) -> None:
    lower = skill_text.lower()
    assert "network" in lower or "connectivity" in lower or "reachab" in lower


def test_stage_data_creates_directory_structure(skill_text: str) -> None:
    lower = skill_text.lower()
    assert "mkdir" in lower or "directory structure" in lower or "create" in lower
    assert "data dir" in lower


def test_stage_data_documents_pass_verdict(skill_text: str) -> None:
    assert "PASS" in skill_text


def test_stage_data_documents_warn_verdict(skill_text: str) -> None:
    assert "WARN" in skill_text


def test_stage_data_documents_fail_verdict(skill_text: str) -> None:
    assert "FAIL" in skill_text


def test_stage_data_categories_include_research(skill_text: str) -> None:
    assert "categories" in skill_text
    assert "research" in skill_text


def test_stage_data_probes_allen_brain_atlas(skill_text: str) -> None:
    assert "brain-map.org" in skill_text


def test_stage_data_probes_cellxgene(skill_text: str) -> None:
    assert "cellxgene" in skill_text.lower()


def test_stage_data_probes_expression_atlas(skill_text: str) -> None:
    assert "gxa" in skill_text


def test_stage_data_probes_human_protein_atlas(skill_text: str) -> None:
    assert "proteinatlas.org" in skill_text


def test_stage_data_probes_string_db(skill_text: str) -> None:
    assert "string-db.org" in skill_text


def test_stage_data_probes_jaspar(skill_text: str) -> None:
    assert "jaspar" in skill_text.lower()


def test_stage_data_documents_rate_limit_behavior(skill_text: str) -> None:
    lower = skill_text.lower()
    assert "rate" in lower and "limit" in lower


def test_stage_data_probe_count_minimum(skill_text: str) -> None:
    known_sources = [
        "GEO / NCBI",
        "ENCODE",
        "UniProt",
        "Allen Brain Atlas",
        "CellxGene",
        "Expression Atlas",
        "Human Protein Atlas",
        "STRING",
        "JASPAR",
    ]
    count = sum(1 for src in known_sources if src.lower() in skill_text.lower())
    assert count >= 9
