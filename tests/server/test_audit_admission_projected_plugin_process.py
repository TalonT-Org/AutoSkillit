"""Cross-process proof for parent-owned audit admission publication."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autoskillit.core import (
    AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR,
    AUTOSKILLIT_STATE_ROOT_ENV_VAR,
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    AuditCycleVerifier,
    PluginLoadMode,
    RecipeExecutionId,
)
from autoskillit.core.io import resolve_temp_dir
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.server._recipe_execution import get_recipe_execution
from autoskillit.server.tools.tools_execution import run_skill
from autoskillit.server.tools.tools_recipe import complete_recipe_initialization
from autoskillit.workspace import project_default_plugin_authority
from tests.server._helpers import (
    _credit_initialization_sections,
    _open_kitchen_patched,
    _pull_step_section,
    _skill_ok,
)
from tests.server._pipeline_test_helpers import _ack_direct_run_skill_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_RECIPE = "remediation"
_STEP = "audit_impl"
_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4587",
    "task_description": "prove parent-owned audit authority across a projected process",
}
_BOUND_INVOCATION_MARKER = "AUTOSKILLIT_BOUND_INVOCATION_V1\n"


def _initialize_git_root(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".gitignore").write_text(".autoskillit/\n")
    for command in (
        ("git", "init", "-q"),
        ("git", "config", "user.email", "audit-probe@example.invalid"),
        ("git", "config", "user.name", "Audit Probe"),
        ("git", "add", "."),
        ("git", "commit", "-qm", "probe"),
    ):
        subprocess.run(command, cwd=path, check=True, timeout=10)


async def _install_attested_recipe(
    monkeypatch: pytest.MonkeyPatch,
    parent_project: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    monkeypatch.chdir(parent_project)
    envelope = await _open_kitchen_patched(_RECIPE, _OVERRIDES, monkeypatch)
    assert envelope["success"] is True
    await _credit_initialization_sections(envelope)
    step = await _pull_step_section(envelope, _STEP)
    receipt = json.loads(await complete_recipe_initialization(envelope["initialization_id"]))
    assert receipt["success"] is True
    credential = receipt[RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY]
    assert isinstance(credential, dict)
    return credential, step


def _tool_result_json(result: object) -> dict[str, object]:
    content = getattr(result, "content")
    assert len(content) == 1
    text = getattr(content[0], "text")
    decoded = json.loads(text)
    assert isinstance(decoded, dict)
    return decoded


async def test_projected_plugin_child_publishes_through_parent_audit_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_ctx_kitchen_open,
) -> None:
    from fastmcp.client import Client
    from fastmcp.client.transports import StdioTransport

    parent_project = tmp_path / "parent-project"
    clone = tmp_path / "recipe-clone"
    _initialize_git_root(parent_project)
    _initialize_git_root(clone)
    credential, step = await _install_attested_recipe(monkeypatch, parent_project)

    installed = get_recipe_execution(tool_ctx_kitchen_open)
    assert installed is not None
    execution_id = credential["execution_id"]
    execution_key = RecipeExecutionId(installed.snapshot.execution_id)
    parent_ledger = tool_ctx_kitchen_open.audit_admission_ledger
    parent_authority = parent_ledger.store_authority

    clone_audit_root = resolve_temp_dir(
        clone,
        tool_ctx_kitchen_open.config.workspace.temp_dir,
    )
    plan_path = clone_audit_root / "rectify" / "plan.md"
    deviation_manifest_path = clone_audit_root / "implement" / "deviations.json"
    plan_path.parent.mkdir(parents=True)
    deviation_manifest_path.parent.mkdir(parents=True)
    plan_path.write_text("# Plan\n\nThe parent owns audit admission.\n")
    deviation_manifest_path.write_text("{}\n")

    reservation_outcomes = []
    original_reserve = type(parent_ledger).reserve

    def _record_reserve(self, request):
        outcome = original_reserve(self, request)
        if self is parent_ledger:
            reservation_outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(type(parent_ledger), "reserve", _record_reserve)

    plugin_authority = project_default_plugin_authority(cwd=clone)
    dispatches: list[dict[str, object]] = []

    async def _run_child(resolved_command: str, cwd: str, **kwargs):
        assert Path(cwd) == clone.resolve()
        provider_extras = kwargs["provider_extras"]
        assert provider_extras[AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR] == str(
            parent_authority.database_path
        )

        backend = ClaudeCodeBackend()
        with plugin_authority.acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ) as binding:
            assert binding.plugin_dir is not None
            mcp_config = json.loads((binding.plugin_dir / ".mcp.json").read_text())
            server = mcp_config["mcpServers"]["autoskillit"]
            spec = backend.build_skill_session_cmd(
                resolved_command,
                cwd,
                plugin_binding=binding,
                provider_extras=provider_extras,
            )
            assert spec.env["AUTOSKILLIT_CWD"] == str(clone.resolve())
            assert spec.env[AUTOSKILLIT_STATE_ROOT_ENV_VAR] == str(clone.resolve())
            assert spec.env[AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR] == str(
                parent_authority.database_path
            )
            transport = StdioTransport(
                command=server["command"],
                args=server.get("args", []),
                env=spec.env,
                cwd=str(clone.resolve()),
            )
            payload = json.loads(resolved_command.split(_BOUND_INVOCATION_MARKER, 1)[1])
            submission = payload["audit_semantic_submission"]
            async with Client(transport) as client:
                written = _tool_result_json(
                    await client.call_tool(
                        "write_audit_semantic_result",
                        {
                            "reservation_handle": submission["reservation_handle"],
                            "audited_plan_refs": submission["audited_plan_refs"],
                            "assessments": [
                                {
                                    "requirement_id": "REQ-4587",
                                    "requirement_text": "The parent owns admission.",
                                    "assessment": "COVERED",
                                    "evidence_summary": (
                                        "The independent child published through the parent "
                                        "handle."
                                    ),
                                }
                            ],
                            "verdict": "GO",
                        },
                    )
                )
            assert written["success"] is True
            dispatches.append(
                {
                    "cwd": cwd,
                    "handle": submission["reservation_handle"],
                    "semantic_path": written["audit_semantic_result_path"],
                }
            )

        child_result = _skill_ok("Substantive audit analysis completed with verdict GO.")
        child_result.outcome_fields = {
            "audit_semantic_result_path": written["audit_semantic_result_path"],
            "audit_verdict": "GO",
        }
        return child_result

    monkeypatch.setattr(tool_ctx_kitchen_open.executor, "run", _run_child)

    with_args = step["with"]
    branch_name = "impl-parent-audit-authority"
    invocation = {
        "skill_command": with_args["skill_command"],
        "cwd": str(clone),
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
    published_raw = await run_skill(**invocation)
    published = json.loads(published_raw)

    assert len(reservation_outcomes) == 1
    assert len(dispatches) == 1
    reservation = reservation_outcomes[0].reservation
    assert reservation is not None
    handle = dispatches[0]["handle"]
    assert isinstance(handle, str)
    assert handle.split(".", 2)[:2] == ["adr1", parent_authority.authority_id]
    assert reservation.allowed_root.is_relative_to(clone.resolve())
    semantic_path = Path(str(dispatches[0]["semantic_path"]))
    assert semantic_path.is_relative_to(clone.resolve())
    assert semantic_path == reservation.semantic_result_path
    assert not (clone_audit_root / "audit-admission" / "ledger.sqlite3").exists()

    assert published["success"] is True
    assert published["audit_status"] == "PUBLISHED"
    assert published["audit_verdict"] == "GO"
    authority = AuditCycleVerifier(clone_audit_root).load_authority(
        Path(published["audit_cycle_path"])
    )
    head = parent_ledger.current_head(
        recipe_execution_id=execution_key,
        cycle_id=authority.cycle_id,
        scope_id=authority.scope_id,
        part_id=authority.part_id,
    )
    assert head is not None
    assert head.current_authority_digest == authority.authority_digest
    attempt_id = reservation.current_attempt_id
    assert (
        parent_ledger.finalization_effect_result(attempt_id, "audit_success_recorded") is not None
    )
    assert (
        parent_ledger.finalization_effect_result(attempt_id, "run_skill_state_cleared") is not None
    )

    _ack_direct_run_skill_result(tool_ctx_kitchen_open, published)
    replay_raw = await run_skill(**invocation)
    replay = json.loads(replay_raw)

    assert len(reservation_outcomes) == 1
    assert len(dispatches) == 1
    assert replay["audit_status"] == "EXACT_REPLAY"
    assert replay["audit_cycle_path"] == published["audit_cycle_path"]
    assert replay["audit_attempt_id"] == published["audit_attempt_id"]
    assert replay["audit_verdict"] == published["audit_verdict"]
    assert replay["receipt_id"] != published["receipt_id"]
    expected_replay = dict(published)
    expected_replay.pop("receipt_id")
    expected_replay["audit_status"] = "EXACT_REPLAY"
    replay_without_receipt = dict(replay)
    replay_without_receipt.pop("receipt_id")
    assert replay_without_receipt == expected_replay
