from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.smoke_utils import (
    extract_investigation,
)

pytestmark = [pytest.mark.medium]

_ISSUE_BODY_WITH_INVESTIGATION = (
    "Some preamble not in the investigation section.\n"
    "\n"
    "## Investigation\n"
    "<!-- investigation_complete: true -->\n"
    "> Prior investigation completed interactively. See below for root cause analysis.\n"
    "\n"
    "# Investigation: Topic\n"
    "## Summary\n"
    "Summary content here.\n"
    "## Root Cause\n"
    "Root cause content here.\n"
    "## Evidence\n"
    "Evidence content here.\n"
    "## Recommendations\n"
    "Recommendation content here.\n"
)


@patch("autoskillit.smoke_utils._investigation.run_gh")
def test_extract_investigation_full_content(mock_run_gh, tmp_path: Path) -> None:
    """Extraction must retain all ## subsections inside ## Investigation."""

    mock_run_gh.return_value = subprocess.CompletedProcess(
        [], 0, _ISSUE_BODY_WITH_INVESTIGATION, ""
    )
    out_dir = tmp_path / "investigate"
    result = extract_investigation(
        investigation_path="",
        issue_number="42",
        output_dir=str(out_dir),
    )
    assert result["investigation_report"] == str(out_dir / "investigation_from_issue.md")
    written = Path(result["investigation_report"]).read_text()
    assert "## Summary" in written
    assert "## Root Cause" in written
    assert "## Evidence" in written
    assert "## Recommendations" in written
    assert "Summary content here." in written
    assert "Root cause content here." in written
    assert "Evidence content here." in written
    assert "Recommendation content here." in written


def test_extract_investigation_passthrough(tmp_path: Path) -> None:
    """When investigation_path is set, file exists, and content is complete, return it."""

    report = tmp_path / "investigation_full.md"
    report.write_text(
        "# Investigation: Topic\n"
        "## Summary\n"
        "Summary content.\n"
        "## Recommendations\n"
        "Recommendations content.\n"
    )
    result = extract_investigation(
        investigation_path=str(report),
        issue_number="42",
        output_dir=str(tmp_path / "unused"),
    )
    assert result["investigation_report"] == str(report)


def test_extract_investigation_passthrough_truncated_raises(tmp_path: Path) -> None:
    """When investigation_path points to a truncated file (no ## subsections), callable raises."""

    truncated = tmp_path / "investigation_truncated.md"
    truncated.write_text(
        "<!-- investigation_complete: true -->\n"
        "> Prior investigation completed interactively. See below for root cause analysis.\n"
    )
    with pytest.raises(ValueError, match="no '## ' subsections"):
        extract_investigation(
            investigation_path=str(truncated),
            issue_number="42",
            output_dir=str(tmp_path / "unused"),
        )


@patch("autoskillit.smoke_utils._investigation.run_gh")
def test_extract_investigation_no_section_raises(mock_run_gh, tmp_path: Path) -> None:
    """When issue body has no ## Investigation section, callable raises."""

    mock_run_gh.return_value = subprocess.CompletedProcess(
        [], 0, "Body without the investigation section.\n## Other\n", ""
    )
    with pytest.raises(ValueError, match="## Investigation"):
        extract_investigation(
            investigation_path="",
            issue_number="42",
            output_dir=str(tmp_path),
        )


@patch("autoskillit.smoke_utils._investigation.run_gh")
def test_extract_investigation_empty_body_raises(mock_run_gh, tmp_path: Path) -> None:
    """When neither the section nor the body carries any ## subsection, callable raises."""

    mock_run_gh.return_value = subprocess.CompletedProcess(
        [], 0, "Preamble with no structure.\n## Investigation\n\n", ""
    )
    with pytest.raises(ValueError, match="no investigation to hand to rectify"):
        extract_investigation(
            investigation_path="",
            issue_number="42",
            output_dir=str(tmp_path),
        )


