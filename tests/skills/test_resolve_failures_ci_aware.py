"""Contract guards for resolve-failures CI-awareness: verdict decision tree.

Verifies that resolve-failures/SKILL.md contains the verdict decision tree
required by Part B of the parallel pipeline deadlock remediation.

These are structural AST-style guards — they check the SKILL.md prose directly,
ensuring that the skill will emit the correct verdict tokens at runtime.

Scenarios:
  A: failure_subtype=flaky/timing_race + local tests green → verdict=flake_suspected
  B: failure_subtype=deterministic + local tests green (CI red) → verdict=ci_only_failure
  C: fix applied, local tests fail then pass → verdict=real_fix + fixes_applied>=1
"""

from __future__ import annotations

import re

import pytest

from autoskillit.core import pkg_root

_SKILL_MD = pkg_root() / "skills_extended" / "resolve-failures" / "SKILL.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    assert _SKILL_MD.exists(), f"resolve-failures SKILL.md not found at {_SKILL_MD}"
    return _SKILL_MD.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Step 2a: Read CI Context
# ---------------------------------------------------------------------------


def test_skill_reads_failure_subtype_from_diagnosis_path(skill_text: str) -> None:
    """Skill must instruct reading failure_subtype from diagnosis_path input."""
    assert "failure_subtype" in skill_text, (
        "resolve-failures SKILL.md must reference 'failure_subtype' — "
        "the skill must read CI failure classification from the diagnosis file"
    )


def test_skill_references_diagnosis_path_input(skill_text: str) -> None:
    """Skill must reference diagnosis_path as an input to read CI context."""
    assert "diagnosis_path" in skill_text, (
        "resolve-failures SKILL.md must reference 'diagnosis_path' input — "
        "the skill must consume the CI diagnosis from diagnose-ci"
    )


# ---------------------------------------------------------------------------
# Step 2c: Verdict Decision Tree
# ---------------------------------------------------------------------------


def test_skill_contains_verdict_decision_tree(skill_text: str) -> None:
    """Skill must contain a verdict decision tree section."""
    assert "verdict" in skill_text.lower(), (
        "resolve-failures SKILL.md must contain a verdict decision tree"
    )
    assert any(
        phrase in skill_text
        for phrase in ("Verdict Decision Tree", "verdict decision tree", "Verdict decision")
    ), "resolve-failures SKILL.md must contain a 'Verdict Decision Tree' section"


def test_skill_maps_flaky_to_flake_suspected(skill_text: str) -> None:
    """Scenario A: flaky/timing_race + local green → flake_suspected."""
    assert "flake_suspected" in skill_text, (
        "resolve-failures SKILL.md must include 'flake_suspected' verdict value "
        "for the flaky/timing_race + local-green scenario"
    )
    # The decision tree must map flaky or timing_race subtypes to flake_suspected
    assert any(subtype in skill_text for subtype in ("flaky", "timing_race")), (
        "resolve-failures SKILL.md must reference 'flaky' or 'timing_race' subtypes "
        "in the verdict decision tree"
    )


def test_skill_maps_deterministic_green_to_ci_only_failure(skill_text: str) -> None:
    """Scenario B: deterministic + local tests green + NO FIX APPLIED → ci_only_failure."""
    assert "ci_only_failure" in skill_text, (
        "resolve-failures SKILL.md must include 'ci_only_failure' verdict value "
        "for the deterministic subtype + local-green scenario"
    )
    assert "deterministic" in skill_text, (
        "resolve-failures SKILL.md must reference 'deterministic' subtype in the "
        "verdict decision tree"
    )


def test_skill_maps_real_fix_correctly(skill_text: str) -> None:
    """Scenario C: fix applied, local tests pass after fix → real_fix."""
    assert "real_fix" in skill_text, (
        "resolve-failures SKILL.md must include 'real_fix' verdict value "
        "for the scenario where fixes are successfully applied"
    )


def test_skill_includes_already_green_verdict(skill_text: str) -> None:
    """Skill must include 'already_green' verdict for the rebase re-entry path."""
    assert "already_green" in skill_text, (
        "resolve-failures SKILL.md must include 'already_green' verdict value "
        "for the pre_resolve_rebase re-entry path"
    )


# ---------------------------------------------------------------------------
# Output tokens
# ---------------------------------------------------------------------------


def test_skill_emits_verdict_token(skill_text: str) -> None:
    """Skill must emit 'verdict = {value}' in the output tokens block."""
    assert re.search(r"verdict\s*=\s*\{", skill_text), (
        "resolve-failures SKILL.md must emit 'verdict = {value}' in the "
        "structured output tokens block"
    )


def test_skill_still_emits_fixes_applied_token(skill_text: str) -> None:
    """Skill must still emit 'fixes_applied = {N}' alongside verdict."""
    assert re.search(r"fixes_applied\s*=\s*\{", skill_text), (
        "resolve-failures SKILL.md must still emit 'fixes_applied = {N}' "
        "alongside the verdict token"
    )


def test_skill_verdict_covers_all_required_values(skill_text: str) -> None:
    """All four verdict values must appear in the SKILL.md."""
    required = {"real_fix", "already_green", "flake_suspected", "ci_only_failure"}
    missing = {v for v in required if v not in skill_text}
    assert not missing, f"resolve-failures SKILL.md is missing these verdict values: {missing}"


