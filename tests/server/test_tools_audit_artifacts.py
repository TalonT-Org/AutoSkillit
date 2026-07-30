"""Typed audit artifact producer tests."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoskillit.core import (
    AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY,
    AdmissionReason,
    AdmissionStatus,
    ArtifactRef,
    AuditArtifactFieldOwnership,
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditDispositionCommitOutcome,
    AuditPreflightProjection,
    AuditVerdict,
    InstallationVersion,
    RecipeExecutionId,
    VerifiedInputPreflightRequest,
    canonical_json_bytes,
    compute_bytes_hash,
    load_audit_semantic_result,
    load_standalone_audit_evidence,
)
from autoskillit.server._recipe_execution import DefaultInputPreflightResolver
from autoskillit.server.tools.tools_audit_artifacts import (
    _build_semantic_result,
    _write_audit_disposition_bundle_sync,
    _write_semantic_result,
    write_audit_disposition_bundle,
    write_audit_semantic_result,
    write_standalone_audit_evidence,
    write_standalone_audit_evidence_sync,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _ref(path: Path, media_type: str) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(
        locator=str(path),
        media_type=media_type,
        schema_version=1,
        byte_size=len(data),
        content_digest=compute_bytes_hash(data),
    )


def _semantic_args(tmp_path: Path) -> dict[str, Any]:
    plan_path = tmp_path / "plan.md"
    remediation_path = tmp_path / "remediation.md"
    plan_bytes = b"plan"
    remediation_bytes = b"remediation"
    plan_path.write_bytes(plan_bytes)
    remediation_path.write_bytes(remediation_bytes)
    return {
        "audited_plan_refs": [
            ArtifactRef(
                locator=str(plan_path),
                media_type="text/markdown",
                schema_version=1,
                byte_size=len(plan_bytes),
                content_digest=compute_bytes_hash(plan_bytes),
            ).to_dict()
        ],
        "assessments": [
            {
                "requirement_id": "REQ-001",
                "requirement_text": "Preserve diagnostics",
                "assessment": AuditAssessment.COVERED.value,
                "evidence_summary": "The plan preserves the error boundary.",
            }
        ],
        "verdict": AuditVerdict.NO_GO.value,
        "remediation_ref": ArtifactRef(
            locator=str(remediation_path),
            media_type="text/markdown",
            schema_version=1,
            byte_size=len(remediation_bytes),
            content_digest=compute_bytes_hash(remediation_bytes),
        ).to_dict(),
    }


def test_private_semantic_writer_round_trips_through_strict_loader(
    tmp_path: Path,
) -> None:
    semantic = _build_semantic_result(**_semantic_args(tmp_path))
    path = tmp_path / "semantic.json"

    _write_semantic_result(path, semantic)

    assert load_audit_semantic_result(path, tmp_path) == semantic


def test_standalone_writer_is_deterministic_and_non_authoritative(
    tmp_path: Path,
) -> None:
    args = _semantic_args(tmp_path)

    first = write_standalone_audit_evidence_sync(temp_root=tmp_path, **args)
    second = write_standalone_audit_evidence_sync(temp_root=tmp_path, **args)

    assert first == second
    assert first["success"] is True
    assert first["audit_status"] == "NON_PUBLISHED_STANDALONE"
    assert "audit_cycle_path" not in first
    path = Path(first["standalone_evidence_path"])
    evidence = load_standalone_audit_evidence(path, tmp_path)
    assert evidence.verdict is AuditVerdict.NO_GO


def test_standalone_writer_never_raises_for_malformed_semantics(
    tmp_path: Path,
) -> None:
    result = write_standalone_audit_evidence_sync(
        temp_root=tmp_path,
        audited_plan_refs=[],
        assessments=[],
        verdict="attacker-selected",
        remediation_ref=None,
    )

    assert result["success"] is False
    assert "error" in result


def test_typed_handler_signatures_exclude_identity_path_and_cwd() -> None:
    semantic_params = set(inspect.signature(write_audit_semantic_result).parameters)
    standalone_params = set(inspect.signature(write_standalone_audit_evidence).parameters)
    disposition_params = set(inspect.signature(write_audit_disposition_bundle).parameters)
    child_semantic_fields = {
        definition.field_name
        for (kind, _), definition in AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY.items()
        if kind == "semantic_result"
        and definition.ownership is AuditArtifactFieldOwnership.CHILD_SEMANTIC
    }
    framework_params = {"ctx", "step_name"}

    assert {"execution_generation", "cycle_id", "generated_at", "cwd", "path"}.isdisjoint(
        semantic_params
    )
    assert {"execution_generation", "generated_at", "cwd", "path"}.isdisjoint(disposition_params)
    assert semantic_params - framework_params - {"reservation_handle"} == child_semantic_fields
    assert standalone_params - framework_params == child_semantic_fields
    assert disposition_params - framework_params == {
        "authority_path",
        "new_plan_path",
        "new_plan_media_type",
        "new_plan_schema_version",
        "dispositions",
    }


@pytest.mark.anyio
async def test_admission_handlers_fail_closed_for_invalid_requests_without_raising() -> None:
    semantic = json.loads(
        await write_audit_semantic_result(
            reservation_handle="",
            audited_plan_refs=[],
            assessments=[],
            verdict="GO",
        )
    )
    disposition = json.loads(
        await write_audit_disposition_bundle(
            authority_path="",
            new_plan_path="",
            new_plan_media_type="",
            new_plan_schema_version=1,
            dispositions=[],
        )
    )

    assert semantic["success"] is False
    assert disposition["success"] is False


def _write_disposition_authority(tmp_path: Path) -> tuple[Path, Path, AuditCycleAuthority]:
    audited_plan = tmp_path / "audited-plan.md"
    audited_plan.write_text("audited plan")
    inventory = tmp_path / "inventory.json"
    inventory.write_bytes(
        canonical_json_bytes(
            {
                "requirement_ids": ["REQ-001"],
                "requirements": [{"id": "REQ-001"}],
                "schema_version": 1,
            }
        )
    )
    remediation = tmp_path / "remediation.md"
    remediation.write_text("remediate REQ-001")
    authority = AuditCycleAuthority.create(
        execution_generation="execution-1",
        cycle_id="cycle-1",
        plan_set_id="plan-set-1",
        scope_id="scope-1",
        part_id="part-1",
        audit_round=1,
        parent_authority_digest=None,
        audited_plan_refs=(_ref(audited_plan, "text/markdown"),),
        inventory_ref=_ref(inventory, "application/json"),
        assessments=(
            AuditAssessmentRow.create(
                requirement_id="REQ-001",
                requirement_text="Implement the requirement",
                assessment=AuditAssessment.MISSING,
                evidence_summary="The audited plan omits it.",
            ),
        ),
        verdict=AuditVerdict.NO_GO,
        remediation_ref=_ref(remediation, "text/markdown"),
        generated_at="2026-07-30T00:00:00Z",
    )
    authority_path = tmp_path / "authority.json"
    authority_path.write_bytes(authority.canonical_bytes)
    new_plan = tmp_path / "new-plan.md"
    new_plan.write_text("new plan")
    return authority_path, new_plan, authority


class _DispositionLedger:
    def __init__(self, authority: AuditCycleAuthority) -> None:
        self._authority = authority
        self.commit_requests: list[Any] = []

    def current_head(self, **_kwargs: Any) -> Any:
        return SimpleNamespace(current_authority_digest=self._authority.authority_digest)

    def commit_disposition(self, request: Any) -> AuditDispositionCommitOutcome:
        self.commit_requests.append(request)
        return AuditDispositionCommitOutcome(
            committed=True,
            generated_at=request.generated_at,
        )


class _TamperAtFinalCas:
    def __init__(self, tamper: Any) -> None:
        self._tamper = tamper

    def __enter__(self) -> None:
        self._tamper()

    def __exit__(self, *_args: Any) -> None:
        return None


@pytest.mark.parametrize(
    "target",
    ("authority", "plan", "report", "association"),
)
def test_disposition_final_cas_rejects_prepared_artifact_toctou(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    authority_path, new_plan, authority = _write_disposition_authority(tmp_path)
    temp_dir = tmp_path / ".autoskillit" / "temp"
    ledger = _DispositionLedger(authority)
    installed = SimpleNamespace(
        snapshot=SimpleNamespace(execution_id=authority.execution_generation),
        installation_version=InstallationVersion("installation-1"),
    )

    def tamper() -> None:
        targets = {
            "authority": authority_path,
            "plan": new_plan,
            "report": next(temp_dir.rglob("disposition-report.json")),
            "association": next(temp_dir.rglob("plan-association.json")),
        }
        targets[target].write_bytes(b"tampered")

    tool_ctx = SimpleNamespace(
        project_dir=tmp_path,
        temp_dir=temp_dir,
        audit_admission_ledger=ledger,
        recipe_execution_lock=_TamperAtFinalCas(tamper),
    )
    monkeypatch.setattr(
        "autoskillit.server._recipe_execution.get_recipe_execution",
        lambda _tool_ctx: installed,
    )

    with pytest.raises(Exception):
        _write_audit_disposition_bundle_sync(
            tool_ctx=tool_ctx,
            authority_path=str(authority_path),
            new_plan_path=str(new_plan),
            new_plan_media_type="text/markdown",
            new_plan_schema_version=1,
            dispositions=[
                {
                    "requirement_id": "REQ-001",
                    "disposition": "carried@step",
                    "implementation_step": "Step 1",
                }
            ],
        )

    assert ledger.commit_requests == []


def test_disposition_final_cas_commits_unchanged_verified_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority_path, new_plan, authority = _write_disposition_authority(tmp_path)
    temp_dir = tmp_path / ".autoskillit" / "temp"
    ledger = _DispositionLedger(authority)
    installed = SimpleNamespace(
        snapshot=SimpleNamespace(execution_id=authority.execution_generation),
        installation_version=InstallationVersion("installation-1"),
    )
    tool_ctx = SimpleNamespace(
        project_dir=tmp_path,
        temp_dir=temp_dir,
        audit_admission_ledger=ledger,
        recipe_execution_lock=_TamperAtFinalCas(lambda: None),
    )
    monkeypatch.setattr(
        "autoskillit.server._recipe_execution.get_recipe_execution",
        lambda _tool_ctx: installed,
    )

    result = _write_audit_disposition_bundle_sync(
        tool_ctx=tool_ctx,
        authority_path=str(authority_path),
        new_plan_path=str(new_plan),
        new_plan_media_type="text/markdown",
        new_plan_schema_version=1,
        dispositions=[
            {
                "requirement_id": "REQ-001",
                "disposition": "carried@step",
                "implementation_step": "Step 1",
            }
        ],
    )

    assert result["success"] is True
    assert len(ledger.commit_requests) == 1


@pytest.mark.parametrize("committed_name", (None, "different-report.json"))
def test_preflight_rejects_prepared_disposition_without_matching_committed_projection(
    tmp_path: Path,
    committed_name: str | None,
) -> None:
    authority_path = tmp_path / "authority.json"
    report_path = tmp_path / "disposition-report.json"
    authority_path.write_text("prepared authority")
    report_path.write_text("prepared disposition")
    authority_digest = compute_bytes_hash(b"authority")
    plan_digest = compute_bytes_hash(b"plan")
    resolution_calls: list[dict[str, Any]] = []

    class _UncommittedLedger:
        def preflight_projection(self, **_kwargs: Any) -> AuditPreflightProjection:
            return AuditPreflightProjection(
                plan_set_id="plan-set-1",
                scope_id="scope-1",
                part_id="part-1",
            )

        def current_head(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(current_authority_digest=authority_digest)

        def resolve_disposition(self, **kwargs: Any) -> Path | None:
            resolution_calls.append(kwargs)
            return tmp_path / committed_name if committed_name is not None else None

    class _PreparedFileVerifier:
        def load_authority(self, _path: str) -> Any:
            return SimpleNamespace(
                execution_generation="execution-1",
                cycle_id="cycle-1",
                plan_set_id="plan-set-1",
                scope_id="scope-1",
                part_id="part-1",
                verdict=AuditVerdict.NO_GO,
                authority_digest=authority_digest,
            )

        def load_report(self, _path: str) -> Any:
            return SimpleNamespace(current_plan_ref=SimpleNamespace(content_digest=plan_digest))

        def evaluate_paths(self, **_kwargs: Any) -> Any:
            pytest.fail("uncommitted disposition must reject before byte-only admission")

    resolver = DefaultInputPreflightResolver(
        allowed_root=tmp_path,
        ledger=_UncommittedLedger(),  # type: ignore[arg-type]
        recipe_execution_id=RecipeExecutionId("execution-1"),
        installation_version=InstallationVersion("installation-1"),
    )
    resolver._verifier = _PreparedFileVerifier()  # type: ignore[assignment]

    result = resolver.resolve(
        VerifiedInputPreflightRequest(
            execution_generation="execution-1",
            step_name="implement",
            skill_name="implement-worktree",
            plan_path=str(tmp_path / "plan.md"),
            audit_cycle_path=str(authority_path),
            plan_disposition_path=str(report_path),
        )
    )

    assert result.decision.status is AdmissionStatus.REJECT
    assert result.decision.reason is AdmissionReason.DISPOSITION_MISMATCH
    assert resolution_calls == [
        {
            "authority_digest": authority_digest,
            "plan_digest": plan_digest,
        }
    ]
