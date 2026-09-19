from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from autoskillit.execution import extract_annotated_source_line, hash_source_line
from autoskillit.smoke_utils import (
    clear_review_annotation_context,
    enrich_diff_context,
)

pytestmark = [pytest.mark.medium]

_ANNOTATED_DIFF_CONTENT = (
    "+++ b/src/app.py\n"
    "@@ -38,10 +38,12 @@ def main():\n"
    "[L38] existing_line_38\n"
    "[L39] existing_line_39\n"
    "[L40]+new_import\n"
    "[L41]+another_import\n"
    "[L42] existing_42\n"
    "[L43] existing_43\n"
    "[L44]+added_44\n"
    "[L45] existing_45\n"
)


def _setup_handoff(
    tmp_path: Path,
    entries: list[dict],
    *,
    annotated_diff: str = _ANNOTATED_DIFF_CONTENT,
) -> None:
    review_dir = tmp_path / ".autoskillit" / "temp" / "review-pr"
    review_dir.mkdir(parents=True)
    handoff = {"schema_version": 1, "context_entries": entries}
    (review_dir / "diff_context_123.json").write_text(json.dumps(handoff))
    (review_dir / "annotated_diff_123.txt").write_text(annotated_diff)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _real_checkout(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "tracked.txt").write_text("tracked\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    return repo, _git(repo, "rev-parse", "HEAD")


def _setup_checkout_handoff(repo: Path, head_sha: str) -> tuple[Path, Path]:
    review_dir = repo / ".autoskillit" / "temp" / "review-pr"
    review_dir.mkdir(parents=True)
    handoff_path = review_dir / "diff_context_123.json"
    handoff_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "_head_sha": head_sha,
                "context_entries": [
                    {
                        "path": "src/app.py",
                        "line": 42,
                        "severity": "critical",
                        "code_region": "",
                    }
                ],
            }
        )
    )
    (review_dir / "annotated_diff_123.txt").write_text(_ANNOTATED_DIFF_CONTENT)
    return review_dir, handoff_path


def test_enrich_diff_context_fills_empty_code_regions(tmp_path: Path) -> None:
    """enrich_diff_context populates empty code_region from annotated diff."""
    _setup_handoff(
        tmp_path,
        [
            {"path": "src/app.py", "line": 42, "severity": "critical", "code_region": ""},
        ],
    )
    review_dir = tmp_path / ".autoskillit" / "temp" / "review-pr"
    result = enrich_diff_context(
        pr_number="123", project_dir=str(tmp_path), output_dir=str(review_dir)
    )
    assert result["enriched"] == "true"
    assert result["enriched_count"] == "1"

    handoff_path = review_dir / "diff_context_123.json"
    handoff = json.loads(handoff_path.read_text())
    assert "[L42]" in handoff["context_entries"][0]["code_region"]


def test_enrich_diff_context_atomically_upgrades_v1_entries_with_digests(
    tmp_path: Path,
) -> None:
    """A complete v1 handoff becomes v2 only after all eligible anchors resolve."""
    _setup_handoff(
        tmp_path,
        [
            {"path": "src/app.py", "line": 42, "severity": "critical", "code_region": ""},
            {
                "path": "src/app.py",
                "line": 43,
                "severity": "warning",
                "code_region": "pre-existing",
            },
            {"path": "src/app.py", "line": None, "severity": "info", "code_region": ""},
        ],
    )
    review_dir = tmp_path / ".autoskillit" / "temp" / "review-pr"

    enrich_diff_context(pr_number="123", project_dir=str(tmp_path), output_dir=str(review_dir))

    handoff = json.loads((review_dir / "diff_context_123.json").read_text())
    assert handoff["schema_version"] == 2
    integer_line_entries = [
        entry for entry in handoff["context_entries"] if type(entry["line"]) is int
    ]
    assert all(entry["anchor_digest"] for entry in integer_line_entries)
    assert "[L42]" in integer_line_entries[0]["code_region"]
    assert integer_line_entries[1]["code_region"] == "pre-existing"
    no_line_entry = handoff["context_entries"][2]
    assert no_line_entry["code_region"] == ""
    assert "anchor_digest" not in no_line_entry