# ---------------------------------------------------------------------------
# Verdict decision table row-level mapping tests
# ---------------------------------------------------------------------------


def _find_table_row_verdict(skill_text: str, subtype: str) -> str | None:
    """Find the verdict assigned to a given failure_subtype in the decision table.

    Scans the markdown table in the Verdict Decision Tree section for a row
    containing the subtype, and extracts the verdict value from that row.
    """
    in_table = False
    for line in skill_text.splitlines():
        if "Local result" in line and "failure_subtype" in line and "Verdict" in line:
            in_table = True
            continue
        if in_table and line.strip().startswith("|---"):
            continue
        if in_table and "|" in line:
            cells = [c.strip() for c in line.split("|")]
            # cells[0] is empty (before first |), cells[-1] is empty (after last |)
            if len(cells) < 4:
                continue
            subtype_cell = cells[2]  # failure_subtype column
            verdict_cell = cells[3]  # Verdict column
            if subtype in subtype_cell:
                # Extract verdict token (backtick-wrapped)
                match = re.search(r"`(\w+)`", verdict_cell)
                if match:
                    return match.group(1)
        elif in_table and line.strip() == "":
            break
    return None


def test_unknown_subtype_maps_to_flake_suspected_not_ci_only(skill_text: str) -> None:
    """The 'unknown' failure_subtype must map to flake_suspected, not ci_only_failure."""
    verdict = _find_table_row_verdict(skill_text, "unknown")
    assert verdict is not None, (
        "resolve-failures SKILL.md verdict decision table must contain a row for 'unknown'"
    )
    assert verdict == "flake_suspected", (
        f"'unknown' subtype must map to 'flake_suspected', got '{verdict}'. "
        "Ambiguous subtypes should not be routed to abort."
    )


def test_env_subtype_maps_to_flake_suspected_not_ci_only(skill_text: str) -> None:
    """The 'env' failure_subtype must map to flake_suspected, not ci_only_failure."""
    verdict = _find_table_row_verdict(skill_text, "env")
    assert verdict is not None, (
        "resolve-failures SKILL.md verdict decision table must contain a row for 'env'"
    )
    assert verdict == "flake_suspected", (
        f"'env' subtype must map to 'flake_suspected', got '{verdict}'. "
        "Ambiguous subtypes should not be routed to abort."
    )


# ---------------------------------------------------------------------------
# Post-fix override guards (REQ-RF-001, REQ-RF-002)
# ---------------------------------------------------------------------------


def test_skill_fix_applied_overrides_to_real_fix(skill_text: str) -> None:
    """When a fix is committed and tests pass, verdict MUST be real_fix regardless of subtype."""
    assert re.search(
        r"fix.*(commit|applied).*verdict.*real_fix", skill_text, re.IGNORECASE
    ) or re.search(r"real_fix.*regardless.*failure_subtype", skill_text, re.IGNORECASE), (
        "resolve-failures SKILL.md must contain an explicit override rule: "
        "when a fix is committed and tests pass, verdict is always real_fix "
        "regardless of failure_subtype"
    )


def test_step2d_table_scoped_to_no_fix_path(skill_text: str) -> None:
    """Step 2d verdict table must only apply when no fix was applied."""
    # Capture the full Step 2d section (from header to next ### heading or EOF)
    table_section_match = re.search(
        r"Step 2d.*?(?=\n### |\Z)",
        skill_text,
        re.DOTALL,
    )
    assert table_section_match is not None, "Step 2d section must exist"
    table_section = table_section_match.group(0)
    assert any(
        phrase in table_section.lower()
        for phrase in (
            "no fix applied",
            "no fix was applied",
            "fixes_applied == 0",
            "without entering step 3",
        )
    ), (
        "Step 2d verdict decision table must explicitly state it applies only "
        "when no fix was applied — prevents LLM from re-evaluating after Step 3"
    )


def test_ci_only_failure_requires_no_fix_applied(skill_text: str) -> None:
    """ci_only_failure must only be emittable when no fix was applied."""
    assert re.search(
        r"ci_only_failure.*(no fix|never.*fix.*applied|fixes_applied.*0|without.*commit)",
        skill_text,
        re.IGNORECASE | re.DOTALL,
    ) or re.search(
        r"(no fix|never.*fix|fixes_applied.*0).*ci_only_failure",
        skill_text,
        re.IGNORECASE | re.DOTALL,
    ), (
        "resolve-failures SKILL.md must explicitly state that ci_only_failure "
        "is only emitted when no fix was applied"
    )


def test_step3_green_always_yields_real_fix(skill_text: str) -> None:
    """Step 3 fix loop: green after fix MUST yield real_fix, never re-evaluates Step 2d."""
    step3_match = re.search(
        r"Step 3.*?Step 4",
        skill_text,
        re.DOTALL,
    )
    assert step3_match is not None, "Step 3 section must exist and precede Step 4"
    step3_text = step3_match.group(0)
    assert "real_fix" in step3_text, "Step 3 must directly assign verdict = real_fix on green exit"
    assert "step 2d" not in step3_text.lower() or "do not" in step3_text.lower(), (
        "Step 3 must not redirect back to Step 2d for verdict evaluation"
    )
