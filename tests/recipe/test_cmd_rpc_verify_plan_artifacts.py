"""Tests for recipe._cmd_rpc.verify_plan_artifacts — deterministic salvage callable
for context-limit-stumbled plan-producing steps (issue #4305)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import autoskillit.recipe._cmd_rpc_guards as cmd_rpc_guards
from autoskillit.core import AuditCycleVerifier
from autoskillit.core.closure_hashing import (
    canonical_json_bytes,
    compute_bytes_hash,
    compute_canonical_hash,
)
from autoskillit.core.types import (
    AUDIT_CYCLE_SCHEMA_VERSION,
    ArtifactRef,
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditVerdict,
    PlanDispositionReport,
    PlanDispositionRow,
)
from autoskillit.recipe._cmd_rpc import verify_plan_artifacts

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class _StaticDispositionResolver:
    def __init__(self, *, authority_digest: str, plan_digest: str, path) -> None:
        self._authority_digest = authority_digest
        self._plan_digest = plan_digest
        self._path = path

    def resolve(self, *, authority_digest: str, plan_digest: str):
        if authority_digest != self._authority_digest or plan_digest != self._plan_digest:
            return None
        return self._path


def _write(tmp_path, name, content="plan content"):
    path = tmp_path / name
    path.write_text(content)
    return str(path)


def _ref(path, media_type: str) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(
        locator=str(path),
        media_type=media_type,
        schema_version=AUDIT_CYCLE_SCHEMA_VERSION,
        byte_size=len(data),
        content_digest=compute_bytes_hash(data),
    )


def _write_audit_tuple(tmp_path):
    cycle = tmp_path / "cycle"
    cycle.mkdir()
    plan = tmp_path / "plan.md"
    plan.write_text(
        "## Implementation Steps\n\n### Step 1: Fix\n\n"
        "## Requirements Map\n\n"
        "| Requirement ID | Disposition | Implementation Step |\n"
        "|---|---|---|\n"
        "| REQ-001 | carried@step | Step 1 |\n"
    )
    inventory = cycle / "inventory.json"
    inventory.write_bytes(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "requirement_ids": ["REQ-001"],
                "requirements": [{"id": "REQ-001"}],
            }
        )
    )
    remediation = cycle / "remediation.md"
    remediation.write_text("REQ-001 remains")
    plan_ref = _ref(plan, "text/markdown")
    inventory_ref = _ref(inventory, "application/json")
    assessment = AuditAssessmentRow.create(
        requirement_id="REQ-001",
        requirement_text="Fix the issue",
        assessment=AuditAssessment.MISSING,
        evidence_summary="not present",
    )
    authority = AuditCycleAuthority.create(
        execution_generation="generation-1",
        cycle_id="cycle-1",
        plan_set_id="plans-1",
        scope_id="scope-1",
        part_id="part-a",
        audit_round=1,
        parent_authority_digest=None,
        audited_plan_refs=(plan_ref,),
        inventory_ref=inventory_ref,
        assessments=(assessment,),
        verdict=AuditVerdict.NO_GO,
        remediation_ref=_ref(remediation, "text/markdown"),
        generated_at="2026-07-23T00:00:00Z",
    )
    authority_path = cycle / "authority.json"
    authority_path.write_bytes(authority.canonical_bytes)
    disposition = PlanDispositionReport.create(
        execution_generation=authority.execution_generation,
        cycle_id=authority.cycle_id,
        plan_set_id=authority.plan_set_id,
        scope_id=authority.scope_id,
        part_id=authority.part_id,
        audit_round=authority.audit_round,
        parent_authority_digest=authority.authority_digest,
        inventory_digest=authority.inventory_ref.content_digest,
        findings_digest=authority.findings_digest,
        current_plan_ref=plan_ref,
        dispositions=(
            PlanDispositionRow.create(
                requirement_id="REQ-001",
                disposition="carried@step",
                implementation_step="Step 1",
            ),
        ),
        generated_at="2026-07-23T00:00:01Z",
    )
    disposition_path = cycle / "disposition.json"
    disposition_path.write_bytes(disposition.canonical_bytes)
    association_payload = {
        "schema_version": 1,
        "plan_ref": plan_ref.to_dict(),
        "disposition_ref": _ref(disposition_path, "application/json").to_dict(),
        "parent_authority_digest": authority.authority_digest,
    }
    association_payload["association_digest"] = compute_canonical_hash(
        association_payload,
        domain="autoskillit:audit-cycle:plan-association:v1:sha256",
    )
    associations = cycle / "associations"
    associations.mkdir()
    (associations / f"{plan_ref.content_digest}.json").write_bytes(
        canonical_json_bytes(association_payload)
    )
    return plan, authority_path, disposition_path


def test_single_absolute_path_salvaged(tmp_path):
    p = _write(tmp_path, "plan.md")
    result = verify_plan_artifacts(plan_parts=p)
    assert result == {"verdict": "salvaged", "plan_parts": p, "plan_path": p}


def test_newline_joined_two_paths_salvaged(tmp_path):
    a = _write(tmp_path, "plan_part_a.md")
    b = _write(tmp_path, "plan_part_b.md")
    result = verify_plan_artifacts(plan_parts=f"{a}\n{b}")
    assert result == {"verdict": "salvaged", "plan_parts": f"{a}\n{b}", "plan_path": a}


def test_comma_separated_two_paths_salvaged(tmp_path):
    a = _write(tmp_path, "plan_part_a.md")
    b = _write(tmp_path, "plan_part_b.md")
    result = verify_plan_artifacts(plan_parts=f"{a},{b}")
    assert result == {"verdict": "salvaged", "plan_parts": f"{a}\n{b}", "plan_path": a}


def test_json_list_repr_two_paths_salvaged(tmp_path):
    a = _write(tmp_path, "plan_part_a.md")
    b = _write(tmp_path, "plan_part_b.md")
    result = verify_plan_artifacts(plan_parts=f'["{a}", "{b}"]')
    assert result == {"verdict": "salvaged", "plan_parts": f"{a}\n{b}", "plan_path": a}


def test_python_list_repr_single_path_salvaged(tmp_path):
    a = _write(tmp_path, "plan.md")
    result = verify_plan_artifacts(plan_parts=f"['{a}']")
    assert result == {"verdict": "salvaged", "plan_parts": a, "plan_path": a}


def test_newline_joined_missing_file_unsalvageable(tmp_path):
    a = _write(tmp_path, "plan_part_a.md")
    missing = str(tmp_path / "plan_part_b.md")
    result = verify_plan_artifacts(plan_parts=f"{a}\n{missing}")
    assert result == {"verdict": "unsalvageable"}


def test_empty_input_unsalvageable():
    assert verify_plan_artifacts(plan_parts="") == {"verdict": "unsalvageable"}


def test_whitespace_input_unsalvageable():
    assert verify_plan_artifacts(plan_parts="   \n  ") == {"verdict": "unsalvageable"}


def test_relative_path_unsalvageable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "plan.md").write_text("content")
    result = verify_plan_artifacts(plan_parts="plan.md")
    assert result == {"verdict": "unsalvageable"}


def test_existing_empty_file_unsalvageable(tmp_path):
    empty = tmp_path / "plan.md"
    empty.write_text("")
    result = verify_plan_artifacts(plan_parts=str(empty))
    assert result == {"verdict": "unsalvageable"}


def test_active_no_go_salvage_restores_exact_disposition(tmp_path):
    plan, authority, disposition = _write_audit_tuple(tmp_path)
    loaded_authority = AuditCycleVerifier(authority.parent).load_authority(authority)
    result = verify_plan_artifacts(
        plan_parts=str(plan),
        audit_cycle_path=str(authority),
        _committed_disposition_resolver=_StaticDispositionResolver(
            authority_digest=loaded_authority.authority_digest,
            plan_digest=compute_bytes_hash(plan.read_bytes()),
            path=disposition,
        ),
    )
    assert result == {
        "verdict": "salvaged",
        "plan_parts": str(plan),
        "plan_path": str(plan),
        "plan_disposition_path": str(disposition),
    }


def test_prepared_disposition_without_committed_projection_is_unsalvageable(tmp_path):
    plan, authority, _ = _write_audit_tuple(tmp_path)

    assert verify_plan_artifacts(
        plan_parts=str(plan),
        audit_cycle_path=str(authority),
    ) == {"verdict": "unsalvageable"}


def test_active_no_go_salvage_rejects_missing_association(tmp_path):
    plan, authority, _ = _write_audit_tuple(tmp_path)
    association = next((authority.parent / "associations").iterdir())
    association.rename(association.with_name("wrong-key.json"))
    assert verify_plan_artifacts(
        plan_parts=str(plan),
        audit_cycle_path=str(authority),
    ) == {"verdict": "unsalvageable"}


def test_active_no_go_salvage_logs_authority_validation_failure(tmp_path, monkeypatch):
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
    cycle = tmp_path / "cycle"
    cycle.mkdir()
    authority = cycle / "authority.json"
    authority.write_text("not-json")
    warnings = []
    monkeypatch.setattr(
        cmd_rpc_guards,
        "logger",
        SimpleNamespace(warning=lambda event, **context: warnings.append((event, context))),
    )

    assert verify_plan_artifacts(
        plan_parts=str(plan),
        audit_cycle_path=str(authority),
    ) == {"verdict": "unsalvageable"}
    assert warnings[0][0] == "plan_disposition_validation_rejected"
    assert warnings[0][1]["reason"] == "authority loading or validation failed"
    assert warnings[0][1]["audit_cycle_path"] == str(authority)
    assert warnings[0][1]["current_plan_path"] == str(plan)
    assert warnings[0][1]["error"].startswith("AuditCycleVerificationError:")
    assert warnings[0][1]["exc_info"] is True


def test_active_no_go_salvage_logs_malformed_association_context(tmp_path, monkeypatch):
    plan, authority, _ = _write_audit_tuple(tmp_path)
    association = next((authority.parent / "associations").iterdir())
    association.write_text("{}")
    warnings = []
    monkeypatch.setattr(
        cmd_rpc_guards,
        "logger",
        SimpleNamespace(warning=lambda event, **context: warnings.append((event, context))),
    )

    assert verify_plan_artifacts(
        plan_parts=str(plan),
        audit_cycle_path=str(authority),
    ) == {"verdict": "unsalvageable"}
    assert warnings[0][0] == "plan_disposition_validation_rejected"
    assert warnings[0][1]["reason"] == (
        "expected exactly one matching plan association; "
        "matches=0, records=0, invalid_candidates=1, candidates=1"
    )
    assert warnings[0][1]["audit_cycle_path"] == str(authority)
    assert warnings[0][1]["current_plan_path"] == str(plan)
    assert warnings[0][1]["error"] is None
    assert warnings[0][1]["exc_info"] is False
