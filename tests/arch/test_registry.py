"""Symbolic rule registry tests.

Tests the RuleDescriptor dataclass, RULES tuple, and Violation NamedTuple
that form the symbolic registry of architecture enforcement rules.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tests.arch._helpers import _scan
from tests.arch._rules import (
    _BROAD_EXCEPT_EXEMPT,
    _PRINT_EXEMPT,
    RULES,
    RuleDescriptor,
    Violation,
)

# ── Tests ─────────────────────────────────────────────────────────────────────


def test_rule_descriptor_is_frozen_dataclass() -> None:
    """REQ-SYMB-001: RuleDescriptor is a frozen dataclass with all required fields."""
    rd = RuleDescriptor(
        rule_id="ARCH-TEST",
        name="test-rule",
        lens="operational",
        description="A test rule.",
        rationale="Test rationale.",
        exemptions=frozenset(),
        severity="error",
        defense_standard=None,
        adr_ref=None,
    )
    assert rd.rule_id == "ARCH-TEST"
    assert rd.name == "test-rule"
    assert rd.lens == "operational"
    assert rd.exemptions == frozenset()
    assert rd.severity == "error"
    assert rd.defense_standard is None
    assert rd.adr_ref is None
    # Verify frozen (immutable)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        rd.rule_id = "MODIFIED"  # type: ignore[misc]


def test_rule_registry_completeness() -> None:
    """REQ-SYMB-006: RULES is complete, non-duplicated, and lens-valid."""
    _KNOWN_LENSES = frozenset(
        {
            "c4-container",
            "module-dependency",
            "process-flow",
            "concurrency",
            "state-lifecycle",
            "error-resilience",
            "security",
            "repository-access",
            "data-lineage",
            "scenarios",
            "deployment",
            "operational",
            "development",
        }
    )
    # (a) all rule_id values are unique
    rule_ids = [r.rule_id for r in RULES]
    assert len(rule_ids) == len(set(rule_ids)), f"Duplicate rule_ids: {rule_ids}"

    # (b) all lens values are from the 13-lens vocabulary
    for r in RULES:
        assert r.lens in _KNOWN_LENSES, (
            f"Rule {r.rule_id} has unknown lens {r.lens!r}. Known: {sorted(_KNOWN_LENSES)}"
        )

    # (c) count equals the number of distinct rules enforced by ArchitectureViolationVisitor
    assert len(RULES) == 7, (
        f"RULES has {len(RULES)} entries but visitor enforces 7 rules. "
        "Add a RuleDescriptor for every new visitor rule."
    )

    # (c cont.) exact set of IDs must match the visitor's rule set
    expected_ids = frozenset(
        {"ARCH-001", "ARCH-002", "ARCH-003", "ARCH-004", "ARCH-005", "ARCH-006", "ARCH-007"}
    )
    actual_ids = frozenset(rule_ids)
    assert actual_ids == expected_ids, (
        f"RULES ID mismatch. Missing: {expected_ids - actual_ids}. "
        f"Extra: {actual_ids - expected_ids}"
    )


def test_all_rules_have_defense_standard() -> None:
    """P13 LOW: every entry in RULES must declare a defense_standard.

    Prevents future @semantic_rule additions from silently omitting
    the defense_standard field, which would break audit-defense-standards
    traceability.
    """
    missing = [r.rule_id for r in RULES if r.defense_standard is None]
    assert not missing, (
        f"RULES entries missing defense_standard: {missing}. "
        "Every architectural rule must trace to a defense standard."
    )


def test_violation_has_rule_id_and_lens_fields() -> None:
    """REQ-SYMB-003: Violation gains rule_id and lens while preserving 4 original fields."""
    v = Violation(
        file=Path("x.py"),
        line=1,
        col=0,
        message="msg",
        rule_id="ARCH-001",
        lens="operational",
    )
    assert v.rule_id == "ARCH-001"
    assert v.lens == "operational"
    # Original 4 fields preserved
    assert v.file == Path("x.py")
    assert v.line == 1
    assert v.col == 0
    assert v.message == "msg"


def test_violation_rule_id_lens_default_to_empty(tmp_path: Path) -> None:
    """Violation with only 4 args has rule_id='' and lens='' (backward-compatible construction)."""
    v = Violation(file=tmp_path / "x.py", line=1, col=0, message="SyntaxError: bad")
    assert v.rule_id == ""
    assert v.lens == ""


def test_add_populates_rule_id_and_lens(tmp_path: Path) -> None:
    """REQ-SYMB-004: _add() creates Violations with rule_id and lens from the RuleDescriptor."""
    f = tmp_path / "bad.py"
    f.write_text("print('hello')\n")
    violations = _scan(f)
    print_violations = [v for v in violations if "print" in v.message]
    assert print_violations, "Expected a print() violation"
    v = print_violations[0]
    assert v.rule_id == "ARCH-001"
    assert v.lens == "operational"


def test_violation_str_includes_rule_and_lens_prefix(tmp_path: Path) -> None:
    """REQ-SYMB-005: str(Violation) includes [ARCH-XXX / lens] as the leading element."""
    f = tmp_path / "bad.py"
    f.write_text("print('hello')\n")
    violations = _scan(f)
    print_violations = [v for v in violations if "print" in v.message]
    assert print_violations
    s = str(print_violations[0])
    assert s.startswith("[ARCH-001 / operational"), (
        f"Expected '[ARCH-001 / operational...' prefix, got: {s!r}"
    )


def test_violation_str_includes_defense_standard_when_present(tmp_path: Path) -> None:
    """REQ-SYMB-005: defense_standard appears in str(Violation) when the rule has one."""
    f = tmp_path / "bad.py"
    f.write_text("print('hello')\n")
    violations = _scan(f)
    print_violations = [v for v in violations if "print" in v.message]
    assert print_violations
    s = str(print_violations[0])
    # ARCH-001 has defense_standard="DS-003"
    assert "DS-003" in s, f"Expected 'DS-003' in violation string, got: {s!r}"


def test_violation_str_omits_defense_standard_when_absent() -> None:
    """REQ-SYMB-005: defense_standard is absent from str(Violation) when rule has none."""
    f = Path("bad.py")
    v = Violation(
        file=f,
        line=1,
        col=0,
        message="asyncio.PIPE used directly",
        rule_id="ARCH-UNKNOWN",
        lens="process-flow",
    )
    s = str(v)
    assert "[ARCH-UNKNOWN / process-flow]" in s, (
        f"Expected '[ARCH-UNKNOWN / process-flow]' prefix, got: {s!r}"
    )
    assert "DS-" not in s, f"Unexpected defense_standard in output: {s!r}"


def test_arch004_violation_str_includes_ds002(tmp_path: Path) -> None:
    """Regression guard: ARCH-004 violation str must include DS-002 after the fix."""
    f = tmp_path / "bad.py"
    f.write_text("import asyncio\nval = asyncio.PIPE\n")
    violations = _scan(f)
    pipe_violations = [v for v in violations if "asyncio.PIPE" in v.message]
    assert pipe_violations
    s = str(pipe_violations[0])
    assert "DS-002" in s
    assert "[ARCH-004 / process-flow / DS-002]" in s


def test_violation_str_no_prefix_without_rule_id() -> None:
    """Violation with empty rule_id uses the legacy str format (no prefix)."""
    v = Violation(file=Path("src/x.py"), line=5, col=0, message="some issue", rule_id="", lens="")
    s = str(v)
    assert not s.startswith("["), f"Expected no prefix for rule_id='', got: {s!r}"
    assert "some issue" in s


# ── P13-7: Shared canonical source verification ───────────────────────────────


def test_ast_rules_and_registry_share_rules_object() -> None:
    """P13-7: test_ast_rules and test_registry import RULES from same _rules module."""
    import tests.arch._rules as shared
    import tests.arch.test_registry as reg_mod

    assert reg_mod.RULES is shared.RULES, "test_registry.RULES must be the shared _rules.RULES"


# ── P13-1: ARCH-001 exemptions sync ──────────────────────────────────────────


def test_arch001_exemptions_match_print_exempt_set() -> None:
    """P13-1: ARCH-001 RuleDescriptor.exemptions must cover all _PRINT_EXEMPT files."""
    arch001 = next(r for r in RULES if r.rule_id == "ARCH-001")
    assert arch001.exemptions == _PRINT_EXEMPT


# ── P13-2: ARCH-003 exemptions sync ──────────────────────────────────────────


def test_arch003_exemptions_match_broad_except_set() -> None:
    """P13-2: ARCH-003 RuleDescriptor.exemptions must cover all _BROAD_EXCEPT_EXEMPT files."""
    arch003 = next(r for r in RULES if r.rule_id == "ARCH-003")
    assert arch003.exemptions == _BROAD_EXCEPT_EXEMPT


# ── P13-5: REQ-ARCH-001/002/003 descriptors exist ────────────────────────────


def test_req_arch_rules_have_descriptors() -> None:
    """P13-5: REQ-ARCH rules must have RuleDescriptor constants in their test files."""
    import tests.arch.test_layer_enforcement as le_mod
    import tests.arch.test_subpackage_isolation as si_mod

    assert hasattr(le_mod, "LAYER_RULES"), "test_layer_enforcement must export LAYER_RULES"
    assert hasattr(si_mod, "ISOLATION_RULES"), (
        "test_subpackage_isolation must export ISOLATION_RULES"
    )
    layer_ids = {r.rule_id for r in le_mod.LAYER_RULES.values()}
    isolation_ids = {r.rule_id for r in si_mod.ISOLATION_RULES.values()}
    assert "REQ-ARCH-001" in layer_ids
    assert "REQ-ARCH-003" in layer_ids
    assert "REQ-ARCH-002" in isolation_ids
