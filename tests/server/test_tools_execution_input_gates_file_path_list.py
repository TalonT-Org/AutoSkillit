"""file_path_list gate behavioral tests for comma and quoted newline plan lists."""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _check(skill_command: str, cwd: str, resolver):
    from autoskillit.server._guards import _check_input_contracts

    return _check_input_contracts(skill_command, cwd, resolver)


def _make_spec(name: str, type_: str, position: int, required: bool = False):
    from autoskillit.core import InputSpec

    return InputSpec(name=name, type=type_, required=required, position=position)


def _resolver_for(specs):
    return lambda skill_command, _s=tuple(specs): _s


def test_gate_accepts_comma_separated_plan_list(tmp_path):
    """Comma-separated existing files in a file_path_list spec must be accepted."""
    plan_a = tmp_path / "plan_a.md"
    plan_a.write_text("a")
    plan_b = tmp_path / "plan_b.md"
    plan_b.write_text("b")
    spec = _make_spec("all_plan_paths", "file_path_list", 0)
    result = _check(
        f"/autoskillit:audit-impl {plan_a},{plan_b}",
        str(tmp_path),
        _resolver_for((spec,)),
    )
    assert result is None


def test_gate_rejects_comma_list_with_missing_member(tmp_path):
    """Comma-separated list with one missing member must be rejected naming the missing path."""
    plan_a = tmp_path / "plan_a.md"
    plan_a.write_text("a")
    missing = tmp_path / "missing.md"
    spec = _make_spec("all_plan_paths", "file_path_list", 0)
    result = _check(
        f"/autoskillit:audit-impl {plan_a},{missing}",
        str(tmp_path),
        _resolver_for((spec,)),
    )
    assert result is not None
    parsed = json.loads(result)
    assert parsed["success"] is False
    assert str(missing) in parsed["result"]


def test_gate_rejects_comma_list_all_missing(tmp_path):
    """All-missing comma list must be rejected naming every missing path."""
    missing_a = tmp_path / "missing_a.md"
    missing_b = tmp_path / "missing_b.md"
    spec = _make_spec("all_plan_paths", "file_path_list", 0)
    result = _check(
        f"/autoskillit:audit-impl {missing_a},{missing_b}",
        str(tmp_path),
        _resolver_for((spec,)),
    )
    assert result is not None
    parsed = json.loads(result)
    assert parsed["success"] is False
    assert str(missing_a) in parsed["result"]
    assert str(missing_b) in parsed["result"]


def test_resolve_input_specs_audit_impl_path_sequence():
    """audit-impl path-spec sequence must be list, list, scalar in declared order."""
    from autoskillit.recipe._contracts_manifest import resolve_input_specs

    specs = resolve_input_specs("/autoskillit:audit-impl /plan1.md,/plan2.md main feature/x")
    names_types_positions = [(s.name, s.type, s.position) for s in specs]
    assert ("all_plan_paths", "file_path_list", 0) in names_types_positions
    assert ("closure_authority_path", "file_path", 2) in names_types_positions


def test_gate_accepts_quoted_newline_separated_plan_list(tmp_path):
    """Quoted newline-separated list is preserved as one logical argument and accepted."""
    from autoskillit.recipe._contracts_manifest import resolve_input_specs

    plan_a = tmp_path / "plan_a.md"
    plan_a.write_text("a")
    plan_b = tmp_path / "plan_b.md"
    plan_b.write_text("b")
    cmd = f'/autoskillit:audit-impl "{plan_a}\n{plan_b}" main feature/x'
    resolver = resolve_input_specs
    result = _check(cmd, str(tmp_path), resolver)
    assert result is None


def test_gate_accepts_audit_impl_conflict_report_list(tmp_path):
    """merge-prs audit-impl shape: plan list and conflict-report list bind separately."""
    from autoskillit.recipe._contracts_manifest import resolve_input_specs

    plan_a = tmp_path / "plan_a.md"
    plan_a.write_text("a")
    plan_b = tmp_path / "plan_b.md"
    plan_b.write_text("b")
    conflict_a = tmp_path / "conflict_a.md"
    conflict_a.write_text("ca")
    conflict_b = tmp_path / "conflict_b.md"
    conflict_b.write_text("cb")
    cmd = (
        f'/autoskillit:audit-impl "{plan_a},{plan_b}" branch feature/x "{conflict_a},{conflict_b}"'
    )
    resolver = resolve_input_specs
    result = _check(cmd, str(tmp_path), resolver)
    assert result is None


def test_real_resolver_multi_spec_interaction(tmp_path):
    """Real resolver with all three path specs — plan list, conflict list, scalar closure."""
    from autoskillit.recipe._contracts_manifest import resolve_input_specs

    plan_a = tmp_path / "plan_a.md"
    plan_a.write_text("a")
    plan_b = tmp_path / "plan_b.md"
    plan_b.write_text("b")
    conflict_a = tmp_path / "conflict_a.md"
    conflict_a.write_text("ca")
    conflict_b = tmp_path / "conflict_b.md"
    conflict_b.write_text("cb")
    authority = tmp_path / "authority.md"
    authority.write_text("auth")
    cmd = (
        f"/autoskillit:audit-impl {plan_a},{plan_b} branch feature/x "
        f"{conflict_a},{conflict_b} {authority}"
    )
    result = _check(cmd, str(tmp_path), resolve_input_specs)
    assert result is None