def test_enrich_diff_context_accepts_matching_real_checkout_head(tmp_path: Path) -> None:
    repo, head_sha = _real_checkout(tmp_path)
    review_dir, handoff_path = _setup_checkout_handoff(repo, head_sha)

    result = enrich_diff_context(
        pr_number="123", project_dir=str(repo), output_dir=str(review_dir)
    )

    assert result["enriched"] == "true"
    assert json.loads(handoff_path.read_text())["schema_version"] == 2


def test_enrich_diff_context_rejects_mismatching_real_checkout_head(tmp_path: Path) -> None:
    repo, _ = _real_checkout(tmp_path)
    review_dir, handoff_path = _setup_checkout_handoff(repo, "0" * 40)
    original = handoff_path.read_bytes()

    result = enrich_diff_context(
        pr_number="123", project_dir=str(repo), output_dir=str(review_dir)
    )

    assert result == {"enriched": "false", "reason": "checkout_head_mismatch"}
    assert handoff_path.read_bytes() == original


def _annotated_digest(annotated_diff: str, line: int) -> str:
    source_line = extract_annotated_source_line(annotated_diff, "src/app.py", line)
    assert source_line is not None
    return hash_source_line(source_line)


def test_anchor_digest_survives_unrelated_line_movement() -> None:
    before = "+++ b/src/app.py\n[L2] before\n[L3] target = 1\n"
    shifted = "+++ b/src/app.py\n[L2]+inserted\n[L3] before\n[L4] target = 1\n"

    assert _annotated_digest(before, 3) == _annotated_digest(shifted, 4)


def test_anchor_digest_changes_when_target_content_changes() -> None:
    before = "+++ b/src/app.py\n[L3] target = 1\n"
    edited = "+++ b/src/app.py\n[L3] target = 2\n"

    assert _annotated_digest(before, 3) != _annotated_digest(edited, 3)


@pytest.mark.parametrize(
    ("line", "annotated_diff"),
    [
        (999, _ANNOTATED_DIFF_CONTENT),
        (42, _ANNOTATED_DIFF_CONTENT + "[L42] duplicate_42\n"),
        (42, "+++ b/src/app.py\n@@ -42 +42 @@\n[L42]\n"),
    ],
    ids=["missing", "ambiguous", "malformed"],
)
def test_enrich_diff_context_keeps_v1_handoff_when_an_integer_anchor_is_invalid(
    tmp_path: Path,
    line: int,
    annotated_diff: str,
) -> None:
    """Failed integer anchor extraction must not publish a partial v2 handoff."""
    _setup_handoff(
        tmp_path,
        [{"path": "src/app.py", "line": line, "severity": "critical", "code_region": ""}],
        annotated_diff=annotated_diff,
    )
    review_dir = tmp_path / ".autoskillit" / "temp" / "review-pr"
    handoff_path = review_dir / "diff_context_123.json"
    original = handoff_path.read_bytes()

    enrich_diff_context(pr_number="123", project_dir=str(tmp_path), output_dir=str(review_dir))

    assert handoff_path.read_bytes() == original


def test_enrich_diff_context_preserves_existing_code_regions(tmp_path: Path) -> None:
    """enrich_diff_context does not overwrite non-empty code_region values."""
    _setup_handoff(
        tmp_path,
        [
            {
                "path": "src/app.py",
                "line": 42,
                "severity": "critical",
                "code_region": "pre-existing",
            },
            {"path": "src/app.py", "line": 40, "severity": "warning", "code_region": ""},
        ],
    )
    review_dir = tmp_path / ".autoskillit" / "temp" / "review-pr"
    result = enrich_diff_context(
        pr_number="123", project_dir=str(tmp_path), output_dir=str(review_dir)
    )
    assert result["enriched"] == "true"
    assert result["enriched_count"] == "1"

    handoff_path = review_dir / "diff_context_123.json"
    handoff = json.loads(handoff_path.read_text())
    assert handoff["context_entries"][0]["code_region"] == "pre-existing"
    assert "[L40]" in handoff["context_entries"][1]["code_region"]


