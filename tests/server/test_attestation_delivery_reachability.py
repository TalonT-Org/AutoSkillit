"""Differential delivery-mode reachability for the recipe execution credential.

Drives the real ``open_kitchen``, ``get_recipe_section``, ``complete_recipe_initialization``,
and ``run_skill`` tool functions and asserts the credential is reachable from returned JSON
alone. ``ATTESTED_INLINE`` is driven at ``finalize_recipe_delivery`` — the same function
``open_kitchen`` calls — because no MCP tool signature accepts host attestation evidence.
No test here reads ``payload.json`` or any file under ``recipe-delivery/``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from autoskillit._recipe_delivery_framing import RECIPE_BODY_START
from autoskillit.core import (
    RECIPE_EXECUTION_CREDENTIAL_WIRE_FIELDS,
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    RecipeDeliveryMode,
)
from autoskillit.pipeline import ReadyRecipe
from tests.conftest import _make_result
from tests.server._helpers import (
    _credit_initialization_sections,
    _open_kitchen_patched,
    _pull_step_section,
)
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
_RECIPE_ORDINARY = "full-audit"
_ATTESTED_STEP = "investigate"
_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4411",
    "task_description": "test task",
}


@pytest.fixture()
def forbid_artifact_reads(monkeypatch: pytest.MonkeyPatch):
    """Return an arming callable that fails any persisted-artifact read."""
    import autoskillit.server.tools.tools_recipe as tools_recipe

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("credential must come from responses, not payload.json")

    return lambda: monkeypatch.setattr(tools_recipe, "load_recipe_artifact", _forbidden)


async def _drive_bounded_initialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    arm,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Drive the bounded envelope flow through real tools, returning (receipt, step_body).

    The poison is armed AFTER the pull is complete so that ``get_recipe_section`` (which
    legitimately reads the artifact to serve pages) continues to work; only
    ``complete_recipe_initialization`` and ``run_skill`` are poisoned thereafter.
    """
    monkeypatch.chdir(tmp_path)
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server.tools.tools_recipe import complete_recipe_initialization

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
    envelope = await _open_kitchen_patched(_RECIPE_ENVELOPE, _OVERRIDES, monkeypatch)
    assert envelope["success"] is True
    assert envelope["delivery_bound_spill"] is True
    assert envelope["recipe_pull"]["pull_tool"] == "get_recipe_section"
    assert RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY not in envelope
    assert isinstance(envelope["initialization_id"], str) and envelope["initialization_id"]

    await _credit_initialization_sections(envelope)
    step_body = await _pull_step_section(envelope, _ATTESTED_STEP)
    arm()
    receipt = json.loads(await complete_recipe_initialization(envelope["initialization_id"]))
    return receipt, step_body


async def test_bounded_initialization_delivers_attestation_credential(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    forbid_artifact_reads,
    tool_ctx_kitchen_open,
) -> None:
    """REQ-031: the bounded envelope path puts the credential on the completion receipt."""
    arm = forbid_artifact_reads
    receipt, _step_body = await _drive_bounded_initialization(monkeypatch, tmp_path, arm)

    assert receipt["success"] is True
    credential = receipt[RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY]
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
    assert isinstance(tool_ctx_kitchen_open.recipe_initialization_state, ReadyRecipe)


async def test_attested_run_skill_succeeds_using_only_delivered_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    forbid_artifact_reads,
    tool_ctx_kitchen_open,
) -> None:
    """REQ-032: the attested ``run_skill`` must be drivable from received values alone."""
    from autoskillit.server.tools.tools_execution import run_skill

    arm = forbid_artifact_reads
    receipt, step_body = await _drive_bounded_initialization(monkeypatch, tmp_path, arm)

    credential = receipt[RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY]
    with_args = step_body["with"]
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))
    tool_ctx_kitchen_open.runner.push(
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
    calls_before = len(tool_ctx_kitchen_open.runner.call_args_list)

    result = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=with_args["step_name"],
            output_dir=with_args["output_dir"],
            recipe_execution_id=credential["execution_id"],
            invocation_template_digest=credential["invocation_template_digests"][_ATTESTED_STEP],
            skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
        )
    )

    serialized = json.dumps(result)
    assert "recipe_execution_attestation_missing" not in serialized
    assert "RECIPE EXECUTION REJECTED" not in serialized
    assert result.get("stage") != "preflight:recipe_execution"
    assert len(tool_ctx_kitchen_open.runner.call_args_list) > calls_before


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
            envelope = await _open_kitchen_patched(_RECIPE_ORDINARY, _OVERRIDES, monkeypatch)
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
