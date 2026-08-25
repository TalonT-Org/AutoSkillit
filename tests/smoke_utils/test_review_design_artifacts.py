"""Smoke-utils tests relocated from the former monolith."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.smoke_utils import (
    check_review_posted,
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


def _setup_handoff(tmp_path: Path, entries: list[dict]) -> None:
    review_dir = tmp_path / ".autoskillit" / "temp" / "review-pr"
    review_dir.mkdir(parents=True)
    handoff = {"schema_version": 1, "context_entries": entries}
    (review_dir / "diff_context_123.json").write_text(json.dumps(handoff))
    (review_dir / "annotated_diff_123.txt").write_text(_ANNOTATED_DIFF_CONTENT)


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
        "schema_version": 2,
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
    expected["context_entries"][0]["code_region"] = enriched["context_entries"][0]["code_region"]
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


_REVIEW_REPOSITORY = "openai/autoskillit"

_REVIEW_PR_NUMBER = 42

_REVIEW_HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"

_REVIEW_LOGICAL_ITERATION = "review-pr:2"

_REVIEW_OPERATION_KEY = "f" * 64


def test_check_review_posted_has_keyword_only_identity_signature() -> None:
    import inspect

    signature = inspect.signature(check_review_posted)
    assert list(signature.parameters) == [
        "cwd",
        "receipt_path",
        "mode",
        "repository",
        "pr_number",
        "head_sha",
        "logical_iteration",
        "operation_key",
        "post_state",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )


def _review_receipt_payload(
    *,
    state: str = "SUCCEEDED",
    reconciliation_result: str = "NOT_NEEDED",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation_key": _REVIEW_OPERATION_KEY,
        "repository": _REVIEW_REPOSITORY,
        "pr_number": _REVIEW_PR_NUMBER,
        "head_sha": _REVIEW_HEAD_SHA,
        "logical_iteration": _REVIEW_LOGICAL_ITERATION,
        "state": state,
        "review_id": 9001,
        "comment_ids": [101, 102],
        "canonical_finding_count": 3,
        "finding_dispositions": [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 1, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
            {
                "original_index": 2,
                "kind": "OMITTED_INVALID",
                "reason": "line is outside the live diff",
            },
        ],
        "reconciliation_result": reconciliation_result,
        "dry_run": False,
    }


def _review_receipt_path(cwd: Path, pr_number: int = _REVIEW_PR_NUMBER) -> Path:
    path = (
        cwd
        / ".autoskillit"
        / "temp"
        / "review-pr"
        / _REVIEW_LOGICAL_ITERATION
        / f"batch_review_response_{pr_number}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _invoke_review_receipt_check(
    *,
    cwd: Path,
    receipt_path: Path,
    mode: str = "github",
    repository: str = _REVIEW_REPOSITORY,
    pr_number: int = _REVIEW_PR_NUMBER,
    head_sha: str = _REVIEW_HEAD_SHA,
    logical_iteration: str = _REVIEW_LOGICAL_ITERATION,
    operation_key: str = _REVIEW_OPERATION_KEY,
    post_state: str = "SUCCEEDED",
) -> dict[str, str]:
    return check_review_posted(
        cwd=str(cwd.resolve()),
        receipt_path=str(receipt_path),
        mode=mode,
        repository=repository,
        pr_number=pr_number,
        head_sha=head_sha,
        logical_iteration=logical_iteration,
        operation_key=operation_key,
        post_state=post_state,
    )


@pytest.mark.parametrize("state", ["SUCCEEDED", "RECONCILED"])
@pytest.mark.parametrize("reconciliation_result", ["NOT_NEEDED", "MATCHED", "ENRICHED"])
def test_check_review_posted_accepts_only_valid_final_receipt(
    tmp_path: Path,
    state: str,
    reconciliation_result: str,
) -> None:
    """A contained, identity-matched, exhaustively accounted final receipt succeeds."""
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    receipt.write_text(
        json.dumps(
            _review_receipt_payload(
                state=state,
                reconciliation_result=reconciliation_result,
            )
        )
    )

    result = _invoke_review_receipt_check(
        cwd=cwd,
        receipt_path=receipt,
        post_state=state,
    )

    assert result["reviews_posted"] == "true"
    assert result.get("sentinel", "") == ""


def test_check_review_posted_missing_receipt_returns_false(tmp_path: Path) -> None:
    """GitHub mode fails closed when the captured receipt does not exist."""
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_local_mode_succeeds_without_reading_receipt(
    tmp_path: Path,
) -> None:
    """Local review mode has no publication receipt and remains an explicit success."""
    cwd = tmp_path / "repo"
    missing_receipt = _review_receipt_path(cwd)

    result = _invoke_review_receipt_check(
        cwd=cwd,
        receipt_path=missing_receipt,
        mode="local",
        repository="not/a-canonical identity",
        head_sha="",
        logical_iteration="",
        operation_key="",
        post_state="",
    )

    assert result["reviews_posted"] == "true"


@pytest.mark.parametrize("mode", ["", "dry-run", "GITHUB", "unknown"])
def test_check_review_posted_unknown_mode_fails(tmp_path: Path, mode: str) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    receipt.write_text(json.dumps(_review_receipt_payload()))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt, mode=mode)

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("repository", "someone/else"),
        ("pr_number", 43),
        ("head_sha", "1" * 40),
        ("logical_iteration", "review-pr:3"),
        ("operation_key", "different-operation"),
        ("state", "RECONCILED"),
    ],
)
def test_check_review_posted_rejects_receipt_identity_mismatch(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    payload = _review_receipt_payload()
    payload[field] = bad_value
    receipt.write_text(json.dumps(payload))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("repository", "OpenAI/autoskillit"),
        ("head_sha", "ABCDEF0123456789ABCDEF0123456789ABCDEF01"),
        ("head_sha", "abc123"),
        ("logical_iteration", "2"),
        ("operation_key", ""),
        ("operation_key", "review-v1:approved"),
    ],
)
def test_check_review_posted_rejects_noncanonical_requested_identity(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    receipt.write_text(json.dumps(_review_receipt_payload()))
    assert isinstance(bad_value, str)
    requested_identity = {
        "repository": _REVIEW_REPOSITORY,
        "head_sha": _REVIEW_HEAD_SHA,
        "logical_iteration": _REVIEW_LOGICAL_ITERATION,
        "operation_key": _REVIEW_OPERATION_KEY,
    }
    requested_identity[field] = bad_value

    result = _invoke_review_receipt_check(
        cwd=cwd,
        receipt_path=receipt,
        repository=requested_identity["repository"],
        head_sha=requested_identity["head_sha"],
        logical_iteration=requested_identity["logical_iteration"],
        operation_key=requested_identity["operation_key"],
    )

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("schema_version", 2),
        ("schema_version", "1"),
        ("state", "PENDING"),
        ("state", "FAILED"),
        ("state", "DRY_RUN"),
        ("reconciliation_result", "UNKNOWN"),
        ("dry_run", True),
        ("review_id", 0),
        ("review_id", -1),
        ("review_id", "9001"),
        ("comment_ids", [101, 101]),
        ("comment_ids", [101, 0]),
        ("comment_ids", [101, "102"]),
        ("canonical_finding_count", -1),
        ("canonical_finding_count", "3"),
    ],
)
def test_check_review_posted_rejects_invalid_schema_or_nonfinal_state(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    payload = _review_receipt_payload()
    payload[field] = bad_value
    receipt.write_text(json.dumps(payload))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    "missing_field",
    [
        "schema_version",
        "operation_key",
        "repository",
        "pr_number",
        "head_sha",
        "logical_iteration",
        "state",
        "review_id",
        "comment_ids",
        "canonical_finding_count",
        "finding_dispositions",
        "reconciliation_result",
    ],
)
def test_check_review_posted_requires_complete_receipt_schema(
    tmp_path: Path,
    missing_field: str,
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    payload = _review_receipt_payload()
    del payload[missing_field]
    receipt.write_text(json.dumps(payload))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    "dispositions",
    [
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 0, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
            {"original_index": 2, "kind": "OMITTED_INVALID", "reason": "invalid"},
        ],
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 2, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
        ],
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 1, "kind": "UNKNOWN", "remote_comment_id": 102},
            {"original_index": 2, "kind": "OMITTED_INVALID", "reason": "invalid"},
        ],
        [
            {"original_index": 0, "kind": "POSTED"},
            {"original_index": 1, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
            {"original_index": 2, "kind": "OMITTED_INVALID", "reason": "invalid"},
        ],
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 0},
            {"original_index": 1, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
            {"original_index": 2, "kind": "OMITTED_INVALID", "reason": "invalid"},
        ],
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 1, "kind": "ALREADY_PRESENT", "remote_comment_id": 101},
            {"original_index": 2, "kind": "OMITTED_INVALID", "reason": "invalid"},
        ],
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 1, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
            {"original_index": 2, "kind": "OMITTED_INVALID", "reason": ""},
        ],
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 1, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
            {
                "original_index": 2,
                "kind": "OMITTED_INVALID",
                "remote_comment_id": 103,
                "reason": "invalid",
            },
        ],
    ],
)
def test_check_review_posted_requires_exhaustive_unique_disposition_partition(
    tmp_path: Path,
    dispositions: list[dict[str, object]],
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    payload = _review_receipt_payload()
    payload["finding_dispositions"] = dispositions
    receipt.write_text(json.dumps(payload))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_disposition_count_mismatch(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    payload = _review_receipt_payload()
    payload["canonical_finding_count"] = 4
    receipt.write_text(json.dumps(payload))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    "raw_content",
    [
        "{not valid JSON",
        "[]",
        "null",
        '"string"',
    ],
)
def test_check_review_posted_rejects_malformed_or_nonobject_json(
    tmp_path: Path,
    raw_content: str,
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    receipt.write_text(raw_content)

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_oversized_receipt(tmp_path: Path) -> None:
    """A syntactically valid receipt over the bounded parser limit is rejected."""
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    receipt.write_text(json.dumps(_review_receipt_payload()) + (" " * 1_048_577))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_path_outside_managed_temp(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    receipt = cwd / "outside" / f"batch_review_response_{_REVIEW_PR_NUMBER}.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(_review_receipt_payload()))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_traversal_path(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    managed_temp = cwd / ".autoskillit" / "temp"
    managed_temp.mkdir(parents=True)
    outside = cwd / "outside" / f"batch_review_response_{_REVIEW_PR_NUMBER}.json"
    outside.parent.mkdir()
    outside.write_text(json.dumps(_review_receipt_payload()))
    traversal = managed_temp / ".." / ".." / "outside" / outside.name

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=traversal)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_relative_receipt_path(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    receipt.write_text(json.dumps(_review_receipt_payload()))
    relative = receipt.relative_to(cwd)

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=relative)

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    "basename",
    [
        "receipt.json",
        "batch_review_response_41.json",
        "batch_review_response_42.json.bak",
    ],
)
def test_check_review_posted_requires_exact_receipt_basename(
    tmp_path: Path,
    basename: str,
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd).with_name(basename)
    receipt.write_text(json.dumps(_review_receipt_payload()))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_symlink_receipt(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    target = receipt.with_name("target.json")
    target.write_text(json.dumps(_review_receipt_payload()))
    receipt.symlink_to(target)

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_symlinked_parent_escape(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    managed_temp = cwd / ".autoskillit" / "temp"
    managed_temp.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    receipt = outside / f"batch_review_response_{_REVIEW_PR_NUMBER}.json"
    receipt.write_text(json.dumps(_review_receipt_payload()))
    linked_namespace = managed_temp / "review-pr"
    linked_namespace.symlink_to(outside, target_is_directory=True)

    result = _invoke_review_receipt_check(
        cwd=cwd,
        receipt_path=linked_namespace / receipt.name,
    )

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_hardlinked_receipt(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    source = receipt.with_name("source.json")
    source.write_text(json.dumps(_review_receipt_payload()))
    os.link(source, receipt)

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_has_no_subprocess_calls(tmp_path):
    """check_review_posted must not invoke any subprocess."""
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    with patch.object(
        subprocess, "run", side_effect=AssertionError("unexpected subprocess")
    ) as mock_run:
        _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)
    mock_run.assert_not_called()


def test_check_review_posted_in_smoke_utils_all():
    """check_review_posted must be exported from smoke_utils.__all__."""
    import autoskillit.smoke_utils as sm

    assert "check_review_posted" in sm.__all__
    assert callable(sm.check_review_posted)
