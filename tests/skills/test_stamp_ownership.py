"""Stamp ownership tests: only dry-walkthrough may write the verification stamp.

Validates that:
- audit-impl does not instruct writing the dry-walkthrough stamp
- No skill besides dry-walkthrough contains write-instructions for the stamp
- The audit-impl remediation template does not contain the stamp
- make-plan contains a stamp-stripping instruction
"""

import re

import pytest

from autoskillit.core import DRY_WALKTHROUGH_VERIFIED_MARKER as STAMP
from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.small]


@pytest.fixture(scope="module")
def audit_impl_text() -> str:
    path = pkg_root() / "skills_extended" / "audit-impl" / "SKILL.md"
    assert path.exists(), f"SKILL.md not found at {path}"
    return path.read_text()


@pytest.fixture(scope="module")
def make_plan_text() -> str:
    path = pkg_root() / "skills_extended" / "make-plan" / "SKILL.md"
    assert path.exists(), f"SKILL.md not found at {path}"
    return path.read_text()


def test_audit_impl_no_stamp_write_instruction(audit_impl_text: str) -> None:
    """audit-impl SKILL.md must not instruct writing the dry-walkthrough stamp."""
    for i, line in enumerate(audit_impl_text.splitlines(), 1):
        if STAMP in line and "Write" in line:
            pytest.fail(
                f"audit-impl SKILL.md line {i} instructs writing the stamp "
                f"owned by dry-walkthrough: {line.strip()!r}"
            )


def test_audit_impl_template_no_stamp(audit_impl_text: str) -> None:
    """The remediation file template in audit-impl must not contain the stamp."""
    template_marker = "Generate `{{AUTOSKILLIT_TEMP}}/audit-impl/remediation_"
    idx = audit_impl_text.find(template_marker)
    if idx == -1:
        pytest.skip("Remediation template marker not found in audit-impl SKILL.md")

    fence_start = audit_impl_text.find("```markdown", idx)
    if fence_start == -1:
        pytest.skip("No markdown fence found after remediation template marker")
    fence_end = audit_impl_text.find("```", fence_start + len("```markdown"))
    if fence_end == -1:
        pytest.skip("Unclosed markdown fence in remediation template")

    template = audit_impl_text[fence_start:fence_end]
    assert STAMP not in template, (
        f"The remediation file template in audit-impl SKILL.md contains the "
        f"dry-walkthrough stamp '{STAMP}' — audit-impl must not pre-seed this stamp"
    )


def _is_write_instruction(line: str, lines: list[str], line_idx: int) -> bool:
    """Classify whether a stamp occurrence is a write-instruction vs read-check."""
    stripped = line.strip()

    if f"`{STAMP}`" in line:
        return False

    if re.search(r"^-\s+Write\b", stripped):
        return True

    for back in range(line_idx - 1, max(line_idx - 15, -1), -1):
        heading = lines[back].strip()
        if heading.startswith("#"):
            if re.search(r"\b(Mark|Write|Add|Insert)\b", heading, re.IGNORECASE):
                return True
            if re.search(
                r"\b(Verify|Check|Read back|Confirm|Validate)\b",
                heading,
                re.IGNORECASE,
            ):
                return False
            break

    if re.search(r"^-\s+.*\b(Write|Add|Insert|Emit)\b", stripped, re.IGNORECASE):
        if f"`{STAMP}`" not in line:
            return True

    return False


def test_stamp_write_ownership_exclusive_to_dry_walkthrough() -> None:
    """Only dry-walkthrough may contain write-instructions for the stamp."""
    violations: list[str] = []

    for base_dir in (pkg_root() / "skills_extended", pkg_root() / "skills"):
        if not base_dir.is_dir():
            continue
        for skill_md in sorted(base_dir.rglob("SKILL.md")):
            text = skill_md.read_text()
            if STAMP not in text:
                continue

            skill_name = skill_md.parent.name
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if STAMP not in line:
                    continue
                if _is_write_instruction(line, lines, i):
                    if skill_name != "dry-walkthrough":
                        violations.append(f"{skill_name}/SKILL.md line {i + 1}: {line.strip()!r}")

    assert not violations, (
        f"Stamp '{STAMP}' write-instructions found outside dry-walkthrough:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_make_plan_has_stamp_stripping_instruction(make_plan_text: str) -> None:
    """make-plan SKILL.md must instruct not propagating the dry-walkthrough stamp."""
    has_instruction = (
        "propagate" in make_plan_text.lower() and "stamp" in make_plan_text.lower()
    ) or (STAMP in make_plan_text and "never" in make_plan_text.lower())

    assert has_instruction, (
        "make-plan SKILL.md must contain an instruction about not propagating "
        "the dry-walkthrough stamp from input files to plan output"
    )
