"""Typed audit artifact producer tests."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoskillit.core import (
    AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY,
    AdmissionReason,
    AdmissionStatus,
    ArtifactRef,
    AuditAdmissionStoreAuthority,
    AuditArtifactFieldOwnership,
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditCycleVerificationError,
    AuditDispositionCommitOutcome,
    AuditPreflightProjection,
    AuditPrepareRequest,
    AuditReservationRequest,
    AuditVerdict,
    InstallationVersion,
    RecipeExecutionId,
    VerifiedInputPreflightRequest,
    canonical_json_bytes,
    compute_bytes_hash,
    load_audit_semantic_result,
    load_standalone_audit_evidence,
)
from autoskillit.pipeline.audit_admission_ledger import DefaultAuditAdmissionLedger
from autoskillit.server._recipe_execution import DefaultInputPreflightResolver
from autoskillit.server.tools.tools_audit_artifacts import (
    _build_semantic_result,
    _SemanticInputError,
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


def _issued_reservation(
    root: Path,
) -> tuple[
    DefaultAuditAdmissionLedger,
    str,
    AuditPrepareRequest,
    AuditReservationRequest,
]:
    root.mkdir(parents=True, exist_ok=True)
    plan = root / "plan.md"
    plan.write_bytes(b"plan")
    authority = AuditAdmissionStoreAuthority(
        database_path=(root / "store" / "ledger.sqlite3").resolve(),
        expected_owner_id=os.getuid(),
    )
    ledger = DefaultAuditAdmissionLedger(authority)
    execution_id = RecipeExecutionId("execution-1")
    installation_version = ledger.create_or_get_installation(
        recipe_execution_id=execution_id,
        snapshot_digest=compute_bytes_hash(b"snapshot"),
    )
    request = AuditReservationRequest(
        recipe_execution_id=execution_id,
        installation_version=installation_version,
        step_name="audit-impl",
        invocation_template_digest=compute_bytes_hash(b"template"),
        slot_intent_digest=compute_bytes_hash(b"intent"),
        runtime_binding_digest=compute_bytes_hash(b"binding"),
        audited_plan_refs=(_ref(plan, "text/markdown"),),
        cycle_id="cycle-1",
        scope_id="scope-1",
        part_id="part-1",
        allowed_root=root,
        parent_authority_digest=None,
    )
    outcome = ledger.reserve(request)
    assert outcome.reservation_handle is not None
    prepare = AuditPrepareRequest(
        attempt_id=outcome.attempt_id,
        installation_version=installation_version,
        semantic_digest=compute_bytes_hash(b"semantic"),
        accepted=True,
    )
    return ledger, outcome.reservation_handle, prepare, request


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


def test_standalone_writer_rejects_symlink_substitution(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    args = _semantic_args(tmp_path)
    first = write_standalone_audit_evidence_sync(temp_root=allowed_root, **args)
    evidence_path = Path(first["standalone_evidence_path"])
    canonical = evidence_path.read_bytes()
    evidence_path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_bytes(canonical)
    evidence_path.symlink_to(outside)

    replay = write_standalone_audit_evidence_sync(temp_root=allowed_root, **args)

    assert replay["success"] is False
    assert "ContainmentError" in replay["error"]


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


@pytest.mark.anyio
@pytest.mark.parametrize("unrecoverable_serving_store", (False, True))
async def test_semantic_handler_reports_safe_wrong_authority_before_store_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    unrecoverable_serving_store: bool,
) -> None:
    issuing_ledger, handle, _prepare, _request = _issued_reservation(tmp_path / "authority-a")
    serving_root = tmp_path / "authority-b"
    if unrecoverable_serving_store:
        real_root = tmp_path / "real-authority-b"
        real_root.mkdir()
        serving_root.symlink_to(real_root, target_is_directory=True)
    serving_authority = AuditAdmissionStoreAuthority(
        database_path=serving_root / "ledger.sqlite3",
        expected_owner_id=os.getuid(),
    )
    serving_ledger = DefaultAuditAdmissionLedger(serving_authority)

    def fail_if_recovered(_ledger: DefaultAuditAdmissionLedger) -> None:
        pytest.fail("wrong-authority diagnostics must precede store recovery/open")

    monkeypatch.setattr(DefaultAuditAdmissionLedger, "_ensure_recovered", fail_if_recovered)
    monkeypatch.setattr(
        "autoskillit.server._get_ctx",
        lambda: SimpleNamespace(
            audit_admission_ledger=serving_ledger,
            timing_log=SimpleNamespace(record=lambda *_args: None),
        ),
    )
    semantic_root = tmp_path / "semantic-input"
    semantic_root.mkdir()

    encoded = await write_audit_semantic_result(
        reservation_handle=handle,
        **_semantic_args(semantic_root),
    )
    result = json.loads(encoded)

    assert result["success"] is False
    assert result["error_code"] == "wrong_audit_authority"
    assert result["handle_authority_id"] == issuing_ledger.store_authority.authority_id
    assert result["serving_authority_id"] == serving_authority.authority_id
    secret = handle.rsplit(".", 1)[-1]
    logged = repr([record.__dict__ for record in caplog.records])
    assert handle not in encoded
    assert secret not in encoded
    assert str(issuing_ledger.store_authority.database_path) not in encoded
    assert str(serving_authority.database_path) not in encoded
    assert handle not in logged
    assert secret not in logged
    assert str(issuing_ledger.store_authority.database_path) not in logged
    assert str(serving_authority.database_path) not in logged


@pytest.mark.anyio
@pytest.mark.parametrize("scenario", ("malformed", "never-issued", "rotated", "closed"))
async def test_semantic_handler_classifies_same_authority_missing_handles_as_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    scenario: str,
) -> None:
    ledger, issued_handle, prepare, request = _issued_reservation(tmp_path / "authority")
    if scenario == "malformed":
        handle = "not-a-reservation-handle"
    elif scenario == "never-issued":
        handle = f"adr1.{ledger.store_authority.authority_id}.{'0' * 64}"
    elif scenario == "rotated":
        handle = issued_handle
        ledger.reserve(request)
    else:
        handle = issued_handle
        ledger.prepare(prepare)

    monkeypatch.setattr(
        "autoskillit.server._get_ctx",
        lambda: SimpleNamespace(
            audit_admission_ledger=ledger,
            timing_log=SimpleNamespace(record=lambda *_args: None),
        ),
    )
    semantic_root = tmp_path / "semantic-input"
    semantic_root.mkdir()

    encoded = await write_audit_semantic_result(
        reservation_handle=handle,
        **_semantic_args(semantic_root),
    )
    result = json.loads(encoded)

    assert result["success"] is False
    assert result["error_code"] == "stale_or_invalid_reservation"
    assert "handle_authority_id" not in result
    assert "serving_authority_id" not in result
    secret = handle.rsplit(".", 1)[-1]
    logged = repr([record.__dict__ for record in caplog.records])
    assert handle not in encoded
    assert secret not in encoded
    assert str(ledger.store_authority.database_path) not in encoded
    assert handle not in logged
    assert secret not in logged
    assert str(ledger.store_authority.database_path) not in logged


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
    ("target", "expected_exception", "expected_match"),
    (
        ("authority", AuditCycleVerificationError, "not strict canonical"),
        ("plan", _SemanticInputError, "changed while"),
        ("report", AuditCycleVerificationError, "not strict canonical"),
        ("association", _SemanticInputError, "changed while"),
    ),
)
def test_disposition_final_cas_rejects_prepared_artifact_toctou(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    expected_exception: type[Exception],
    expected_match: str,
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

    with pytest.raises(expected_exception, match=expected_match):
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
