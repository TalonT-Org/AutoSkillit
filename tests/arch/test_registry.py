"""Symbolic rule registry tests.

Tests the RuleDescriptor dataclass, RULES tuple, and Violation NamedTuple
that form the symbolic registry of architecture enforcement rules.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tests.arch._helpers import _scan
from tests.arch._rules import (
    _ASYNCIO_PIPE_EXEMPT,
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

    # (c) exact set of IDs must match the visitor's rule set
    # Add a RuleDescriptor for every new visitor rule and update this set.
    expected_ids = frozenset(
        {
            "ARCH-001",
            "ARCH-002",
            "ARCH-003",
            "ARCH-004",
            "ARCH-005",
            "ARCH-006",
            "ARCH-007",
            "ARCH-008",
            "ARCH-009",
        }
    )
    actual_ids = frozenset(rule_ids)
    assert actual_ids == expected_ids, (
        f"RULES ID mismatch. Missing: {expected_ids - actual_ids}. "
        f"Extra: {actual_ids - expected_ids}"
    )


def test_all_rules_have_defense_standard() -> None:
    """Every entry in RULES, LAYER_RULES, and ISOLATION_RULES must declare
    a defense_standard.

    Prevents future rule additions from silently omitting the defense_standard
    field, which would break audit-defense-standards traceability.
    """
    import tests.arch.test_layer_enforcement as le_mod
    import tests.arch.test_subpackage_isolation as si_mod

    all_rules: list[RuleDescriptor] = list(RULES)
    all_rules.extend(le_mod.LAYER_RULES.values())
    all_rules.extend(si_mod.ISOLATION_RULES.values())

    missing = [r.rule_id for r in all_rules if r.defense_standard is None]
    assert not missing, (
        f"Rule entries missing defense_standard: {missing}. "
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


# ── P13-9: ARCH-004 exemptions sync ──────────────────────────────────────────


def test_arch004_exemptions_match_asyncio_pipe_exempt_set() -> None:
    """P13-9: ARCH-004 RuleDescriptor.exemptions must equal _ASYNCIO_PIPE_EXEMPT.

    If execution/process.py is renamed or the ARCH-004 exemption set drifts,
    this test catches the staleness before the AST visitor silently skips
    the new filename.
    """
    arch004 = next(r for r in RULES if r.rule_id == "ARCH-004")
    assert arch004.exemptions == _ASYNCIO_PIPE_EXEMPT


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
    assert "REQ-LAYER-001" in layer_ids
    assert "REQ-LAYER-002" in layer_ids


def test_monkeypatch_targets_do_not_bypass_package_reexports() -> None:
    """Every monkeypatch.setattr path must target the namespace production code resolves.

    When autoskillit.X re-exports 'name' from autoskillit.X.submodule via __init__.py,
    patching autoskillit.X.submodule.name does NOT affect autoskillit.X.name.
    All patches of the form autoskillit.X.submodule.name, where X is a sub-package
    that re-exports 'name' FROM that exact submodule, are wrong and must be corrected.

    Note: patching autoskillit.X.B.name where X imports 'name' from a DIFFERENT submodule
    (not B) is correct -- it targets the local binding in B, which is the namespace that
    module B's own functions resolve.
    """
    # Match string literals in monkeypatch.setattr("autoskillit.A.B.C", ...)
    # where A is a sub-package, B is a submodule, C is the name.
    pattern = re.compile(
        r'monkeypatch\.setattr\s*\(\s*["\']'
        r"(autoskillit\.\w+\.\w+\.\w+)"
        r'["\']'
    )

    violations: list[str] = []
    tests_dir = Path(__file__).parent.parent

    for test_file in sorted(tests_dir.glob("**/test_*.py")):
        source = test_file.read_text()
        for match in pattern.finditer(source):
            full_path = match.group(1)
            # Split: autoskillit . pkg . submodule . name
            parts = full_path.split(".")
            if len(parts) != 4:
                continue
            _, pkg, submod, name = parts
            parent_pkg = f"autoskillit.{pkg}"
            try:
                parent_mod = importlib.import_module(parent_pkg)
            except ImportError:
                continue
            if not hasattr(parent_mod, name):
                continue
            # Refine: only flag if the parent pkg actually imports 'name' FROM this
            # exact submodule. If it imports 'name' from a different module (e.g.
            # autoskillit.migration imports applicable_migrations from .loader, not
            # .engine), then the patch targets a local binding in 'submod' -- which
            # is the correct mock target for module-level imports in that submodule.
            try:
                parent_source = inspect.getsource(parent_mod)
                tree = ast.parse(parent_source)
            except (OSError, TypeError, SyntaxError):
                # Can't inspect source -- conservatively flag as violation.
                imports_from_this_submod = True
            else:
                imports_from_this_submod = False
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    is_relative_from_submod = node.level == 1 and node.module == submod
                    is_absolute_from_submod = (
                        node.level == 0 and node.module == f"autoskillit.{pkg}.{submod}"
                    )
                    if is_relative_from_submod or is_absolute_from_submod:
                        for alias in node.names:
                            if (alias.asname or alias.name) == name:
                                imports_from_this_submod = True
                                break
                    if imports_from_this_submod:
                        break
            if imports_from_this_submod:
                line_no = source[: match.start()].count("\n") + 1
                violations.append(
                    f"{test_file.name}:{line_no}: patches {full_path!r} "
                    f"but '{name}' is re-exported at '{parent_pkg}.{name}'. "
                    f"Patch '{parent_pkg}.{name}' instead."
                )

    assert not violations, "Monkeypatch paths bypass package re-exports:\n" + "\n".join(
        f"  {v}" for v in violations
    )