def test_enrich_diff_context_preserves_experimental_provenance(tmp_path: Path) -> None:
    """Enrichment changes only code_region on an experimental context entry."""
    review_dir = tmp_path / ".autoskillit" / "temp" / "review-pr"
    review_dir.mkdir(parents=True)
    entry = {
        "path": "src/app.py",
        "line": 42,
        "severity": "warning",
        "message": "Unreachable abstraction",
        "code_region": "",
        "evidence": [
            {"path": "src/app.py", "line": 42, "role": "anchor", "claim": "Declaration"},
            {"path": "src/app.py", "line": 44, "role": "consumer", "claim": "Only consumer"},
        ],
        "trace": [{"path": "src/app.py", "line": 44, "relation": "calls"}],
        "boundary_checks": [
            {
                "boundary": "public_api",
                "status": "checked_no_reachable_path",
                "claim": "No public entry point",
            }
        ],
        "confidence": 0.9,
        "simpler_behavior": "Equivalent across all semantic categories",
        "candidate_id": "candidate-1",
        "disposition_id": "disposition-1",
        "snapshot": {"head_sha": "head", "diff_sha256": "diff"},
        "opaque_future_field": {"preserve": True},
    }
    handoff = {
        "schema_version": 1,
        "_head_sha": "head",
        "_base_sha": "base",
        "_merge_base_sha": "merge-base",
        "annotation_generation_id": "generation-1",
        "review_generation_id": "review-1",
        "context_entries": [entry],
    }
    (review_dir / "diff_context_123.json").write_text(json.dumps(handoff))
    (review_dir / "annotated_diff_123.txt").write_text(_ANNOTATED_DIFF_CONTENT)

    result = enrich_diff_context(
        pr_number="123", project_dir=str(tmp_path), output_dir=str(review_dir)
    )

    assert result["enriched"] == "true"
    enriched = json.loads((review_dir / "diff_context_123.json").read_text())
    expected = json.loads(json.dumps(handoff))
    expected["schema_version"] = 2
    expected["context_entries"][0]["code_region"] = enriched["context_entries"][0]["code_region"]
    expected["context_entries"][0]["anchor_digest"] = enriched["context_entries"][0][
        "anchor_digest"
    ]
    assert "[L42]" in enriched["context_entries"][0]["code_region"]
    assert enriched == expected


def test_enrich_diff_context_missing_handoff_file(tmp_path: Path) -> None:
    """enrich_diff_context returns gracefully when handoff file does not exist."""
    result = enrich_diff_context(
        pr_number="999", project_dir=str(tmp_path), output_dir=str(tmp_path)
    )
    assert result["enriched"] == "false"
    assert result["reason"] == "handoff_not_found"


def test_aggregate_review_verdict_go(tmp_path: Path) -> None:
    """GO verdict when no criticals and warnings below threshold."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    findings = [
        {"dimension": "scope_alignment", "severity": "info", "message": "ok"},
        {"dimension": "variance_protocol", "severity": "warning", "message": "minor"},
    ]
    (tmp_path / "findings.json").write_text(json.dumps(findings))
    dims = {"scope_alignment": "H", "variance_protocol": "M"}
    (tmp_path / "dims.json").write_text(json.dumps(dims))

    result = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "findings.json"),
        dimensions_manifest_path=str(tmp_path / "dims.json"),
        output_dir=str(tmp_path / "out"),
    )
    assert result["verdict"] == "GO"
    assert "evaluation_dashboard_path" in result
    assert Path(result["evaluation_dashboard_path"]).exists()
    assert "revision_guidance_path" not in result


def test_aggregate_review_verdict_revise(tmp_path: Path) -> None:
    """REVISE verdict when non-stop-trigger critical is present."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    findings = [
        {
            "dimension": "scope_alignment",
            "severity": "critical",
            "message": "gap",
            "fixability": "ADDRESSABLE",
        },
    ]
    (tmp_path / "findings.json").write_text(json.dumps(findings))

    result = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "findings.json"),
        output_dir=str(tmp_path / "out"),
    )
    assert result["verdict"] == "REVISE"
    assert "revision_guidance_path" in result
    assert Path(result["revision_guidance_path"]).exists()
    assert Path(result["evaluation_dashboard_path"]).exists()


