"""Tests for write_audit_cycle_artifact: producer/consumer round-trip (#4406),
containment, structural validation, write-once, and read-before-validate ordering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    AUDIT_CYCLE_SCHEMA_VERSION,
    AuditCycleVerifier,
    compute_bytes_hash,
    decode_versioned_json_bytes,
    parse_canonical_json_bytes,
)
from autoskillit.recipe._cmd_rpc_guards import _resolve_plan_disposition
from autoskillit.server.tools.tools_audit_cycle import write_audit_cycle_artifact_sync

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_GENERATED_AT = "2026-07-23T00:00:00Z"


def _authority_fields(
    *,
    inventory_path: Path,
    plan_path: Path,
    remediation_path: Path,
    parent_authority_digest: str | None = None,
    audit_round: int = 1,
) -> dict:
    return {
        "execution_generation": "generation-1",
        "cycle_id": "cycle-1",
        "plan_set_id": "plans-1",
        "scope_id": "scope-1",
        "part_id": "part-a",
        "audit_round": audit_round,
        "parent_authority_digest": parent_authority_digest,
        "audited_plan_refs": [
            {"locator": str(plan_path), "media_type": "text/markdown", "schema_version": 1},
        ],
        "inventory_ref": {
            "locator": str(inventory_path),
            "media_type": "application/json",
            "schema_version": AUDIT_CYCLE_SCHEMA_VERSION,
        },
        "remediation_ref": {
            "locator": str(remediation_path),
            "media_type": "text/markdown",
            "schema_version": 1,
        },
        "assessments": [
            {
                "requirement_id": "REQ-001",
                "requirement_text": "Fix the issue",
                "assessment": "MISSING",
                "evidence_summary": "not present",
            }
        ],
        "verdict": "NO GO",
        "generated_at": _GENERATED_AT,
    }


def _inventory_fields() -> dict:
    return {
        "schema_version": AUDIT_CYCLE_SCHEMA_VERSION,
        "generated_at": _GENERATED_AT,
        "plan_set_id": "plans-1",
        "requirement_ids": ["REQ-001"],
        "requirements": [{"id": "REQ-001", "text": "Fix the issue"}],
    }


def _write_fixture_files(tmp_path: Path) -> tuple[Path, Path]:
    """Write the plan and remediation source files a round-trip test references."""
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("## Implementation Steps\n\n### Step 1: Fix\n")
    remediation_path = tmp_path / "remediation.md"
    remediation_path.write_text("REQ-001 remains")
    return plan_path, remediation_path


class TestProducerConsumerRoundTrip:
    """Step 1 items 1a-1c: the sanctioned producer against the real consumer."""

    def test_full_cycle_round_trip_accepted_by_real_consumers(self, tmp_path: Path) -> None:
        cwd = str(tmp_path)
        cycle_dir = tmp_path / "cycle"
        plan_path, remediation_path = _write_fixture_files(tmp_path)

        inventory_path = cycle_dir / "inventory.json"
        inventory_result = write_audit_cycle_artifact_sync(
            kind="inventory",
            path=str(inventory_path),
            fields=_inventory_fields(),
            cwd=cwd,
        )
        assert inventory_result["success"] is True, inventory_result
        parse_canonical_json_bytes(inventory_path.read_bytes())  # byte-exact canonical

        authority_path = cycle_dir / "authority.json"
        authority_result = write_audit_cycle_artifact_sync(
            kind="authority",
            path=str(authority_path),
            fields=_authority_fields(
                inventory_path=inventory_path,
                plan_path=plan_path,
                remediation_path=remediation_path,
            ),
            cwd=cwd,
        )
        assert authority_result["success"] is True, authority_result
        parse_canonical_json_bytes(authority_path.read_bytes())

        verifier = AuditCycleVerifier(tmp_path)
        authority = verifier.load_authority(authority_path)
        assert authority.verdict.value == "NO GO"
        assert authority.assessments[0].requirement_id == "REQ-001"

        report_path = cycle_dir / "disposition.json"
        report_result = write_audit_cycle_artifact_sync(
            kind="disposition_report",
            path=str(report_path),
            fields={
                "execution_generation": authority.execution_generation,
                "cycle_id": authority.cycle_id,
                "plan_set_id": authority.plan_set_id,
                "scope_id": authority.scope_id,
                "part_id": authority.part_id,
                "audit_round": authority.audit_round,
                "parent_authority_digest": authority.authority_digest,
                "inventory_digest": authority.inventory_ref.content_digest,
                "findings_digest": authority.findings_digest,
                "current_plan_ref": {
                    "locator": str(plan_path),
                    "media_type": "text/markdown",
                    "schema_version": 1,
                },
                "dispositions": [
                    {
                        "requirement_id": "REQ-001",
                        "disposition": "carried@step",
                        "implementation_step": "Step 1",
                    }
                ],
                "generated_at": _GENERATED_AT,
            },
            cwd=cwd,
        )
        assert report_result["success"] is True, report_result
        parse_canonical_json_bytes(report_path.read_bytes())

        report = verifier.load_report(report_path)
        assert report.parent_authority_digest == authority.authority_digest

        plan_digest = compute_bytes_hash(plan_path.read_bytes())
        association_path = cycle_dir / "associations" / f"{plan_digest}.json"
        association_result = write_audit_cycle_artifact_sync(
            kind="plan_association",
            path=str(association_path),
            fields={
                "plan_ref": {
                    "locator": str(plan_path),
                    "media_type": "text/markdown",
                    "schema_version": 1,
                },
                "disposition_ref": {
                    "locator": str(report_path),
                    "media_type": "application/json",
                    "schema_version": 1,
                },
                "parent_authority_digest": authority.authority_digest,
            },
            cwd=cwd,
        )
        assert association_result["success"] is True, association_result
        parse_canonical_json_bytes(association_path.read_bytes())

        resolved = _resolve_plan_disposition(
            audit_cycle_path=str(authority_path),
            current_plan_path=plan_path,
        )
        assert resolved == str(report_path)

    def test_inventory_shape_matches_the_inline_consumer_check(self, tmp_path: Path) -> None:
        """Mirrors the exact inline check inside AuditCycleVerifier._verify_active_tuple."""
        inventory_path = tmp_path / "inventory.json"
        result = write_audit_cycle_artifact_sync(
            kind="inventory",
            path=str(inventory_path),
            fields=_inventory_fields(),
            cwd=str(tmp_path),
        )
        assert result["success"] is True

        raw = decode_versioned_json_bytes(
            inventory_path.read_bytes(),
            expected_version=AUDIT_CYCLE_SCHEMA_VERSION,
            require_canonical=True,
        )
        assert raw is not None
        requirement_ids = tuple(raw["requirement_ids"])
        row_ids = tuple(item["id"] for item in raw["requirements"])
        assert requirement_ids == row_ids
        assert all(isinstance(item, str) and item for item in requirement_ids)


class TestDestinationContainment:
    def test_destination_escaping_cwd_is_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside-dest" / "authority.json"
        result = write_audit_cycle_artifact_sync(
            kind="inventory",
            path=str(outside),
            fields=_inventory_fields(),
            cwd=str(tmp_path),
        )
        assert result["success"] is False
        assert "escapes cwd" in result["error"]
        assert not outside.exists()


class TestReferencedArtifactContainment:
    def test_referenced_artifact_escaping_cwd_is_rejected(self, tmp_path: Path) -> None:
        outside_dir = tmp_path.parent / "outside-plan"
        outside_dir.mkdir(exist_ok=True)
        outside_plan = outside_dir / "plan.md"
        outside_plan.write_text("plan content")
        _, remediation_path = _write_fixture_files(tmp_path)
        inventory_path = tmp_path / "inventory.json"
        write_audit_cycle_artifact_sync(
            kind="inventory",
            path=str(inventory_path),
            fields=_inventory_fields(),
            cwd=str(tmp_path),
        )

        authority_path = tmp_path / "authority.json"
        result = write_audit_cycle_artifact_sync(
            kind="authority",
            path=str(authority_path),
            fields=_authority_fields(
                inventory_path=inventory_path,
                plan_path=outside_plan,
                remediation_path=remediation_path,
            ),
            cwd=str(tmp_path),
        )
        assert result["success"] is False
        assert "referenced artifact read failed" in result["error"]
        assert not authority_path.exists()


class TestMalformedFieldsRejected:
    def test_invalid_assessment_value_rejected_without_raising_or_partial_write(
        self, tmp_path: Path
    ) -> None:
        plan_path, remediation_path = _write_fixture_files(tmp_path)
        inventory_path = tmp_path / "inventory.json"
        write_audit_cycle_artifact_sync(
            kind="inventory",
            path=str(inventory_path),
            fields=_inventory_fields(),
            cwd=str(tmp_path),
        )

        fields = _authority_fields(
            inventory_path=inventory_path, plan_path=plan_path, remediation_path=remediation_path
        )
        fields["assessments"][0]["assessment"] = "NOT_A_REAL_ASSESSMENT"

        authority_path = tmp_path / "authority.json"
        result = write_audit_cycle_artifact_sync(
            kind="authority", path=str(authority_path), fields=fields, cwd=str(tmp_path)
        )
        assert result["success"] is False
        assert "assessment" in result["error"]
        assert not authority_path.exists()

    def test_missing_required_field_rejected(self, tmp_path: Path) -> None:
        fields = _inventory_fields()
        del fields["requirement_ids"]
        path = tmp_path / "inventory.json"
        result = write_audit_cycle_artifact_sync(
            kind="inventory", path=str(path), fields=fields, cwd=str(tmp_path)
        )
        assert result["success"] is False
        assert not path.exists()


class TestUnknownKindRejected:
    def test_unknown_kind_returns_structured_error_with_no_io(self, tmp_path: Path) -> None:
        path = tmp_path / "authroity.json"
        result = write_audit_cycle_artifact_sync(
            kind="authroity",
            path=str(path),
            fields={
                "audited_plan_refs": [
                    {
                        "locator": str(tmp_path / "does-not-exist.md"),
                        "media_type": "text/markdown",
                        "schema_version": 1,
                    }
                ]
            },
            cwd=str(tmp_path),
        )
        assert result == {"success": False, "error": "unknown kind: 'authroity'"}
        assert not path.exists()


class TestWriteOnceGuard:
    def test_existing_destination_is_rejected_and_unchanged(self, tmp_path: Path) -> None:
        path = tmp_path / "inventory.json"
        path.write_bytes(b"pre-existing content")

        result = write_audit_cycle_artifact_sync(
            kind="inventory", path=str(path), fields=_inventory_fields(), cwd=str(tmp_path)
        )
        assert result["success"] is False
        assert "already exists" in result["error"]
        assert path.read_bytes() == b"pre-existing content"


class TestReadBeforeValidateOrdering:
    def test_structural_failure_precedes_referenced_artifact_reads(self, tmp_path: Path) -> None:
        """Regression test: a bad assessment value must fail before ANY referenced
        artifact is read, even when many referenced-artifact paths are present.

        Points every referenced-artifact locator at a directory — resolve_contained_path
        raises ContainmentError("Regular file required") if it is ever invoked on one.
        A failure surfacing as an assessment-validation error (not a containment error)
        proves no referenced-artifact read occurred before structural validation.
        """
        bad_dir = tmp_path / "not-a-file"
        bad_dir.mkdir()
        bad_ref = {
            "locator": str(bad_dir),
            "media_type": "text/markdown",
            "schema_version": 1,
        }

        fields = {
            "execution_generation": "generation-1",
            "cycle_id": "cycle-1",
            "plan_set_id": "plans-1",
            "scope_id": "scope-1",
            "part_id": "part-a",
            "audit_round": 1,
            "parent_authority_digest": None,
            "audited_plan_refs": [bad_ref] * 50,
            "inventory_ref": bad_ref,
            "remediation_ref": bad_ref,
            "assessments": [
                {
                    "requirement_id": "REQ-001",
                    "requirement_text": "Fix the issue",
                    "assessment": "NOT_A_REAL_ASSESSMENT",
                    "evidence_summary": "not present",
                }
            ],
            "verdict": "NO GO",
            "generated_at": _GENERATED_AT,
        }

        authority_path = tmp_path / "authority.json"
        result = write_audit_cycle_artifact_sync(
            kind="authority", path=str(authority_path), fields=fields, cwd=str(tmp_path)
        )
        assert result["success"] is False
        assert "assessment" in result["error"]
        assert "Regular file" not in result["error"]
        assert "Containment" not in result["error"]
        assert not authority_path.exists()

    def test_too_many_referenced_artifacts_rejected_before_reads(self, tmp_path: Path) -> None:
        bad_dir = tmp_path / "not-a-file"
        bad_dir.mkdir()
        bad_ref = {
            "locator": str(bad_dir),
            "media_type": "text/markdown",
            "schema_version": 1,
        }
        fields = {
            "execution_generation": "generation-1",
            "cycle_id": "cycle-1",
            "plan_set_id": "plans-1",
            "scope_id": "scope-1",
            "part_id": "part-a",
            "audit_round": 1,
            "parent_authority_digest": None,
            "audited_plan_refs": [bad_ref] * 1000,
            "inventory_ref": bad_ref,
            "remediation_ref": None,
            "assessments": [
                {
                    "requirement_id": "REQ-001",
                    "requirement_text": "Fix the issue",
                    "assessment": "MISSING",
                    "evidence_summary": "not present",
                }
            ],
            "verdict": "NO GO",
            "generated_at": _GENERATED_AT,
        }
        authority_path = tmp_path / "authority.json"
        result = write_audit_cycle_artifact_sync(
            kind="authority", path=str(authority_path), fields=fields, cwd=str(tmp_path)
        )
        assert result["success"] is False
        assert "exceeds" in result["error"]
        assert not authority_path.exists()


class TestNeverRaises:
    def test_non_dict_fields_returns_structured_error(self, tmp_path: Path) -> None:
        result = write_audit_cycle_artifact_sync(
            kind="inventory",
            path=str(tmp_path / "inventory.json"),
            fields=None,  # type: ignore[arg-type]
            cwd=str(tmp_path),
        )
        assert result["success"] is False

    def test_missing_cwd_returns_structured_error(self, tmp_path: Path) -> None:
        result = write_audit_cycle_artifact_sync(
            kind="inventory",
            path=str(tmp_path / "inventory.json"),
            fields=_inventory_fields(),
            cwd=str(tmp_path / "does-not-exist"),
        )
        assert result["success"] is False
        assert "cwd" in result["error"]
