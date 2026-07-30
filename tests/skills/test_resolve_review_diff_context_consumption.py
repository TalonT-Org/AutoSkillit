"""Guards: resolve-review loads and uses diff_context handoff file from review-pr."""

import json
from pathlib import Path

import pytest

from autoskillit.smoke_utils import (
    prepare_experimental_review_publication,
    publish_experimental_review_artifacts,
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-review"
    / "SKILL.md"
)


def _skill_text() -> str:
    return SKILL_PATH.read_text()


def _step2_section() -> str:
    text = _skill_text()
    start = text.find("### Step 2")
    end = text.find("### Step 3", start)
    return text[start:end]


def _step35_section() -> str:
    text = _skill_text()
    start = text.find("### Step 3.5")
    assert start != -1, "### Step 3.5 not found in SKILL.md"
    end = text.find("### Step 4", start)
    return text[start:end]


def _step4_section() -> str:
    text = _skill_text()
    start = text.find("### Step 4")
    assert start != -1, "### Step 4 not found in SKILL.md"
    end = text.find("### Step 5", start)
    end = end if end != -1 else None
    return text[start:end]


def test_step2_checks_for_diff_context_file():
    """Step 2 must check for the review-pr diff_context handoff file."""
    section = _step2_section()
    assert "diff_context" in section


def test_step2_loads_diff_context_map():
    """Step 2 must build a diff_context_map lookup structure."""
    section = _step2_section()
    assert "diff_context_map" in section


def test_step2_fallback_when_file_absent():
    """Step 2 must fall back to empty map when diff_context file is absent."""
    section = _step2_section()
    # Must mention fallback, absence, or empty-map behavior
    lower = section.lower()
    assert "absent" in lower or "not found" in lower or "fallback" in lower or "{}" in section


def test_step35_uses_prebuilt_code_region():
    """Step 3.5 sub-agent prompt must use pre-loaded code_region when available."""
    section = _step35_section()
    assert "diff_context_map" in section


def test_step35_skips_file_read_when_context_available():
    """Step 3.5 must skip 'read file' instruction when pre-built context is present."""
    section = _step35_section()
    lower = section.lower()
    # Must indicate the file-read instruction is conditional or skipped
    assert (
        "instead of" in lower
        or "skip" in lower
        or "do not read" in lower
        or "use the pre" in lower
    )


def test_step4_skips_understanding_read_when_context_present():
    """Step 4 must skip the ±20 line understanding read when diff_context_map has entry."""
    section = _step4_section()
    assert "diff_context_map" in section
    lower = section.lower()
    assert "skip" in lower or "omit" in lower or "already available" in lower


def test_step4_still_reads_file_for_editing():
    """Step 4 must still read the file for applying actual edits even with pre-built context."""
    section = _step4_section()
    lower = section.lower()
    # Must mention that file read for editing is still needed
    assert "still read" in lower or "read the file" in lower


def test_diff_context_path_matches_review_pr_output_path():
    """resolve-review's diff_context path must reference the review-pr handoff file."""
    full = _skill_text()
    assert "diff_context_" in full and "REVIEW_PR_OUTPUT" in full


def test_step2_map_type_is_dict_of_dicts():
    """Step 2 must declare dict[tuple[str, int], dict] not dict[tuple[str, int], str]."""
    section = _step2_section()
    assert "dict[tuple[str, int], dict]" in section


def test_step35_accesses_code_region_from_dict():
    """Step 3.5 must access code_region via .get('code_region') on the dict value."""
    section = _step35_section()
    assert '.get("code_region"' in section or ".get('code_region'" in section


def test_enriched_context_fields_remain_opaque_dict_values() -> None:
    section = _step2_section()
    assert "copy the complete entry dictionary" in section
    for field in (
        "evidence",
        "trace",
        "boundary_checks",
        "confidence",
        "simpler_behavior",
        "candidate_id",
        "disposition_id",
        "snapshot",
    ):
        assert field in section


@pytest.mark.parametrize("mode", ["local", "github"])
def test_actual_publisher_output_matches_resolve_review_path_line_boundary(
    tmp_path: Path,
    mode: str,
) -> None:
    findings = [
        {
            "file": "src/reach.py",
            "line": 11,
            "severity": "warning",
            "dimension": "overengineering_reachability",
            "message": "No reachable consumer",
            "requires_decision": False,
            "candidate_id": "reach",
            "disposition_id": "disposition-reach",
            "evidence": [{"opaque": "preserved"}],
            "code_region": "[L11]+unused",
        },
        {
            "file": "src/surface.py",
            "line": 22,
            "severity": "warning",
            "dimension": "overengineering_abstraction_surface",
            "message": "Unused abstraction surface",
            "requires_decision": False,
            "candidate_id": "surface",
            "disposition_id": "disposition-surface",
            "custom_proof": {"kept": True},
        },
        {
            "file": "src/standard.py",
            "line": 33,
            "severity": "critical",
            "dimension": "bugs",
            "message": "Standard finding",
            "requires_decision": False,
            "candidate_id": "standard",
        },
        {
            "file": "src/deleted.py",
            "line": 44,
            "severity": "critical",
            "dimension": "deletion_regression",
            "message": "Deletion regression",
            "requires_decision": False,
            "candidate_id": "deletion",
        },
    ]
    publication = prepare_experimental_review_publication(
        raw_ledger={"candidate_records": findings},
        survivors=findings,
        snapshot={"head_sha": "head", "base_sha": "base", "merge_base_sha": "merge"},
        annotation_generation_id="annotation",
        mode=mode,
        snapshot_is_fresh=True,
        handoff_metadata={"pr_number": 51, "iteration": 3},
        receipt=(
            {"posted": True, "http_status": 200, "commit_id": "head"} if mode == "github" else None
        ),
    )
    published = publish_experimental_review_artifacts(
        publication=publication,
        output_dir=str(tmp_path / mode),
        pr_number="51",
    )

    diff_context = json.loads(Path(published["published_paths"]["diff_context"]).read_text())
    resolve_review_map = {
        (entry["path"], entry["line"]): dict(entry) for entry in diff_context["context_entries"]
    }
    assert set(resolve_review_map) == {
        ("src/reach.py", 11),
        ("src/surface.py", 22),
        ("src/standard.py", 33),
        ("src/deleted.py", 44),
    }
    assert resolve_review_map[("src/reach.py", 11)]["code_region"] == "[L11]+unused"
    assert resolve_review_map[("src/reach.py", 11)]["evidence"] == [{"opaque": "preserved"}]
    assert resolve_review_map[("src/surface.py", 22)]["custom_proof"] == {"kept": True}
    assert {resolve_review_map[key]["dimension"] for key in resolve_review_map} >= {
        "overengineering_reachability",
        "overengineering_abstraction_surface",
        "bugs",
        "deletion_regression",
    }
