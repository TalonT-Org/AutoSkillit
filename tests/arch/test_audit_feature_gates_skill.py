"""Structural integrity tests for the audit-feature-gates skill."""

import re
from pathlib import Path

import yaml

_SKILLS_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "skills_extended"
_SKILL_DIR = _SKILLS_ROOT / "audit-feature-gates"
_SKILL_FILE = _SKILL_DIR / "SKILL.md"
_RECIPES_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes"
_FULL_AUDIT = _RECIPES_ROOT / "full-audit.yaml"
_VALIDATE_SKILL = _SKILLS_ROOT / "validate-audit" / "SKILL.md"


def test_audit_feature_gates_skill_dir_exists():
    assert _SKILL_DIR.is_dir(), "skills_extended/audit-feature-gates/ directory must exist"


def test_audit_feature_gates_skill_md_exists():
    assert _SKILL_FILE.is_file(), "audit-feature-gates/SKILL.md must exist"


def test_audit_feature_gates_skill_has_audit_category():
    source = _SKILL_FILE.read_text()
    parts = source.split("---", 2)
    assert len(parts) >= 3, "SKILL.md must have YAML frontmatter"
    fm = yaml.safe_load(parts[1])
    assert isinstance(fm, dict), "SKILL.md frontmatter must parse to a dict"
    assert "audit" in fm.get("categories", []), "audit-feature-gates must have categories: [audit]"


def test_audit_feature_gates_skill_has_readonly_hook():
    source = _SKILL_FILE.read_text()
    assert "Read-only audit" in source or "no code changes" in source.lower(), (
        "Skill must declare read-only hook in frontmatter"
    )


def test_audit_feature_gates_six_dimensions_present():
    source = _SKILL_FILE.read_text()
    dimensions = [
        "Config Projection",
        "Import Chain",
        "Runtime Gate",
        "Tool/Skill Tag",
        "Boundary Coupling",
        "Test Marker",
    ]
    for dim in dimensions:
        assert dim in source, f"Skill must document dimension: {dim}"


def test_audit_feature_gates_output_path_declared():
    source = _SKILL_FILE.read_text()
    assert "audit-feature-gates" in source and "feature_gate_audit" in source, (
        "Skill must declare its output path pattern"
    )


def test_full_audit_yaml_has_four_audit_chains():
    source = _FULL_AUDIT.read_text()
    # Count distinct audit-* skill references in the run_audits step context
    audit_skill_calls = re.findall(
        r"audit-feature-gates|audit-tests|audit-cohesion|audit-arch", source
    )
    unique = set(audit_skill_calls)
    assert "audit-feature-gates" in unique, "full-audit.yaml must reference audit-feature-gates"
    assert len(unique) >= 4, f"full-audit.yaml must have ≥4 audit skill references, got: {unique}"


def test_full_audit_yaml_summary_mentions_four_audits():
    source = _FULL_AUDIT.read_text()
    assert "audit×4" in source or "4 audit" in source.lower() or "audit-feature-gates" in source, (
        "full-audit.yaml summary must reflect 4 audits"
    )


def test_validate_audit_recognizes_feature_gate_format():
    source = _VALIDATE_SKILL.read_text()
    assert "Feature Gate Audit" in source, (
        "validate-audit SKILL.md must contain 'Feature Gate Audit' abort-error string"
    )


# ---------------------------------------------------------------------------
# GAP 2a: audit-feature-gates format standardization tests
# ---------------------------------------------------------------------------


def test_audit_feature_gates_finding_id_scheme():
    """Skill must document the FG-D{dim}-{seq} per-finding ID scheme."""
    source = _SKILL_FILE.read_text()
    assert "FG-D" in source, (
        "audit-feature-gates must define a per-finding ID scheme (e.g. FG-D2-01) "
        "so validate-audit can address individual findings"
    )


def test_audit_feature_gates_block_warn_file_line_mandate():
    """BLOCK and WARN findings must require file:line references."""
    source = _SKILL_FILE.read_text()
    lower = source.lower()
    has_file_line_mandate = (
        "file:line" in lower and ("block" in lower or "warn" in lower)
    )
    assert has_file_line_mandate, (
        "audit-feature-gates must mandate file:line references on BLOCK and WARN findings "
        "so validate-audit subagents can verify them against actual code"
    )


def test_audit_feature_gates_remediation_checklist_per_dimension():
    """Each dimension must have a Remediation Checklist in the output report."""
    source = _SKILL_FILE.read_text()
    assert "Remediation Checklist" in source, (
        "audit-feature-gates report format must include a Remediation Checklist "
        "per dimension, matching the pattern established by audit-cohesion"
    )
