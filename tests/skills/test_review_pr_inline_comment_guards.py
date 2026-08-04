"""Structural guards for canonical PR-review publication.

Review-producing skills hand one complete review to ``post_pr_review``.  The
prompt must not reproduce GitHub mutation, retry, pacing, or fallback policy.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

_SKILLS_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "skills_extended"

_WRITERS = (
    pytest.param("review-pr", "review-pr", id="review-pr"),
    pytest.param("review-research-pr", "review-research-pr", id="review-research-pr"),
    pytest.param("audit-claims", "audit-claims", id="audit-claims"),
    pytest.param("resolve-review", "resolve-review", id="resolve-review-deferred"),
)

_CAPTURED_RECEIPT_FIELDS = (
    "review_operation_key",
    "review_head_sha",
    "review_post_state",
    "review_receipt_path",
)

_RAW_REVIEW_ENDPOINT_RE = re.compile(r"/pulls/(?:[^/\s]+|\{[^}]+\})/reviews(?!/)")
_RAW_COMMENT_ENDPOINT_RE = re.compile(r"/pulls/(?:[^/\s]+|\{[^}]+\})/comments(?!/)")
_POST_RE = re.compile(r"(?:--method\s+POST|-X\s+POST|\bPOST\b)", re.IGNORECASE)
_FENCED_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def _skill_text(skill_name: str) -> str:
    path = _SKILLS_ROOT / skill_name / "SKILL.md"
    assert path.exists(), f"SKILL.md not found for canonical review writer {skill_name!r}"
    return path.read_text()


def _publication_section(text: str) -> str:
    call_idx = text.find("post_pr_review")
    assert call_idx >= 0, "canonical review writer must call post_pr_review"
    section_start = text.rfind("\n### ", 0, call_idx)
    section_end = text.find("\n### ", call_idx)
    return text[max(0, section_start) : section_end if section_end >= 0 else len(text)]


def _parameter_is_present(section: str, name: str) -> bool:
    return bool(
        re.search(
            rf"(?m)^\s*(?:[-*]\s*)?[`\"']?{re.escape(name)}[`\"']?\s*[:=]",
            section,
        )
    )


def _parameter_line(section: str, name: str) -> str:
    pattern = re.compile(rf"[`\"']?{re.escape(name)}[`\"']?\s*[:=]")
    return next(line for line in section.splitlines() if pattern.search(line))


def _fenced_blocks(text: str) -> list[str]:
    return _FENCED_BLOCK_RE.findall(text)


def test_standard_findings_decode_degrades_on_parse_or_type_failure() -> None:
    text = _skill_text("review-pr")
    assert "except json.JSONDecodeError:" in text
    assert "standard findings are not valid JSON" in text
    assert "isinstance(STANDARD_FINDINGS_DECODED, list)" in text
    assert "standard findings must be a JSON array" in text
    assert "if STANDARD_VALIDATION_ERRORS:" in text
    assert '"validation_errors": STANDARD_VALIDATION_ERRORS' in text


def test_auditor_status_uses_one_authoritative_mapping() -> None:
    text = _skill_text("review-pr")
    assert 'AUDITOR_STATUS_BY_NAME.update(VALIDATION_RESULT["status_by_name"])' in text
    assert "EXPERIMENTAL_AUDITOR_STATUS =" not in text
    assert "`AUDITOR_STATUS_BY_NAME` terminal-status authority" in text


@pytest.mark.parametrize(("skill_name", "iteration_namespace"), _WRITERS)
def test_canonical_writer_uses_iteration_namespace(
    skill_name: str,
    iteration_namespace: str,
) -> None:
    text = _skill_text(skill_name)
    assert iteration_namespace in _publication_section(text)


@pytest.mark.parametrize(("skill_name", "iteration_namespace"), _WRITERS)
def test_post_pr_review_receives_canonical_identity_and_head(
    skill_name: str,
    iteration_namespace: str,
) -> None:
    text = _skill_text(skill_name)
    section = _publication_section(text)

    for parameter in (
        "cwd",
        "repository",
        "pr_number",
        "head_sha",
        "logical_iteration",
    ):
        assert _parameter_is_present(section, parameter), (
            f"{skill_name}/SKILL.md post_pr_review call must pass {parameter!r}"
        )

    assert "nameWithOwner" in text, (
        f"{skill_name}/SKILL.md must resolve the canonical nameWithOwner repository "
        "rather than reconstructing owner/repo fragments"
    )
    assert (
        "^[0-9a-f]{40}$" in text
        or "40-character lowercase" in text.lower()
        or "40 character lowercase" in text.lower()
    ), f"{skill_name}/SKILL.md must validate a full, lowercase 40-hex head SHA before publication"

    logical_idx = section.find("logical_iteration")
    logical_context = section[max(0, logical_idx - 300) : logical_idx + 500]
    assert iteration_namespace in logical_context, (
        f"{skill_name}/SKILL.md logical_iteration must be namespaced with {iteration_namespace!r}"
    )


@pytest.mark.parametrize(("skill_name", "iteration_namespace"), _WRITERS)
def test_post_pr_review_receives_complete_review_and_contained_receipt(
    skill_name: str,
    iteration_namespace: str,
) -> None:
    del iteration_namespace
    section = _publication_section(_skill_text(skill_name))

    for parameter in ("event", "body", "comments", "receipt_path", "dry_run"):
        assert _parameter_is_present(section, parameter), (
            f"{skill_name}/SKILL.md post_pr_review call must pass the complete {parameter!r} value"
        )

    assert re.search(r"batch_review_response_[^\s`\"']*pr[^\s`\"']*\.json", section, re.I), (
        f"{skill_name}/SKILL.md receipt_path must use batch_review_response_<pr>.json"
    )
    assert "AUTOSKILLIT_TEMP" in section or "OUTPUT_DIR" in section, (
        f"{skill_name}/SKILL.md receipt_path must be contained by its declared "
        "AutoSkillit output directory"
    )
    assert "/tmp/" not in section

    for parameter in ("event", "body", "comments"):
        parameter_line = _parameter_line(section, parameter)
        assert not re.search(
            rf"[`\"']?{parameter}[`\"']?\s*[:=]\s*(?:\[\s*\]|[\"']{{2}}|null|None)"
            r"\s*[,)]?\s*$",
            parameter_line,
        ), (
            f"{skill_name}/SKILL.md must pass the prepared {parameter} value, not an "
            "empty or placeholder value"
        )

    dry_run_line = _parameter_line(section, "dry_run")
    assert re.search(
        r"[`\"']?dry_run[`\"']?\s*[:=]\s*(?:false|False)\b",
        dry_run_line,
    ), f"{skill_name}/SKILL.md publication must pass dry_run=false"


@pytest.mark.parametrize(("skill_name", "iteration_namespace"), _WRITERS)
def test_writer_captures_receipt_state_before_preserving_verdict(
    skill_name: str,
    iteration_namespace: str,
) -> None:
    del iteration_namespace
    text = _skill_text(skill_name)
    call_idx = text.find("post_pr_review")
    tail = text[call_idx:]

    for field in _CAPTURED_RECEIPT_FIELDS:
        assert field in tail, (
            f"{skill_name}/SKILL.md must capture post_pr_review output field {field!r}"
        )
    assert "verdict=" in tail, (
        f"{skill_name}/SKILL.md must preserve its verdict output after publication"
    )


@pytest.mark.parametrize(("skill_name", "iteration_namespace"), _WRITERS)
def test_writer_contains_no_raw_or_fallback_review_mutations(
    skill_name: str,
    iteration_namespace: str,
) -> None:
    del iteration_namespace
    text = _skill_text(skill_name)
    publication_section = _publication_section(text)

    for block in _fenced_blocks(text):
        if "gh api" not in block or not _POST_RE.search(block):
            continue
        assert not _RAW_REVIEW_ENDPOINT_RE.search(block), (
            f"{skill_name}/SKILL.md must not issue a raw Reviews API mutation"
        )
        assert not _RAW_COMMENT_ENDPOINT_RE.search(block), (
            f"{skill_name}/SKILL.md must not issue individual review-comment mutations"
        )

    forbidden_fallbacks = (
        "tier 1 fallback",
        "tier 2 fallback",
        "split the batch",
        "split review",
        "split comments",
        "file-level fallback",
        "file level fallback",
        "individual comment posting",
        "per-finding post",
        "supplementary review",
        "second review",
        "second summary",
        "second summary review",
        "summary-only review",
    )
    lower = text.lower()
    for phrase in forbidden_fallbacks:
        assert phrase not in lower, (
            f"{skill_name}/SKILL.md must delegate mutation policy to post_pr_review; "
            f"found forbidden prompt fallback {phrase!r}"
        )

    assert "subject_type" not in publication_section, (
        f"{skill_name}/SKILL.md must not synthesize file-level fallback comments"
    )
    assert not re.search(r"\bself[- ]review\b", publication_section, re.IGNORECASE), (
        f"{skill_name}/SKILL.md must not perform prompt-level self-review transformation"
    )
    assert not re.search(
        r"\b(?:transform|rewrite|repair)\s+(?:the\s+)?review\b",
        publication_section,
        re.IGNORECASE,
    ), f"{skill_name}/SKILL.md must pass the already-complete review payload unchanged"
    assert not re.search(r"\bsleep(?:\s+\d+|\s*\()", publication_section, re.IGNORECASE), (
        f"{skill_name}/SKILL.md must not pace post_pr_review mutations in the prompt"
    )


@pytest.mark.parametrize(
    "skill_name",
    ("review-pr", "review-research-pr"),
)
def test_review_writer_preserves_line_anchor_and_severity_filtering(skill_name: str) -> None:
    text = _skill_text(skill_name)
    assert "[LNNN]" in text
    assert "VALID_DIFF_LINES" in text

    section = _publication_section(text)
    assert "severity" in section.lower(), (
        f"{skill_name}/SKILL.md must filter the final comments payload by severity"
    )


def test_review_pr_preserves_local_mode_without_publication() -> None:
    text = _skill_text("review-pr")
    local_idx = text.lower().find("mode=local")
    assert local_idx >= 0
    local_section = text[local_idx : local_idx + 2500]
    assert "local_findings" in local_section
    assert any(
        phrase in local_section.lower()
        for phrase in (
            "do not post",
            "skip github",
            "no github api",
            "skip publication",
        )
    )


def test_resolve_review_deferred_payload_keeps_review_flag_filtering() -> None:
    text = _skill_text("resolve-review")
    section = _publication_section(text)
    assert "deferred_observations" in section
    assert "REVIEW-FLAG" in section
    assert "severity" in section.lower()
    assert "dimension" in section.lower()
    assert "info" in section.lower()
    assert any(word in section.lower() for word in ("skip", "exclude", "filter"))


def test_resolve_review_handles_null_line_file_level_threads() -> None:
    text = _skill_text("resolve-review")
    step2_start = text.find("### Step 2")
    step3_start = text.find("### Step 3")
    assert step2_start != -1
    assert step3_start != -1
    step2_section = text[step2_start:step3_start].lower()
    assert "null" in step2_section
    assert "file-level" in step2_section or "file level" in step2_section


def test_resolve_research_review_handles_null_line_file_level_threads() -> None:
    text = _skill_text("resolve-research-review")
    step3_start = text.find("### Step 3")
    step4_start = text.find("### Step 4")
    assert step3_start != -1
    assert step4_start != -1
    step3_section = text[step3_start:step4_start].lower()
    assert "null" in step3_section
    assert "file-level" in step3_section or "file level" in step3_section
