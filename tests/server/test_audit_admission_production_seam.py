"""Production-seam regression for attested parent-owned audit publication."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core import (
    AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR,
    AUTOSKILLIT_STATE_ROOT_ENV_VAR,
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    AuditCycleVerifier,
    RecipeExecutionId,
)
from autoskillit.core.io import resolve_temp_dir
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.fleet._capture import _extract_captures
from autoskillit.server._audit_authority_materializer import (
    DefaultAuditAuthorityMaterializer,
)
from autoskillit.server._recipe_execution import get_recipe_execution
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
_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4419",
    "task_description": "prove parent-owned audit authority at the production seam",
}
_BOUND_INVOCATION_MARKER = "AUTOSKILLIT_BOUND_INVOCATION_V1\n"


async def _install_attested_recipe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_ctx,
) -> tuple[dict[str, object], dict[str, object]]:
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


async def test_attested_run_skill_materializes_publishes_captures_and_exact_replays(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_ctx_kitchen_open,
) -> None:
    credential, step = await _install_attested_recipe(
        monkeypatch,
        tmp_path,
        tool_ctx_kitchen_open,
    )
    installed = get_recipe_execution(tool_ctx_kitchen_open)
    assert installed is not None
    execution_id = credential["execution_id"]
    assert installed.snapshot.execution_id == execution_id
    execution_key = RecipeExecutionId(installed.snapshot.execution_id)
    assert isinstance(
        tool_ctx_kitchen_open.audit_authority_materializer,
        DefaultAuditAuthorityMaterializer,
    )

    work_dir = tmp_path / "worktree"
    audit_root = resolve_temp_dir(
        work_dir,
        tool_ctx_kitchen_open.config.workspace.temp_dir,
    )
    plan_path = audit_root / "rectify" / "plan.md"
    deviation_manifest_path = audit_root / "implement" / "deviations.json"
    plan_path.parent.mkdir(parents=True)
    deviation_manifest_path.parent.mkdir(parents=True)
    plan_path.write_text("# Plan\n\nParent owns the audit authority identity.\n")
    deviation_manifest_path.write_text("{}\n")

    dispatch_prompts: list[str] = []
    hostile_authority_path = "/hostile/provider/ledger.sqlite3"
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.is_feature_enabled",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *args, **kwargs: (
            "vertex",
            {AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR: hostile_authority_path},
        ),
    )

    async def _run_child(resolved_command: str, _cwd: str, **kwargs):
        dispatch_prompts.append(resolved_command)
        provider_extras = kwargs["provider_extras"]
        trusted_authority_path = str(
            tool_ctx_kitchen_open.audit_admission_ledger.store_authority.database_path
        )
        assert provider_extras[AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR] == trusted_authority_path
        child_spec = ClaudeCodeBackend().build_skill_session_cmd(
            "/autoskillit:audit-impl",
            str(work_dir),
            completion_marker="DONE",
            provider_extras=provider_extras,
        )
        assert child_spec.cwd == str(work_dir)
        assert child_spec.env["AUTOSKILLIT_CWD"] == str(work_dir)
        assert child_spec.env[AUTOSKILLIT_STATE_ROOT_ENV_VAR] == str(work_dir)
        assert child_spec.env[AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR] == trusted_authority_path
        assert str(execution_id) not in resolved_command
        payload = json.loads(resolved_command.split(_BOUND_INVOCATION_MARKER, 1)[1])
        submission = payload["audit_semantic_submission"]
        written = json.loads(
            await write_audit_semantic_result(
                reservation_handle=submission["reservation_handle"],
                audited_plan_refs=submission["audited_plan_refs"],
                assessments=[
                    {
                        "requirement_id": "REQ-4419",
                        "requirement_text": "The parent owns audit authority identity.",
                        "assessment": "COVERED",
                        "evidence_summary": (
                            "The child submitted semantic findings through an opaque handle."
                        ),
                    }
                ],
                verdict="GO",
            )
        )
        assert written["success"] is True
        child_result = _skill_ok("child-authored result must be replaced")
        child_result.outcome_fields = {
            "audit_semantic_result_path": written["audit_semantic_result_path"],
            "audit_cycle_path": "/child/forged-authority.json",
            "audit_verdict": "NO GO",
        }
        return child_result

    monkeypatch.setattr(tool_ctx_kitchen_open.executor, "run", _run_child)

    with_args = step["with"]
    branch_name = "impl-audit-authority"
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
    published = json.loads(await run_skill(**invocation))

    assert len(dispatch_prompts) == 1, published
    assert published["success"] is True
    assert published["result"] == "Server-authored audit outcome: PUBLISHED"
    assert published["audit_status"] == "PUBLISHED"
    assert published["audit_verdict"] == "GO"
    assert published["audit_cycle_path"] != "/child/forged-authority.json"
    assert published["audit_attempt_id"]

    authority_path = Path(published["audit_cycle_path"])
    authority = AuditCycleVerifier(audit_root).load_authority(authority_path)
    assert authority.execution_generation == installed.snapshot.execution_id
    head = tool_ctx_kitchen_open.audit_admission_ledger.current_head(
        recipe_execution_id=execution_key,
        cycle_id=authority.cycle_id,
        scope_id=authority.scope_id,
        part_id=authority.part_id,
    )
    assert head is not None
    assert head.current_authority_digest == authority.authority_digest
    projection = tool_ctx_kitchen_open.audit_admission_ledger.preflight_projection(
        recipe_execution_id=execution_key,
        installation_version=installed.installation_version,
        step_name="dry_walkthrough",
    )
    assert projection is not None
    assert projection.plan_set_id == authority.plan_set_id

    active_steps = tool_ctx_kitchen_open.active_recipe_steps
    assert active_steps is not None
    captured = _extract_captures(active_steps[_STEP].capture, published)
    assert captured["audit_cycle_path"] == published["audit_cycle_path"]
    assert captured["audit_status"] == "PUBLISHED"
    assert captured["audit_verdict"] == "GO"
    assert captured["audit_attempt_id"] == published["audit_attempt_id"]

    _ack_direct_run_skill_result(tool_ctx_kitchen_open, published)

    replay = json.loads(await run_skill(**invocation))

    assert len(dispatch_prompts) == 1
    assert replay["success"] is True
    assert replay["audit_status"] == "EXACT_REPLAY"
    assert replay["audit_verdict"] == published["audit_verdict"]
    assert replay["audit_cycle_path"] == published["audit_cycle_path"]
    assert replay["audit_attempt_id"] == published["audit_attempt_id"]
    assert replay["receipt_id"] != published["receipt_id"]


async def test_substantive_go_without_semantic_publication_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_ctx_kitchen_open,
) -> None:
    credential, step = await _install_attested_recipe(monkeypatch, tmp_path)
    work_dir = tmp_path / "worktree"
    audit_root = resolve_temp_dir(
        work_dir,
        tool_ctx_kitchen_open.config.workspace.temp_dir,
    )
    plan_path = audit_root / "rectify" / "plan.md"
    deviation_manifest_path = audit_root / "implement" / "deviations.json"
    plan_path.parent.mkdir(parents=True)
    deviation_manifest_path.parent.mkdir(parents=True)
    plan_path.write_text("# Plan\n\nSubstantive audit input.\n")
    deviation_manifest_path.write_text("{}\n")

    async def _run_child(_resolved_command: str, _cwd: str, **_kwargs):
        child_result = _skill_ok("Substantive analysis completed with verdict GO.")
        child_result.outcome_fields = {"audit_verdict": "GO"}
        return child_result

    monkeypatch.setattr(tool_ctx_kitchen_open.executor, "run", _run_child)

    with_args = step["with"]
    execution_id = credential["execution_id"]
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
            "branch_name": "impl-missing-semantic-publication",
            "base_branch": "develop",
            "prior_audit_cycle_path": "",
        },
        "closure_plan_paths": str(plan_path),
        "closure_base_sha": "impl-missing-semantic-publication",
    }

    rejected = json.loads(await run_skill(**invocation))

    assert rejected["success"] is False
    assert rejected["audit_status"] == "SEMANTIC_REJECTED"
    assert rejected["audit_verdict"] is None
    assert rejected["audit_cycle_path"] is None
    assert "audit_semantic_result_path" not in rejected
