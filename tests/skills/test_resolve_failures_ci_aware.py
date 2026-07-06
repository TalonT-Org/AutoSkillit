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


pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


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
    Returns the FIRST matching row's verdict.
    """
    in_table = False
    subtype_col = -1
    verdict_col = -1
    for line in skill_text.splitlines():
        if "Local result" in line and "failure_subtype" in line and "Verdict" in line:
            in_table = True
            header_cells = [c.strip() for c in line.split("|")]
            for i, cell in enumerate(header_cells):
                if "failure_subtype" in cell:
                    subtype_col = i
                if "Verdict" in cell:
                    verdict_col = i
            continue
        if in_table and line.strip().startswith("|---"):
            continue
        if in_table and "|" in line:
            cells = [c.strip() for c in line.split("|")]
            if len(cells) <= max(subtype_col, verdict_col):
                continue
            subtype_cell = cells[subtype_col]
            verdict_cell = cells[verdict_col]
            if subtype in subtype_cell:
                match = re.search(r"`(\w+)`", verdict_cell)
                if match:
                    return match.group(1)
        elif in_table and line.strip() == "":
            break
    return None


def _find_all_table_row_verdicts(skill_text: str, subtype: str) -> list[str]:
    """Find ALL verdicts for rows matching a given failure_subtype."""
    verdicts: list[str] = []
    in_table = False
    subtype_col = -1
    verdict_col = -1
    for line in skill_text.splitlines():
        if "Local result" in line and "failure_subtype" in line and "Verdict" in line:
            in_table = True
            header_cells = [c.strip() for c in line.split("|")]
            for i, cell in enumerate(header_cells):
                if "failure_subtype" in cell:
                    subtype_col = i
                if "Verdict" in cell:
                    verdict_col = i
            continue
        if in_table and line.strip().startswith("|---"):
            continue
        if in_table and "|" in line:
            cells = [c.strip() for c in line.split("|")]
            if len(cells) <= max(subtype_col, verdict_col):
                continue
            subtype_cell = cells[subtype_col]
            verdict_cell = cells[verdict_col]
            if subtype in subtype_cell:
                match = re.search(r"`(\w+)`", verdict_cell)
                if match:
                    verdicts.append(match.group(1))
        elif in_table and line.strip() == "":
            break
    return verdicts


def test_unknown_subtype_maps_to_flake_suspected_not_ci_only(skill_text: str) -> None:
    """The 'unknown' failure_subtype must map to flake_suspected when is_fixable=true,
    and ci_only_failure when is_fixable=false."""
    verdicts = _find_all_table_row_verdicts(skill_text, "unknown")
    assert len(verdicts) >= 2, (
        f"resolve-failures SKILL.md verdict decision table must contain at least 2 rows "
        f"for 'unknown' (split by is_fixable), got {len(verdicts)} row(s)"
    )
    assert "flake_suspected" in verdicts, (
        f"'unknown' subtype must have a 'flake_suspected' row (for is_fixable=true), "
        f"got verdicts: {verdicts}"
    )
    assert "ci_only_failure" in verdicts, (
        f"'unknown' subtype must have a 'ci_only_failure' row (for is_fixable=false), "
        f"got verdicts: {verdicts}"
    )


def test_env_subtype_maps_to_flake_suspected_not_ci_only(skill_text: str) -> None:
    """The 'env' failure_subtype must map to flake_suspected when is_fixable=true,
    and ci_only_failure when is_fixable=false."""
    verdicts = _find_all_table_row_verdicts(skill_text, "env")
    assert len(verdicts) >= 2, (
        f"resolve-failures SKILL.md verdict decision table must contain at least 2 rows "
        f"for 'env' (split by is_fixable), got {len(verdicts)} row(s)"
    )
    assert "flake_suspected" in verdicts, (
        f"'env' subtype must have a 'flake_suspected' row (for is_fixable=true), "
        f"got verdicts: {verdicts}"
    )
    assert "ci_only_failure" in verdicts, (
        f"'env' subtype must have a 'ci_only_failure' row (for is_fixable=false), "
        f"got verdicts: {verdicts}"
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


def _extract_invariant_paragraph(skill_text: str) -> str:
    """Extract the invariant paragraph from SKILL.md.

    Scoped to the single paragraph starting with **Invariant:** — does NOT
    use re.DOTALL across the full document (which caused the 476059fbb
    weakening to evade detection).
    """
    lines = skill_text.splitlines()
    in_paragraph = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not in_paragraph:
            if stripped.startswith("**Invariant:**") or stripped.startswith("> **Invariant:**"):
                in_paragraph = True
                collected.append(stripped)
            continue
        if stripped == "":
            break
        if stripped.startswith("### "):
            break
        collected.append(stripped)
    assert collected, "resolve-failures SKILL.md must contain an **Invariant:** paragraph"
    return " ".join(collected)


def test_ci_only_failure_invariant_paragraph_exists(skill_text: str) -> None:
    """The invariant paragraph must exist with a clear anchor."""
    invariant = _extract_invariant_paragraph(skill_text)
    assert "ci_only_failure" in invariant, "Invariant paragraph must reference ci_only_failure"


def test_ci_only_failure_invariant_requires_no_fix(skill_text: str) -> None:
    """Invariant paragraph: ci_only_failure must require no-fix-applied.

    Scoped to the invariant paragraph (not full-DOTALL across SKILL.md).
    The weakening language from 476059fbb would appear WITHIN this paragraph.
    """
    invariant = _extract_invariant_paragraph(skill_text)
    assert re.search(
        r"ci_only_failure.*?(?:NEVER|must\s+not|is\s+not\s+emitted)",
        invariant,
        re.IGNORECASE,
    ), (
        "Invariant paragraph must state ci_only_failure is NEVER/must not/is not emitted "
        "when fixes_applied >= 1"
    )


def test_ci_only_failure_invariant_not_weakened(skill_text: str) -> None:
    """Invariant paragraph must not contain the weakening language from 476059fbb.

    The weakening said: 'ci_only_failure MAY be emitted when fixes_applied >= 1'.
    This test catches any attempt to re-introduce that exact softening.
    """
    invariant = _extract_invariant_paragraph(skill_text)
    forbidden_phrases = (
        "MAY be emitted when fixes_applied >= 1",
        "MAY be emitted when fixes_applied>=1",
        "may be emitted when fixes_applied",
    )
    for phrase in forbidden_phrases:
        assert phrase not in invariant, (
            f"Invariant paragraph must not contain weakening language: {phrase!r}. "
            f"The invariant was weakened in 476059fbb; this test prevents regression."
        )


def test_ci_only_failure_hard_invariant_pinned(skill_text: str) -> None:
    """Pin the hard invariant: ci_only_failure NEVER when fixes_applied >= 1."""
    assert re.search(
        r"\*\*Invariant:\*\*.*ci_only_failure.*NEVER.*fixes_applied\s*>=\s*1",
        skill_text,
    ), "Invariant paragraph must contain 'ci_only_failure NEVER ... fixes_applied >= 1'"


def test_step3_green_always_yields_real_fix(skill_text: str) -> None:
    """Step 3 fix loop: green after fix MUST yield real_fix, never re-evaluates Step 2d."""
    step3_match = re.search(
        r"### Step 3.*?(?=\n### Step [45]|\Z)",
        skill_text,
        re.DOTALL,
    )
    assert step3_match is not None, "### Step 3 section must exist in SKILL.md"
    step3_text = step3_match.group(0)
    assert "real_fix" in step3_text, "Step 3 must directly assign verdict = real_fix on green exit"
    assert "step 2d" not in step3_text.lower() or "do not" in step3_text.lower(), (
        "Step 3 must not redirect back to Step 2d for verdict evaluation"
    )


def test_step3_excludes_ci_only_failure(skill_text: str) -> None:
    """Step 3 (fix loop) must NOT reference ci_only_failure.

    ci_only_failure applies ONLY on the no-fix path (Step 2d). If it appears
    in Step 3, it suggests Step 3 might emit ci_only_failure after applying
    fixes — which violates the hard invariant.
    """
    step3_match = re.search(
        r"### Step 3.*?(?=\n### Step [45]|\Z)",
        skill_text,
        re.DOTALL,
    )
    assert step3_match is not None, "### Step 3 section must exist in SKILL.md"
    step3_text = step3_match.group(0)
    assert "ci_only_failure" not in step3_text, (
        "Step 3 (fix loop) must not reference ci_only_failure — that verdict is "
        "only valid on the no-fix path (Step 2d, fixes_applied == 0)"
    )


def test_invariant_paragraph_no_permissive_ci_only_failure(skill_text: str) -> None:
    """Invariant paragraph must not permit ci_only_failure with fixes_applied >= 1.

    Specifically: no phrase matching ci_only_failure ... (allowed|permitted|may|valid)
    in proximity to fixes_applied >= 1 or 'after a fix' or 'after the fix'.
    """
    invariant = _extract_invariant_paragraph(skill_text)
    permissive = re.search(
        r"ci_only_failure.*?(allowed|permitted|may\s+be\s+valid|may\s+be\s+emitted)",
        invariant,
        re.IGNORECASE,
    )
    after_fix = re.search(
        r"(after\s+(a|the)\s+fix|fixes_applied\s*>=\s*1|fixes_applied\s*>=\s*1\s+if)",
        invariant,
        re.IGNORECASE,
    )
    assert not (permissive and after_fix), (
        "Invariant paragraph must not permit ci_only_failure after fixes are applied. "
        f"Found permissive phrase: {permissive.group(0) if permissive else None!r} "
        f"near: {after_fix.group(0) if after_fix else None!r}"
    )
