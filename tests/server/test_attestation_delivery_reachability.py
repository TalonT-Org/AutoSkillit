"""Differential delivery-mode reachability for the recipe execution credential.

Drives the real ``open_kitchen``, ``get_recipe_section``, ``complete_recipe_initialization``,
and ``run_skill`` tool functions and asserts the credential is reachable from returned JSON
alone. ``ATTESTED_INLINE`` is driven at ``finalize_recipe_delivery`` — the same function
``open_kitchen`` calls — because no MCP tool signature accepts host attestation evidence.
No test here reads ``payload.json`` or any file under ``recipe-delivery/``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from unittest.mock import Mock

import pytest

from autoskillit._recipe_delivery_framing import RECIPE_BODY_START
from autoskillit.core import (
    RECIPE_EXECUTION_CREDENTIAL_WIRE_FIELDS,
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    RecipeDeliveryMode,
)
from autoskillit.pipeline import ReadyRecipe
from autoskillit.server._recipe_execution import (
    RecipeExecutionAdmissionError,
    install_recipe_execution,
)
from tests.conftest import _make_result
from tests.server._pipeline_test_helpers import _write_tracker
from tests.server.test_tools_recipe_pull import (
    _NOW,
    _attestation,
    _evidence,
    _finalize_recipe_delivery,
    _ledger,
    _payload,
    _protected_codex_backend,
    _request,
    _test_projection,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_RECIPE_ENVELOPE = "remediation"
_RECIPE_INLINE_SMALL = "promote-to-main-wrapper"
_ATTESTED_STEP = "investigate"
_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4411",
    "task_description": "test task",
}


def _write_attested_tracker(ready, with_args: Mapping[str, object]) -> None:
    step_name = with_args["step_name"]
    assert isinstance(step_name, str)
    _write_tracker(
        ready.tool_ctx.project_dir,
        "AB",
        {step_name: {"status": "pending"}},
        {},
        kitchen_id=ready.tool_ctx.kitchen_id,
    )


async def test_bounded_initialization_delivers_attestation_credential(
    tool_ctx_ready_recipe,
) -> None:
    """REQ-031: the bounded envelope path puts the credential on the completion receipt."""
    ready = tool_ctx_ready_recipe

    assert ready.receipt["success"] is True
    credential = ready.credential
    assert set(credential) == RECIPE_EXECUTION_CREDENTIAL_WIRE_FIELDS
    digests = credential["invocation_template_digests"]
    assert digests, "invocation_template_digests is empty"
    for value in digests.values():
        assert isinstance(value, str)
        assert value.startswith("sha256:")
        assert len(value) == len("sha256:") + 64
        assert value.removeprefix("sha256:").islower()
    assert _ATTESTED_STEP in digests
    assert credential["execution_id"]
    assert isinstance(ready.tool_ctx.recipe_initialization_state, ReadyRecipe)


async def test_ready_execution_replacement_rejects_mismatched_audit_ledger(
    tool_ctx_ready_recipe,
) -> None:
    ready = tool_ctx_ready_recipe
    state = ready.tool_ctx.recipe_initialization_state
    assert isinstance(state, ReadyRecipe)
    mismatched = replace(state.installed_execution, audit_admission_ledger=Mock())

    with pytest.raises(RecipeExecutionAdmissionError, match="different audit admission ledger"):
        install_recipe_execution(ready.tool_ctx, prepared_execution=mismatched)


async def test_attested_run_skill_succeeds_using_only_delivered_values(
    tmp_path,
    tool_ctx_ready_recipe,
) -> None:
    """REQ-032: the attested ``run_skill`` must be drivable from received values alone."""
    from autoskillit.server.tools.tools_execution import run_skill

    ready = tool_ctx_ready_recipe
    with_args = ready.with_args
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_attested_tracker(ready, with_args)
    ready.tool_ctx.runner.push(_make_result(returncode=1))
    ready.tool_ctx.runner.push(
        _make_result(
            0,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "done",
                    "session_id": "session-1",
                }
            ),
            "",
        )
    )
    calls_before = len(ready.tool_ctx.runner.call_args_list)

    result = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=with_args["step_name"],
            output_dir=with_args["output_dir"],
            recipe_execution_id=ready.credential["execution_id"],
            invocation_template_digest=ready.template_digest,
            skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
        )
    )

    serialized = json.dumps(result)
    assert "recipe_execution_attestation_missing" not in serialized
    assert "RECIPE EXECUTION REJECTED" not in serialized
    assert result.get("stage") != "preflight:recipe_execution"
    assert len(ready.tool_ctx.runner.call_args_list) > calls_before


async def test_attested_run_skill_admits_explicit_order_id(
    tmp_path,
    tool_ctx_ready_recipe,
) -> None:
    """#4402/T3.a — order_id is ORCHESTRATOR_SCOPING: an attested call may pass
    it explicitly alongside the delivered protocol values without denial (the
    #4296 escape hatch), and the passed value reaches the executor ahead of
    the AUTOSKILLIT_DISPATCH_ID env fallback (effective_order_id = order_id or
    env, tools_execution.py). Unreachable for attested calls before #4402 —
    order_id had no with:-declared template entry, so any non-empty value was
    an "undeclared effective name" and the gate denied it outright.

    model/stale_threshold RecipeStep-fallback e2e coverage (T3.b/T3.c) lives
    in test_run_skill_execution_tuning_fallbacks.py instead of here: that
    fallback block runs whenever step_name and tool_ctx.active_recipe_steps
    are set, independent of attestation status, and the real "remediation"
    recipe's investigate step declares only a templated model: field (bare
    aliases, #4412 — out of scope here) and no stale_threshold: field at all,
    so it cannot pin a literal fallback value without a bespoke fixture
    recipe this plan does not add.
    """
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    ready = tool_ctx_ready_recipe
    executor = InMemoryHeadlessExecutor()
    ready.tool_ctx.executor = executor
    with_args = ready.with_args
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_attested_tracker(ready, with_args)

    result = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=with_args["step_name"],
            output_dir=with_args["output_dir"],
            recipe_execution_id=ready.credential["execution_id"],
            invocation_template_digest=ready.template_digest,
            skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
            order_id="AB",
        )
    )

    serialized = json.dumps(result)
    assert "recipe_execution_attestation_missing" not in serialized
    assert "RECIPE EXECUTION REJECTED" not in serialized
    assert result.get("stage") != "preflight:recipe_execution"
    assert len(executor.calls) == 1
    assert executor.calls[0].order_id == "AB"


async def test_attested_run_skill_never_forwards_an_unresolved_model_template(
    tmp_path,
    tool_ctx_ready_recipe,
) -> None:
    """#4402 remediation — restores T3.b's original intent against the real
    recipe, catching the defect the synthetic-literal-model unit tests in
    test_run_skill_execution_tuning_fallbacks.py structurally could not see.

    The real remediation.yaml investigate step declares
    ``model: ${{ 'opus[1m]' if inputs.depth == 'deep' else 'sonnet' }}`` — a
    template load_recipe() never interpolates (it's a thin YAML parse; see
    the output_dir fallback's identical "${{" guard). Since sous-chef now
    mandates omitting model from attested calls, every real invocation of
    this step reaches the RecipeStep.model fallback. Before the fix, the
    fallback had no template guard (unlike its output_dir sibling) and would
    forward the raw, broken template string straight to --model.
    """
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    ready = tool_ctx_ready_recipe
    executor = InMemoryHeadlessExecutor()
    ready.tool_ctx.executor = executor
    with_args = ready.with_args
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    result = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=with_args["step_name"],
            output_dir=with_args["output_dir"],
            recipe_execution_id=ready.credential["execution_id"],
            invocation_template_digest=ready.template_digest,
            skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
        )
    )

    assert result.get("stage") != "preflight:recipe_execution", result
    assert len(executor.calls) == 1
    assert executor.calls[0].model == "", (
        "unresolved recipe model must preserve the executor's vacancy sentinel, got "
        f"{executor.calls[0].model!r}"
    )


async def test_attested_run_skill_reports_missing_canonical_tool_def(
    tmp_path,
    tool_ctx_ready_recipe,
    monkeypatch,
) -> None:
    """A missing canonical run_skill definition remains a loud runtime invariant."""
    from autoskillit.server.tools import tools_execution

    ready = tool_ctx_ready_recipe
    with_args = ready.with_args
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.setattr(tools_execution, "get_tool_def", lambda _name: None)

    result = json.loads(
        await tools_execution.run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=with_args["step_name"],
            output_dir=with_args["output_dir"],
            recipe_execution_id=ready.credential["execution_id"],
            invocation_template_digest=ready.template_digest,
            skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
        )
    )

    assert result["success"] is False
    assert result["subtype"] == "crashed"
    assert "RuntimeError: run_skill must be a registered ToolDef" in result["result"]


async def test_tool_ctx_ready_recipe_fixture_yields_genuine_attestation(
    tmp_path,
    tool_ctx_ready_recipe,
) -> None:
    """#4402/T8 — tool_ctx_ready_recipe must yield a genuinely attested context,
    not a mock of one: its installed snapshot's template digest round-trips
    through a real ``run_skill`` admission — the genuine digest succeeds, a
    fabricated one is denied at ``preflight:recipe_execution``. A mock that
    accepted any digest would pass the first half and fail to distinguish
    the second.
    """
    from autoskillit.server.tools.tools_execution import run_skill

    ready = tool_ctx_ready_recipe
    with_args = ready.with_args
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    ready.tool_ctx.runner.push(_make_result(returncode=1))
    ready.tool_ctx.runner.push(
        _make_result(
            0,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "done",
                    "session_id": "session-1",
                }
            ),
            "",
        )
    )

    genuine = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=with_args["step_name"],
            output_dir=with_args["output_dir"],
            recipe_execution_id=ready.credential["execution_id"],
            invocation_template_digest=ready.template_digest,
            skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
        )
    )
    assert genuine.get("stage") != "preflight:recipe_execution", genuine

    fabricated = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=with_args["step_name"],
            output_dir=with_args["output_dir"],
            recipe_execution_id=ready.credential["execution_id"],
            invocation_template_digest="sha256:" + "0" * 64,
            skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
        )
    )
    assert fabricated.get("stage") == "preflight:recipe_execution", fabricated


@pytest.mark.parametrize("mode", list(RecipeDeliveryMode), ids=lambda m: m.value)
async def test_no_delivery_mode_omits_the_attestation_credential(
    mode: RecipeDeliveryMode,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    forbid_artifact_reads,
    tool_ctx_kitchen_open,
) -> None:
    """REQ-033: each delivery mode places the credential at a caller-visible location."""
    from tests.server._helpers import _credit_initialization_sections, _open_kitchen_patched

    monkeypatch.chdir(tmp_path)
    tool_ctx_kitchen_open.session_serve_overrides = None
    tool_ctx_kitchen_open.session_serve_defer_unresolved = False
    tool_ctx_kitchen_open.recipe_name = ""
    arm = forbid_artifact_reads

    match mode:
        case RecipeDeliveryMode.ORDINARY_INLINE:
            arm()
            envelope = await _open_kitchen_patched(_RECIPE_INLINE_SMALL, _OVERRIDES, monkeypatch)
            assert envelope.get("success") is True
            assert not envelope.get("delivery_bound_spill")
            block = envelope[RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY]
        case RecipeDeliveryMode.ATTESTED_INLINE:
            from autoskillit.recipe import _api_cache
            from autoskillit.recipe._api_cache import LoadCache

            monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
            # The real ``remediation`` recipe's full payload (104 KB body +
            # 98 KB flow_records + ~250 KB metadata) renders to ~453 KB —
            # far above Codex's authoritative attested budget of 56_750 bytes.
            # Use a synthetic small payload that fits the ATTESTED_INLINE
            # window while still exercising the real finalize path with a
            # non-trivial body, projection, and flow records.
            payload = _payload("remediation body\n" + ("x" * 30_000))
            payload["recipe_name"] = _RECIPE_ENVELOPE
            tool_ctx_kitchen_open.backend = _protected_codex_backend()
            arm()
            finalized = _finalize_recipe_delivery(
                payload,
                surface="open_kitchen",
                recipe_name=_RECIPE_ENVELOPE,
                tool_ctx=tool_ctx_kitchen_open,
                finalized_projection=_test_projection(),
                delivery_request=_request(),
                attestation=_attestation(),
                supported_evidence=_evidence(),
                receipt_ledger=_ledger(tmp_path),
                now_unix=_NOW,
            )
            assert finalized.decision.mode is RecipeDeliveryMode.ATTESTED_INLINE
            assert finalized.rendered is not None
            control_text = finalized.rendered.split(RECIPE_BODY_START, 1)[0]
            control = json.loads(control_text)
            block = control["recipe_delivery"]["payload_metadata"][
                RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY
            ]
        case RecipeDeliveryMode.ENVELOPE:
            envelope = await _open_kitchen_patched(_RECIPE_ENVELOPE, _OVERRIDES, monkeypatch)
            assert envelope["success"] is True
            assert envelope["delivery_bound_spill"] is True
            assert RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY not in envelope
            await _credit_initialization_sections(envelope)
            arm()
            from autoskillit.server.tools.tools_recipe import complete_recipe_initialization

            receipt = json.loads(
                await complete_recipe_initialization(envelope["initialization_id"])
            )
            block = receipt[RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY]
        case _ as unreachable:
            pytest.fail(f"unhandled delivery mode: {unreachable!r}")

    assert set(block) == RECIPE_EXECUTION_CREDENTIAL_WIRE_FIELDS
