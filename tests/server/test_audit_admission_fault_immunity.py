"""Bounded fault immunity at the attested audit admission production seam."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import autoskillit.server._audit_authority_materializer as materializer_module
import autoskillit.server._recipe_execution as audit_finalization_module
import autoskillit.server.tools.tools_audit_artifacts as audit_artifacts_module
import autoskillit.server.tools.tools_execution as execution_module
from autoskillit.core import (
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    AuditAttemptLifecycle,
    AuditCycleVerifier,
    AuditIdentityReservation,
    RecipeBindingProjection,
)
from autoskillit.core.io import resolve_temp_dir
from autoskillit.pipeline import ReadyRecipe
from autoskillit.server._recipe_execution import (
    build_recipe_execution_snapshot,
    clear_recipe_execution,
    install_recipe_execution,
    prepare_recipe_execution,
)
from autoskillit.server._recipe_initialization import stage_recipe_initialization
from autoskillit.server.tools.tools_audit_artifacts import (
    write_audit_semantic_result,
)
from autoskillit.server.tools.tools_execution import run_skill
from autoskillit.server.tools.tools_recipe import complete_recipe_initialization
from tests.server._helpers import (
    _credit_initialization_sections,
    _open_kitchen_patched,
    _pull_step_section,
    _ready_recipe_segment_step,
    _skill_ok,
)
from tests.server._pipeline_test_helpers import _ack_direct_run_skill_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_RECIPE = "remediation"
_STEP = "audit_impl"
_BOUND_INVOCATION_MARKER = "AUTOSKILLIT_BOUND_INVOCATION_V1\n"
_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4419",
    "task_description": "exercise deterministic audit admission fault recovery",
}
_REDISPATCH_STAGES = {"semantic_acceptance", "prepared_record_persistence"}
_PREPARED_STAGES = {
    "inventory_write",
    "authority_write",
    "post_write_pre_cas",
    "cas",
}
_PUBLISHED_STAGES = {
    "success_effect_finalization",
    "result_rewrite",
    "response_shaping",
    "response_commit",
}
_STAGES = (
    "semantic_acceptance",
    "prepared_record_persistence",
    "inventory_write",
    "authority_write",
    "post_write_pre_cas",
    "cas",
    "success_effect_finalization",
    "result_rewrite",
    "response_shaping",
    "response_commit",
)


async def _install_attested_recipe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_ctx,
) -> tuple[dict[str, Any], dict[str, Any]]:
    monkeypatch.chdir(tmp_path)
    envelope = await _open_kitchen_patched(_RECIPE, _OVERRIDES, monkeypatch)
    assert envelope["success"] is True
    await _credit_initialization_sections(envelope)
    receipt = json.loads(await complete_recipe_initialization(envelope["initialization_id"]))
    assert receipt["success"] is True
    credential = receipt[RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY]
    assert isinstance(credential, dict)
    if "recipe_segment" in envelope:
        step, credential = _ready_recipe_segment_step(tool_ctx, _STEP)
    else:
        step = await _pull_step_section(envelope, _STEP)
    return credential, step


def _attempt_state(
    database_path: Path,
    attempt_id: str,
) -> dict[str, object]:
    with sqlite3.connect(database_path) as connection:
        attempt = connection.execute(
            "SELECT lifecycle, committed_outcome_json FROM attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        assert attempt is not None
        prepared_effects = tuple(
            row[0]
            for row in connection.execute(
                "SELECT artifact_kind FROM prepared_effects "
                "WHERE attempt_id = ? ORDER BY artifact_kind",
                (attempt_id,),
            )
        )
        finalization_effects = tuple(
            row[0]
            for row in connection.execute(
                "SELECT effect_name FROM finalization_effects "
                "WHERE attempt_id = ? ORDER BY effect_name",
                (attempt_id,),
            )
        )
        head_count = connection.execute(
            "SELECT COUNT(*) FROM head_claims",
        ).fetchone()
        projection_count = connection.execute(
            "SELECT COUNT(*) FROM preflight_projections",
        ).fetchone()
        attempt_count = connection.execute(
            "SELECT COUNT(*) FROM attempts",
        ).fetchone()
    assert head_count is not None
    assert projection_count is not None
    assert attempt_count is not None
    return {
        "lifecycle": attempt[0],
        "committed_outcome_json": attempt[1],
        "prepared_effects": prepared_effects,
        "finalization_effects": finalization_effects,
        "head_count": head_count[0],
        "projection_count": projection_count[0],
        "attempt_count": attempt_count[0],
    }


def _install_fault(
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
    tool_ctx,
) -> list[str]:
    fault_hits: list[str] = []

    def fail_once() -> None:
        if not fault_hits:
            fault_hits.append(stage)
            raise OSError(f"injected audit fault: {stage}")

    if stage == "semantic_acceptance":
        original = audit_artifacts_module._write_or_verify_semantic_result

        def fault_semantic(*args, **kwargs):
            fail_once()
            return original(*args, **kwargs)

        monkeypatch.setattr(
            audit_artifacts_module,
            "_write_or_verify_semantic_result",
            fault_semantic,
        )
    elif stage == "prepared_record_persistence":
        original = tool_ctx.audit_admission_ledger.prepare

        def fault_prepare(*args, **kwargs):
            fail_once()
            return original(*args, **kwargs)

        monkeypatch.setattr(tool_ctx.audit_admission_ledger, "prepare", fault_prepare)
    elif stage in {"inventory_write", "authority_write", "post_write_pre_cas"}:
        original = materializer_module._write_or_verify

        def fault_write(effect, allowed_root):
            if not fault_hits and effect.artifact_kind == (
                "inventory" if stage == "inventory_write" else "authority"
            ):
                if stage == "post_write_pre_cas":
                    original(effect, allowed_root)
                fail_once()
            return original(effect, allowed_root)

        monkeypatch.setattr(materializer_module, "_write_or_verify", fault_write)
    elif stage == "cas":
        original = tool_ctx.audit_admission_ledger.commit_authority

        def fault_commit(*args, **kwargs):
            fail_once()
            return original(*args, **kwargs)

        monkeypatch.setattr(
            tool_ctx.audit_admission_ledger,
            "commit_authority",
            fault_commit,
        )
    elif stage == "success_effect_finalization":
        original = execution_module._complete_audit_finalization_effects

        def fault_finalization(*args, **kwargs):
            fail_once()
            return original(*args, **kwargs)

        monkeypatch.setattr(
            execution_module,
            "_complete_audit_finalization_effects",
            fault_finalization,
        )
    elif stage == "result_rewrite":
        original = execution_module.AuditResultOutcome

        def fault_rewrite(*args, **kwargs):
            fail_once()
            return original(*args, **kwargs)

        monkeypatch.setattr(execution_module, "AuditResultOutcome", fault_rewrite)
    elif stage == "response_shaping":
        original = execution_module.shape_execution_response

        def fault_shape(*args, **kwargs):
            fail_once()
            return original(*args, **kwargs)

        monkeypatch.setattr(execution_module, "shape_execution_response", fault_shape)
    elif stage == "response_commit":
        original = tool_ctx.audit_admission_ledger.finalize_response

        def fault_response_commit(*args, **kwargs):
            fail_once()
            return original(*args, **kwargs)

        monkeypatch.setattr(
            tool_ctx.audit_admission_ledger,
            "finalize_response",
            fault_response_commit,
        )
    else:  # pragma: no cover - parameter registry is intentionally closed
        raise AssertionError(f"unregistered fault stage: {stage}")
    return fault_hits


@pytest.mark.parametrize("stage", _STAGES)
async def test_staged_fault_retry_preserves_one_durable_audit_lifecycle(
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_ctx_kitchen_open,
) -> None:
    monkeypatch.setattr(tool_ctx_kitchen_open, "background", None)
    credential, step = await _install_attested_recipe(
        monkeypatch,
        tmp_path,
        tool_ctx_kitchen_open,
    )
    execution_id = str(credential["execution_id"])
    work_dir = tmp_path / "worktree"
    audit_root = resolve_temp_dir(
        work_dir,
        tool_ctx_kitchen_open.config.workspace.temp_dir,
    )
    plan_path = audit_root / "rectify" / "plan.md"
    deviation_manifest_path = audit_root / "implement" / "deviations.json"
    plan_path.parent.mkdir(parents=True)
    deviation_manifest_path.parent.mkdir(parents=True)
    plan_path.write_text("# Plan\n\nAudit admission retries retain one identity.\n")
    deviation_manifest_path.write_text("{}\n")

    dispatches: list[str] = []
    reservations: list[AuditIdentityReservation] = []

    async def run_child(resolved_command: str, _cwd: str, **_kwargs):
        dispatches.append(resolved_command)
        payload = json.loads(resolved_command.split(_BOUND_INVOCATION_MARKER, 1)[1])
        submission = payload["audit_semantic_submission"]
        reservation = tool_ctx_kitchen_open.audit_admission_ledger.resolve_reservation_handle(
            submission["reservation_handle"]
        )
        assert reservation is not None
        reservations.append(reservation)
        written = json.loads(
            await write_audit_semantic_result(
                reservation_handle=submission["reservation_handle"],
                audited_plan_refs=submission["audited_plan_refs"],
                assessments=[
                    {
                        "requirement_id": "REQ-4419",
                        "requirement_text": "Audit admission retry is deterministic.",
                        "assessment": "COVERED",
                        "evidence_summary": "The production seam retained one attempt.",
                    }
                ],
                verdict="GO",
            )
        )
        if not written["success"]:
            raise OSError(written["error"])
        child_result = _skill_ok("child result must not survive admission")
        child_result.outcome_fields = {
            "audit_semantic_result_path": written["audit_semantic_result_path"],
            "audit_cycle_path": "/child/forged.json",
            "audit_verdict": "NO GO",
        }
        return child_result

    monkeypatch.setattr(tool_ctx_kitchen_open.executor, "run", run_child)

    success_calls: list[str] = []
    clear_calls: list[Path] = []
    original_record_success = tool_ctx_kitchen_open.audit.record_success
    original_clear = audit_finalization_module.clear_run_skill_state

    def record_success(skill_command: str, *args, **kwargs):
        success_calls.append(skill_command)
        return original_record_success(skill_command, *args, **kwargs)

    def clear_state(project_dir: Path):
        clear_calls.append(project_dir)
        return original_clear(project_dir)

    monkeypatch.setattr(tool_ctx_kitchen_open.audit, "record_success", record_success)
    monkeypatch.setattr(
        audit_finalization_module,
        "clear_run_skill_state",
        clear_state,
    )
    fault_hits = _install_fault(stage, monkeypatch, tool_ctx_kitchen_open)

    with_args = step["with"]
    branch_name = "impl-audit-fault-immunity"
    invocation = {
        "skill_command": with_args["skill_command"],
        "cwd": str(work_dir),
        "step_name": with_args["step_name"],
        "output_dir": with_args["output_dir"],
        "recipe_execution_id": execution_id,
        "invocation_template_digest": credential["invocation_template_digests"][_STEP],
        "skill_inputs": {
            "all_plan_paths": str(plan_path),
            "deviation_manifest_path": str(deviation_manifest_path),
            "branch_name": branch_name,
            "base_branch": "develop",
            "prior_audit_cycle_path": "",
        },
        "closure_plan_paths": str(plan_path),
        "closure_base_sha": branch_name,
    }

    first = json.loads(await run_skill(**invocation))
    _ack_direct_run_skill_result(tool_ctx_kitchen_open, first)

    assert fault_hits == [stage]
    assert first["success"] is False
    assert not first.get("audit_cycle_path")
    assert not first.get("audit_verdict")
    assert len(reservations) == 1
    reservation = reservations[0]
    attempt_id = reservation.current_attempt_id.value
    database_path = (
        tool_ctx_kitchen_open.project_dir
        / ".autoskillit"
        / "temp"
        / "audit-admission"
        / "ledger.sqlite3"
    ).resolve()
    state_after_fault = _attempt_state(database_path, attempt_id)

    if stage in _REDISPATCH_STAGES:
        assert state_after_fault["lifecycle"] == AuditAttemptLifecycle.OPEN.value
        assert state_after_fault["prepared_effects"] == ()
    elif stage in _PREPARED_STAGES:
        assert state_after_fault["lifecycle"] == AuditAttemptLifecycle.PREPARED.value
        assert state_after_fault["prepared_effects"] == (
            "authority",
            "inventory",
            "semantic_result",
        )
    else:
        assert stage in _PUBLISHED_STAGES
        assert (
            state_after_fault["lifecycle"]
            == AuditAttemptLifecycle.PUBLISHED_PENDING_FINALIZATION.value
        )
        assert state_after_fault["head_count"] == 1
        assert state_after_fault["projection_count"] == 2

    expected_paths = {
        "semantic_acceptance": (False, False, False),
        "prepared_record_persistence": (True, False, False),
        "inventory_write": (True, False, False),
        "authority_write": (True, True, False),
        "post_write_pre_cas": (True, True, True),
        "cas": (True, True, True),
        "success_effect_finalization": (True, True, True),
        "result_rewrite": (True, True, True),
        "response_shaping": (True, True, True),
        "response_commit": (True, True, True),
    }[stage]
    assert (
        reservation.semantic_result_path.exists(),
        reservation.inventory_path.exists(),
        reservation.authority_path.exists(),
    ) == expected_paths

    retried = json.loads(await run_skill(**invocation))

    assert retried["success"] is True
    assert retried["audit_status"] == "PUBLISHED"
    assert retried["audit_verdict"] == "GO"
    assert retried["audit_attempt_id"] == attempt_id
    assert Path(retried["audit_cycle_path"]) == reservation.authority_path
    _ack_direct_run_skill_result(tool_ctx_kitchen_open, retried)
    expected_dispatches = 2 if stage in _REDISPATCH_STAGES else 1
    assert len(dispatches) == expected_dispatches
    if stage in _REDISPATCH_STAGES:
        assert len(reservations) == 2
        assert {item.current_attempt_id.value for item in reservations} == {attempt_id}
        assert {item.generated_at for item in reservations} == {reservation.generated_at}

    authority = AuditCycleVerifier(audit_root).load_authority(Path(retried["audit_cycle_path"]))
    assert authority.execution_generation == execution_id
    assert authority.generated_at == reservation.generated_at

    replayed = json.loads(await run_skill(**invocation))

    assert replayed["receipt_id"] != retried["receipt_id"]
    assert replayed == {
        **retried,
        "audit_status": "EXACT_REPLAY",
        "receipt_id": replayed["receipt_id"],
    }
    assert len(dispatches) == expected_dispatches
    assert success_calls == [invocation["skill_command"]]
    assert clear_calls == [tool_ctx_kitchen_open.project_dir]

    committed = _attempt_state(database_path, attempt_id)
    assert committed["lifecycle"] == AuditAttemptLifecycle.RESPONSE_COMMITTED.value
    assert committed["committed_outcome_json"] is not None
    assert committed["attempt_count"] == 1
    assert committed["head_count"] == 1
    assert committed["projection_count"] == 2
    assert committed["prepared_effects"] == (
        "authority",
        "inventory",
        "semantic_result",
    )
    assert committed["finalization_effects"] == (
        "audit_success_recorded",
        "run_skill_state_cleared",
    )


@pytest.mark.parametrize("barrier", ("replacement", "clear"))
async def test_active_installation_barrier_fences_prepared_authority_without_redispatch(
    barrier: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_ctx_kitchen_open,
) -> None:
    monkeypatch.setattr(tool_ctx_kitchen_open, "background", None)
    credential, step = await _install_attested_recipe(
        monkeypatch,
        tmp_path,
        tool_ctx_kitchen_open,
    )
    execution_id = str(credential["execution_id"])
    work_dir = tmp_path / "worktree"
    audit_root = resolve_temp_dir(
        work_dir,
        tool_ctx_kitchen_open.config.workspace.temp_dir,
    )
    plan_path = audit_root / "rectify" / "barrier-plan.md"
    deviation_manifest_path = audit_root / "implement" / "barrier-deviations.json"
    plan_path.parent.mkdir(parents=True)
    deviation_manifest_path.parent.mkdir(parents=True)
    plan_path.write_text("# Plan\n\nInstallation barriers fence final CAS.\n")
    deviation_manifest_path.write_text("{}\n")

    dispatches: list[str] = []
    reservations: list[AuditIdentityReservation] = []

    async def run_child(resolved_command: str, _cwd: str, **_kwargs):
        dispatches.append(resolved_command)
        payload = json.loads(resolved_command.split(_BOUND_INVOCATION_MARKER, 1)[1])
        submission = payload["audit_semantic_submission"]
        reservation = tool_ctx_kitchen_open.audit_admission_ledger.resolve_reservation_handle(
            submission["reservation_handle"]
        )
        assert reservation is not None
        reservations.append(reservation)
        written = json.loads(
            await write_audit_semantic_result(
                reservation_handle=submission["reservation_handle"],
                audited_plan_refs=submission["audited_plan_refs"],
                assessments=[
                    {
                        "requirement_id": "REQ-4419",
                        "requirement_text": "Installation occurrence fences final CAS.",
                        "assessment": "COVERED",
                        "evidence_summary": "The active installation changed after prepare.",
                    }
                ],
                verdict="GO",
            )
        )
        assert written["success"] is True
        child_result = _skill_ok("prepared child semantics")
        child_result.outcome_fields = {
            "audit_semantic_result_path": written["audit_semantic_result_path"]
        }
        return child_result

    monkeypatch.setattr(tool_ctx_kitchen_open.executor, "run", run_child)
    original_commit = tool_ctx_kitchen_open.audit_admission_ledger.commit_authority
    barrier_hits: list[str] = []

    def cross_installation_barrier(request):
        if not barrier_hits:
            barrier_hits.append(barrier)
            if barrier == "clear":
                clear_recipe_execution(tool_ctx_kitchen_open)
            else:
                state = tool_ctx_kitchen_open.recipe_initialization_state
                assert isinstance(state, ReadyRecipe)
                current = state.installed_execution.snapshot
                replacement_snapshot = build_recipe_execution_snapshot(
                    recipe_name=current.recipe_name,
                    content_hash=current.content_hash,
                    composite_hash=current.composite_hash,
                    projection=RecipeBindingProjection(
                        {name: template.invocation for name, template in current.templates.items()}
                    ),
                )
                stage_recipe_initialization(
                    tool_ctx_kitchen_open,
                    recipe_name=state.recipe_name,
                    artifact_generation=state.artifact_generation,
                    flow_generation=state.flow_generation,
                    initialization_id="fault-immunity-replacement",
                    staged_snapshot=replacement_snapshot,
                    requirements=(),
                    generation_store_key=state.generation_store_key,
                    finalized_projection=state.finalized_projection,
                )
                prepared = prepare_recipe_execution(
                    tool_ctx_kitchen_open,
                    snapshot=replacement_snapshot,
                )
                install_recipe_execution(
                    tool_ctx_kitchen_open,
                    prepared_execution=prepared,
                    completion_receipt="fault-immunity-replacement-receipt",
                )
        return original_commit(request)

    monkeypatch.setattr(
        tool_ctx_kitchen_open.audit_admission_ledger,
        "commit_authority",
        cross_installation_barrier,
    )

    with_args = step["with"]
    branch_name = "impl-audit-installation-barrier"
    invocation = {
        "skill_command": with_args["skill_command"],
        "cwd": str(work_dir),
        "step_name": with_args["step_name"],
        "output_dir": with_args["output_dir"],
        "recipe_execution_id": execution_id,
        "invocation_template_digest": credential["invocation_template_digests"][_STEP],
        "skill_inputs": {
            "all_plan_paths": str(plan_path),
            "deviation_manifest_path": str(deviation_manifest_path),
            "branch_name": branch_name,
            "base_branch": "develop",
            "prior_audit_cycle_path": "",
        },
        "closure_plan_paths": str(plan_path),
        "closure_base_sha": branch_name,
    }

    conflicted = json.loads(await run_skill(**invocation))

    assert barrier_hits == [barrier]
    assert conflicted["success"] is False
    assert conflicted["audit_status"] == "CONFLICT"
    assert not conflicted["audit_cycle_path"]
    assert not conflicted["audit_verdict"]
    assert len(dispatches) == 1
    assert len(reservations) == 1
    reservation = reservations[0]
    database_path = (
        tool_ctx_kitchen_open.project_dir
        / ".autoskillit"
        / "temp"
        / "audit-admission"
        / "ledger.sqlite3"
    ).resolve()
    prepared_state = _attempt_state(
        database_path,
        reservation.current_attempt_id.value,
    )
    assert prepared_state["lifecycle"] == AuditAttemptLifecycle.PREPARED.value
    assert prepared_state["prepared_effects"] == (
        "authority",
        "inventory",
        "semantic_result",
    )
    assert prepared_state["head_count"] == 0
    assert prepared_state["projection_count"] == 0
    assert prepared_state["finalization_effects"] == ()

    rejected_retry = json.loads(await run_skill(**invocation))

    assert rejected_retry["success"] is False
    assert len(dispatches) == 1
    after_retry = _attempt_state(
        database_path,
        reservation.current_attempt_id.value,
    )
    assert after_retry == prepared_state
