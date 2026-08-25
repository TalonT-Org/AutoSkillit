"""Tests for persisted audit/skill contract validation and gate-error schema normalization (split from test_tools_execution_results.py per issue #4796)."""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import (
    AuditAttemptId,
    AuditMaterializationResult,
    AuditMaterializationStatus,
    AuditOutcomeStatus,
    AuditVerdict,
)
from autoskillit.server.tools.tools_execution import _complete_resumed_audit

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_resumed_audit_finalization_is_retry_safe_and_persists_replay(
    tool_ctx_kitchen_open,
    monkeypatch,
) -> None:
    class FlakyFinalizer:
        def __init__(self) -> None:
            self.calls = 0
            self.outcomes = []
            self.effects = {}

        def finalization_effect_result(self, attempt_id, effect_name):
            return self.effects.get((attempt_id, effect_name))

        def acknowledge_finalization_effect(
            self,
            attempt_id,
            effect_name,
            result,
        ) -> None:
            self.effects[(attempt_id, effect_name)] = result

        def finalize_response(
            self,
            attempt_id,
            outcome,
            *,
            required_effect_names,
        ) -> None:
            assert set(required_effect_names) == {
                "audit_success_recorded",
                "run_skill_state_cleared",
            }
            assert all(
                (attempt_id, effect_name) in self.effects for effect_name in required_effect_names
            )
            self.calls += 1
            self.outcomes.append(outcome)
            if self.calls == 1:
                raise OSError("simulated response-commit fault")

    finalizer = FlakyFinalizer()
    monkeypatch.setattr(tool_ctx_kitchen_open, "audit_admission_ledger", finalizer)
    tool_ctx_kitchen_open.audit.clear()
    result = AuditMaterializationResult(
        status=AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION,
        attempt_id=AuditAttemptId("attempt-1"),
        verdict=AuditVerdict.GO,
        path=tool_ctx_kitchen_open.project_dir / "authority.json",
        error=None,
    )

    with pytest.raises(OSError, match="response-commit fault"):
        _complete_resumed_audit(
            tool_ctx_kitchen_open,
            result=result,
            skill_command="/autoskillit:audit-impl",
        )
    response = json.loads(
        _complete_resumed_audit(
            tool_ctx_kitchen_open,
            result=result,
            skill_command="/autoskillit:audit-impl",
        )
    )

    success_records = [
        record
        for record in tool_ctx_kitchen_open.audit.get_report()
        if record.subtype == "success"
    ]
    assert len(success_records) == 1
    assert {name for (_, name) in finalizer.effects} == {
        "audit_success_recorded",
        "run_skill_state_cleared",
    }
    assert response["audit_status"] == AuditOutcomeStatus.PUBLISHED.value
    committed = finalizer.outcomes[-1]
    assert committed.replay_response_json is not None
    replay = json.loads(committed.replay_response_json)
    assert replay["audit_status"] == AuditOutcomeStatus.EXACT_REPLAY.value
    assert replay["kill_reason"] == response["kill_reason"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("read_only", "false"),
        ("completion_required", 1),
    ],
)
def test_deserialize_skill_contract_rejects_non_boolean_authority(
    field: str,
    value: object,
) -> None:
    from autoskillit.core import SkillContractError
    from autoskillit.server.tools._execution_helpers import deserialize_skill_contract

    payload = json.dumps({"inputs": [], "outputs": [], field: value})

    with pytest.raises(SkillContractError, match="Persisted skill execution contract"):
        deserialize_skill_contract(payload)


def test_deserialize_skill_contract_rejects_non_mapping_payload() -> None:
    from autoskillit.core import SkillContractError
    from autoskillit.server.tools._execution_helpers import deserialize_skill_contract

    with pytest.raises(SkillContractError, match="Persisted skill execution contract"):
        deserialize_skill_contract("[]")


def test_persisted_audit_contract_preserves_selected_output_mode() -> None:
    from autoskillit.server.tools import _execution_helpers as helpers

    selected = helpers.SkillContract(
        inputs=(),
        outputs=[
            helpers.SkillOutput(name="audit_status", type="str"),
            helpers.SkillOutput(name="standalone_evidence_path", type="file_path"),
            helpers.SkillOutput(name="content_digest", type="str"),
        ],
        audit_output_mode=helpers.AuditOutputMode.STANDALONE,
    )
    restored = helpers.deserialize_skill_contract(helpers.serialize_skill_contract(selected))

    assert restored is not None
    assert restored.audit_output_mode is helpers.AuditOutputMode.STANDALONE
    assert restored.audit_authority_publication is None
    assert {output.name for output in restored.outputs} == {
        "audit_status",
        "standalone_evidence_path",
        "content_digest",
    }


@pytest.mark.parametrize(
    ("input_type", "value"),
    [("str", ""), ("integer", 0), ("boolean", False)],
)
def test_persisted_skill_contract_preserves_falsey_absence_value(
    input_type: str,
    value: str | int | bool,
) -> None:
    from autoskillit.server.tools import _execution_helpers as helpers

    selected = helpers.SkillContract(
        inputs=(
            helpers.SkillInput(
                name="value",
                type=input_type,
                required=False,
                absence_value=value,
            ),
        ),
        outputs=[],
    )

    restored = helpers.deserialize_skill_contract(helpers.serialize_skill_contract(selected))

    assert restored is not None
    restored_default = restored.inputs[0].absence_value
    assert restored_default == value
    assert type(restored_default) is type(value)


class TestGateErrorSchemaNormalization:
    """Gate errors use the standard 9-field response schema."""

    def test_require_enabled_gate_returns_standard_schema(self, tool_ctx):
        """Gate errors must use the same schema as normal responses."""
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._guards import _require_enabled

        tool_ctx.gate = DefaultGateState(enabled=False)
        gate_result = _require_enabled()
        assert gate_result is not None
        response = json.loads(gate_result)
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["needs_retry"] is False
        assert "result" in response

    def test_dry_walkthrough_gate_returns_standard_schema(self, tool_ctx, tmp_path):
        """Dry-walkthrough gate errors must use the standard response schema."""
        from autoskillit.server._guards import _check_dry_walkthrough

        plan = tmp_path / "plan.md"
        plan.write_text("No marker here")
        skill_cmd = f"/implement-worktree {plan}"
        result = _check_dry_walkthrough(skill_cmd, str(tmp_path))
        assert result is not None
        response = json.loads(result)
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["subtype"] == "gate_error"