@patch("autoskillit.smoke_utils._investigation.run_gh")
def test_extract_investigation_attestation_section_falls_back_to_body(
    mock_run_gh, tmp_path: Path
) -> None:
    """An attestation-style section hands rectify the whole body, not a three-line note.

    Regression test for #4392. A sizeable minority of issues use ``## Investigation`` to
    record *that* an investigation happened, with the analysis written above the heading.
    Requiring a ``## Recommendations`` heading rejected 16 of 34 such issues and — because
    bridge_investigation now halts on failure — stopped the pipeline outright.
    """

    body = (
        "## Problem\n"
        "The real analysis lives up here, above the attestation.\n"
        "## Root cause\n"
        "Detailed root cause content.\n"
        "\n"
        "## Investigation\n"
        "<!-- investigation_complete: true -->\n"
        "> Prior investigation completed interactively; analysis included above.\n"
    )
    mock_run_gh.return_value = subprocess.CompletedProcess([], 0, body, "")
    out_dir = tmp_path / "investigate"
    result = extract_investigation(
        investigation_path="",
        issue_number="42",
        output_dir=str(out_dir),
    )
    written = Path(result["investigation_report"]).read_text()
    # The whole body is handed over, so the analysis above the heading survives.
    assert "The real analysis lives up here" in written
    assert "Detailed root cause content." in written
    assert "## Problem" in written


@patch("autoskillit.smoke_utils._investigation.run_gh")
def test_extract_investigation_gh_failure_raises(mock_run_gh, tmp_path: Path) -> None:
    """When gh issue view fails, callable raises ValueError."""

    mock_run_gh.return_value = subprocess.CompletedProcess([], 1, "", "gh: not authenticated")
    with pytest.raises(ValueError, match="gh issue view failed"):
        extract_investigation(
            investigation_path="",
            issue_number="42",
            output_dir=str(tmp_path),
        )


@patch("autoskillit.smoke_utils._investigation.run_gh")
def test_extract_investigation_ignores_h3_investigation_decoy(mock_run_gh, tmp_path: Path) -> None:
    """A decoy '### Investigation' subsection must not be mistaken for the real heading."""

    body = (
        "Preamble.\n"
        "### Investigation\n"
        "Decoy sub-subsection text, not the real heading.\n"
        "\n"
        "## Investigation\n"
        "## Summary\n"
        "Summary content.\n"
        "## Recommendations\n"
        "Recommendation content.\n"
    )
    mock_run_gh.return_value = subprocess.CompletedProcess([], 0, body, "")
    out_dir = tmp_path / "investigate"
    result = extract_investigation(
        investigation_path="",
        issue_number="42",
        output_dir=str(out_dir),
    )
    written = Path(result["investigation_report"]).read_text()
    assert "Decoy sub-subsection text" not in written
    assert "## Summary" in written
    assert "Summary content." in written


def test_extract_investigation_passthrough_rejects_h3_subsection_decoy(
    tmp_path: Path,
) -> None:
    """A '### ' heading is not a '## ' subsection and must not satisfy the check."""

    truncated = tmp_path / "investigation_truncated.md"
    truncated.write_text(
        "<!-- investigation_complete: true -->\n"
        "> Prior investigation completed interactively.\n"
        "### Recommendations for future work (not a real section)\n"
    )
    with pytest.raises(ValueError, match="no '## ' subsections"):
        extract_investigation(
            investigation_path=str(truncated),
            issue_number="42",
            output_dir=str(tmp_path / "unused"),
        )


def test_extract_investigation_accepts_report_without_recommendations(
    tmp_path: Path,
) -> None:
    """Completeness must not be proxied on one heading name (#4392).

    A structured report using a different terminal section is complete. The prior
    revision rejected this shape, which is what halted 16 of 34 real investigations.
    """

    report = tmp_path / "investigation_no_recs.md"
    report.write_text(
        "# Investigation: Topic\n"
        "## Summary\n"
        "Summary content.\n"
        "## Root Cause\n"
        "Root cause content.\n"
        "## Scope Boundary\n"
        "Scope content — no Recommendations heading anywhere.\n"
    )
    result = extract_investigation(
        investigation_path=str(report),
        issue_number="42",
        output_dir=str(tmp_path / "unused"),
    )
    assert result["investigation_report"] == str(report)
