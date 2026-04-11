"""Tests for project-local audit skill content.

Verifies that .claude/skills/audit-arch, audit-cohesion, audit-tests, and validate-audit
have the required frontmatter, temp path placeholders, exception whitelists, and quality
mechanisms documented in issue #723.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"


def _read_skill(skill_name: str) -> str:
    path = SKILLS_DIR / skill_name / "SKILL.md"
    return path.read_text(encoding="utf-8")


def _parse_frontmatter(content: str) -> dict:
    """Extract and parse YAML frontmatter from a markdown file."""
    if not content.startswith("---"):
        return {}
    try:
        end = content.index("---", 3)
    except ValueError as exc:
        raise ValueError("Unclosed frontmatter delimiter in SKILL.md content") from exc
    fm_text = content[3:end].strip()
    return yaml.safe_load(fm_text) or {}


# ---------------------------------------------------------------------------
# Test 1: categories: [audit] in frontmatter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skill_name", ["audit-arch", "audit-cohesion", "audit-tests"])
def test_project_local_audit_skills_have_audit_category(skill_name: str) -> None:
    content = _read_skill(skill_name)
    fm = _parse_frontmatter(content)
    assert "categories" in fm, f"{skill_name}/SKILL.md frontmatter missing 'categories'"
    assert isinstance(fm["categories"], list), f"{skill_name}/SKILL.md 'categories' must be a list"
    assert "audit" in fm["categories"], (
        f"{skill_name}/SKILL.md 'categories' must contain 'audit', got: {fm['categories']}"
    )


# ---------------------------------------------------------------------------
# Test 2: {{AUTOSKILLIT_TEMP}} placeholder — no literal temp/audit- paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skill_name", ["audit-arch", "audit-cohesion", "audit-tests"])
def test_project_local_audit_skills_use_autoskillit_temp_placeholder(skill_name: str) -> None:
    content = _read_skill(skill_name)

    # Strip fenced code blocks so we don't false-positive on examples
    stripped = re.sub(r"```.*?```", "", content, flags=re.DOTALL)

    # Must not contain literal temp/audit- path
    literal_pattern = f"temp/{skill_name}/"
    assert literal_pattern not in stripped, (
        f"{skill_name}/SKILL.md still contains literal '{literal_pattern}' "
        f"outside fenced blocks — replace with {{{{AUTOSKILLIT_TEMP}}}}/{skill_name}/"
    )

    # Must contain the placeholder form at least once
    placeholder = "{{AUTOSKILLIT_TEMP}}"
    assert placeholder in content, (
        f"{skill_name}/SKILL.md does not contain '{placeholder}' — "
        f"add {{{{AUTOSKILLIT_TEMP}}}}/{skill_name}/ to the report path"
    )


# ---------------------------------------------------------------------------
# Test 3: P12 composition-boundary tiers
# ---------------------------------------------------------------------------


def test_audit_arch_p12_has_composition_boundary_tiers() -> None:
    content = _read_skill("audit-arch")
    assert "Intra-package Default*" in content, (
        "audit-arch/SKILL.md P12 missing 'Intra-package Default*' tier marker"
    )
    assert "L3 CLI" in content, "audit-arch/SKILL.md P12 missing 'L3 CLI' tier marker"
    assert "DI convenience default" in content, (
        "audit-arch/SKILL.md P12 missing 'DI convenience default' tier marker"
    )


# ---------------------------------------------------------------------------
# Test 4: General Exception IDs GE-1..GE-17 in audit-arch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ge_id", ["GE-1", "GE-2", "GE-7", "GE-9", "GE-13", "GE-16", "GE-17"])
def test_audit_arch_general_exceptions_present(ge_id: str) -> None:
    content = _read_skill("audit-arch")
    assert re.search(r"\*\*" + re.escape(ge_id) + r"\*\*", content), (
        f"audit-arch/SKILL.md missing exception ID '{ge_id}' in Exception Whitelist"
    )


# ---------------------------------------------------------------------------
# Test 5: Project-Specific Exception IDs PS-1..PS-8 in audit-arch
# ---------------------------------------------------------------------------


# PS-3/5/6 are cohesion-specific; tested in test_audit_cohesion_general_exceptions_present
@pytest.mark.parametrize("ps_id", ["PS-1", "PS-2", "PS-4", "PS-7", "PS-8"])
def test_audit_arch_project_specific_exceptions_present(ps_id: str) -> None:
    content = _read_skill("audit-arch")
    assert ps_id in content, f"audit-arch/SKILL.md missing project-specific exception '{ps_id}'"


# ---------------------------------------------------------------------------
# Test 6: Pre-Flight Verification Checklist heading + five row labels
# ---------------------------------------------------------------------------


def test_audit_arch_has_pre_flight_checklist() -> None:
    content = _read_skill("audit-arch")
    assert "Pre-Flight Verification Checklist" in content, (
        "audit-arch/SKILL.md missing heading 'Pre-Flight Verification Checklist'"
    )
    for row_label in [
        "Missing export",
        "Missing decorator",
        "Enforcement gap",
        "Code duplication",
        "Misplaced file",
    ]:
        assert row_label in content, (
            f"audit-arch/SKILL.md Pre-Flight Checklist missing row label '{row_label}'"
        )


# ---------------------------------------------------------------------------
# Test 7: Self-Validation Pass + four sub-checks
# ---------------------------------------------------------------------------


def test_audit_arch_has_self_validation_pass() -> None:
    content = _read_skill("audit-arch")
    assert "Self-Validation Pass" in content, (
        "audit-arch/SKILL.md missing 'Self-Validation Pass' section"
    )
    assert "HIGH/CRITICAL re-read" in content, (
        "audit-arch/SKILL.md Self-Validation Pass missing 'HIGH/CRITICAL re-read' sub-check"
    )
    assert "Concrete-class check" in content, (
        "audit-arch/SKILL.md Self-Validation Pass missing 'Concrete-class check' sub-check"
    )
    assert "Enforcement-search confirmation" in content, (
        "audit-arch/SKILL.md Self-Validation Pass missing 'Enforcement-search confirmation'"
    )
    # Either CONFIRMED or REVISED must appear (internal validation note markers)
    assert "CONFIRMED" in content or "REVISED" in content, (
        "audit-arch/SKILL.md Self-Validation Pass missing 'CONFIRMED' or 'REVISED' note marker"
    )


# ---------------------------------------------------------------------------
# Test 8: Principle Suggestion constraints
# ---------------------------------------------------------------------------


def test_audit_arch_principle_suggestion_constraints() -> None:
    content = _read_skill("audit-arch")
    assert "ONE dedicated subagent" in content, (
        "audit-arch/SKILL.md Principle Suggestion missing 'ONE dedicated subagent' constraint"
    )
    assert "At most ONE" in content, (
        "audit-arch/SKILL.md Principle Suggestion missing 'at most ONE' constraint"
    )
    assert "MUST NOT generate findings" in content, (
        "audit-arch/SKILL.md Principle Suggestion missing 'MUST NOT generate findings' constraint"
    )
    assert "MUST NOT be enforced" in content, (
        "audit-arch/SKILL.md Principle Suggestion missing 'MUST NOT be enforced' constraint"
    )
    assert "MUST NOT invent or enforce principles" in content, (
        "audit-arch/SKILL.md per-principle subagent instructions missing "
        "'MUST NOT invent or enforce principles' phrase"
    )


# ---------------------------------------------------------------------------
# Test 9: Cohesion exception IDs + C10 file-verification rule
# ---------------------------------------------------------------------------


def test_audit_cohesion_general_exceptions_present() -> None:
    content = _read_skill("audit-cohesion")
    for ge_id in ["GE-10", "GE-11", "GE-12", "GE-13", "GE-15"]:
        assert ge_id in content, f"audit-cohesion/SKILL.md missing exception ID '{ge_id}'"
    for ps_id in ["PS-3", "PS-5", "PS-6"]:
        assert ps_id in content, (
            f"audit-cohesion/SKILL.md missing project-specific exception '{ps_id}'"
        )
    assert "read the actual file at the cited path" in content, (
        "audit-cohesion/SKILL.md missing C10 file-verification rule phrase "
        "'read the actual file at the cited path'"
    )


# ---------------------------------------------------------------------------
# Test 10: validate-audit project-local override exists with required content
# ---------------------------------------------------------------------------


def test_validate_audit_project_local_override_exists() -> None:
    path = SKILLS_DIR / "validate-audit" / "SKILL.md"
    assert path.exists(), ".claude/skills/validate-audit/SKILL.md does not exist"
    content = path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(content)
    assert "categories" in fm, "validate-audit/SKILL.md frontmatter missing 'categories'"
    assert "audit" in fm.get("categories", []), (
        "validate-audit/SKILL.md 'categories' must contain 'audit'"
    )
    assert "Known Project Exceptions" in content, (
        "validate-audit/SKILL.md missing '## Known Project Exceptions' section"
    )
    for ps_id in [f"PS-{i}" for i in range(1, 9)]:
        assert ps_id in content, (
            f"validate-audit/SKILL.md Known Project Exceptions table missing '{ps_id}'"
        )


# ---------------------------------------------------------------------------
# Test 11: validate-audit uses {{AUTOSKILLIT_TEMP}} placeholder
# ---------------------------------------------------------------------------


def test_validate_audit_uses_autoskillit_temp_placeholder() -> None:
    path = SKILLS_DIR / "validate-audit" / "SKILL.md"
    assert path.exists(), ".claude/skills/validate-audit/SKILL.md does not exist"
    content = path.read_text(encoding="utf-8")
    stripped = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
    assert "temp/validate-audit/" not in stripped, (
        "validate-audit/SKILL.md contains literal 'temp/validate-audit/' outside fenced blocks"
    )
    assert "{{AUTOSKILLIT_TEMP}}" in content, (
        "validate-audit/SKILL.md does not contain '{{AUTOSKILLIT_TEMP}}' placeholder"
    )