def test_aggregate_review_verdict_stop_structural_l1(tmp_path: Path) -> None:
    """STOP verdict on estimand_clarity critical with fixability=None."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    findings = [
        {
            "dimension": "estimand_clarity",
            "severity": "critical",
            "message": "ambiguous",
            "fixability": None,
        },
    ]
    (tmp_path / "findings.json").write_text(json.dumps(findings))

    result = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "findings.json"),
        output_dir=str(tmp_path / "out"),
    )
    assert result["verdict"] == "STOP"
    assert "revision_guidance_path" not in result
    assert "evaluation_dashboard_path" in result
    assert Path(result["evaluation_dashboard_path"]).exists()


def test_aggregate_review_verdict_estimand_clarity_addressable_is_revise(tmp_path: Path) -> None:
    """estimand_clarity critical with fixability=ADDRESSABLE → REVISE, not STOP."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    findings = [
        {
            "dimension": "estimand_clarity",
            "severity": "critical",
            "message": "ambiguous but addressable",
            "fixability": "ADDRESSABLE",
        },
    ]
    (tmp_path / "findings.json").write_text(json.dumps(findings))

    result = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "findings.json"),
        output_dir=str(tmp_path / "out"),
    )
    assert result["verdict"] == "REVISE"
    assert "revision_guidance_path" in result
    assert Path(result["revision_guidance_path"]).exists()
    assert Path(result["evaluation_dashboard_path"]).exists()


def test_structural_fixability_values_matches_skill_md_pseudocode() -> None:
    """_STRUCTURAL_FIXABILITY_VALUES must be referenced by name in SKILL.md pseudocode."""
    from autoskillit.core import pkg_root

    skill_md = (pkg_root() / "skills_extended" / "review-design" / "SKILL.md").read_text()
    step7_start = skill_md.find("### Step 7")
    assert step7_start != -1, "SKILL.md must contain '### Step 7' heading"
    step7_end = skill_md.find("### Step 8")
    assert step7_end != -1, "SKILL.md must contain '### Step 8' heading"
    step7_text = skill_md[step7_start:step7_end]

    match = re.search(
        r"structural_stop_triggers\s*=\s*\[(.+?)\n\s*\]",
        step7_text,
        re.DOTALL,
    )
    assert match, "Step 7 must contain structural_stop_triggers list comprehension"
    comprehension_body = match.group(1)

    assert "_STRUCTURAL_FIXABILITY_VALUES" in comprehension_body, (
        "structural_stop_triggers must reference _STRUCTURAL_FIXABILITY_VALUES by name — "
        "do not inline the fixability values as separate OR clauses"
    )

    assert "f.dimension ==" not in comprehension_body, (
        "structural_stop_triggers must not use dimension-only matching — "
        "this was the original bug (issue #3092)"
    )
    assert 'f.get("dimension")' not in comprehension_body, (
        "structural_stop_triggers must not use f.get('dimension') matching — "
        "use fixability-based gating only"
    )


def test_aggregate_review_verdict_rt_cap_downgrades(tmp_path: Path) -> None:
    """rt_max_severity='warning' downgrades red_team critical to warning."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    findings = [
        {"dimension": "red_team", "severity": "critical", "message": "adversarial"},
    ]
    (tmp_path / "findings.json").write_text(json.dumps(findings))
    dims = {"scope_alignment": "H"}
    (tmp_path / "dims.json").write_text(json.dumps(dims))

    result = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "findings.json"),
        dimensions_manifest_path=str(tmp_path / "dims.json"),
        rt_max_severity="warning",
        output_dir=str(tmp_path / "out"),
    )
    assert result["verdict"] == "GO"
    dashboard = Path(result["evaluation_dashboard_path"]).read_text()
    assert "warning_count: 1" in dashboard
    assert "critical_count: 0" in dashboard


def test_aggregate_review_verdict_empty_path_returns_go(tmp_path: Path) -> None:
    """Empty findings_manifest_path (silent type path) returns GO with no findings."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    result = aggregate_review_verdict(
        findings_manifest_path="",
        output_dir=str(tmp_path / "out"),
    )
    assert result.get("verdict") == "GO"
    assert "error" not in result


def test_aggregate_review_verdict_missing_file_returns_error(tmp_path: Path) -> None:
    """Non-existent findings_manifest_path returns error key."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    result = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "nonexistent.json"),
        output_dir=str(tmp_path / "out"),
    )
    assert "error" in result


def test_aggregate_review_verdict_warning_threshold_proportional(tmp_path: Path) -> None:
    """warning_threshold = active_dimensions * 5: 10 warnings -> REVISE, 9 -> GO."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    dims = {"dim_a": "H", "dim_b": "M"}  # 2 active -> threshold=10
    (tmp_path / "dims.json").write_text(json.dumps(dims))

    findings_10 = [
        {"dimension": "dim_a", "severity": "warning", "message": f"w{i}"} for i in range(10)
    ]
    (tmp_path / "f10.json").write_text(json.dumps(findings_10))
    r10 = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "f10.json"),
        dimensions_manifest_path=str(tmp_path / "dims.json"),
        output_dir=str(tmp_path / "out10"),
    )
    assert r10["verdict"] == "REVISE"

    findings_9 = [
        {"dimension": "dim_a", "severity": "warning", "message": f"w{i}"} for i in range(9)
    ]
    (tmp_path / "f9.json").write_text(json.dumps(findings_9))
    r9 = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "f9.json"),
        dimensions_manifest_path=str(tmp_path / "dims.json"),
        output_dir=str(tmp_path / "out9"),
    )
    assert r9["verdict"] == "GO"


_ANNOTATED_DIFF_ITER = (
    "+++ b/src/app.py\n"
    "@@ -38,10 +38,12 @@ def main():\n"
    "[L38] existing_line_38\n"
    "[L39] existing_line_39\n"
    "[L40]+new_import\n"
    "[L41]+another_import\n"
    "[L42] existing_42\n"
    "[L43] existing_43\n"
)


def _setup_iter_handoff(iter_dir: Path, pr: str = "123") -> None:
    iter_dir.mkdir(parents=True)
    handoff = {
        "schema_version": 1,
        "context_entries": [
            {"path": "src/app.py", "line": 42, "severity": "critical", "code_region": ""},
        ],
    }
    (iter_dir / f"diff_context_{pr}.json").write_text(json.dumps(handoff))
    (iter_dir / f"annotated_diff_{pr}.txt").write_text(_ANNOTATED_DIFF_ITER)


def test_enrich_diff_context_iteration_scoped_output_dir(tmp_path: Path) -> None:
    """enrich_diff_context reads from iteration-scoped output_dir."""
    iter_dir = tmp_path / ".autoskillit" / "temp" / "review-pr" / "iter_1"
    _setup_iter_handoff(iter_dir)

    result = enrich_diff_context(
        pr_number="123",
        project_dir=str(tmp_path),
        output_dir=str(iter_dir),
    )
    assert result["enriched"] == "true"
    assert int(result["enriched_count"]) > 0


def test_enrich_diff_context_requires_output_dir() -> None:
    """enrich_diff_context must raise TypeError when output_dir is not provided."""
    with pytest.raises(TypeError):
        enrich_diff_context(pr_number="1", project_dir="/tmp")  # type: ignore[call-arg]


def test_clear_review_annotation_context_clears_anchor_authority() -> None:
    result = clear_review_annotation_context()

    assert result["anchor_authority_path"] == ""
