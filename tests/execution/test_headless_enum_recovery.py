"""Tests for contract-aware enum-token recovery (Part A of #4305 rectify).

Covers deterministic enum inference (_parse_single_enum_binding,
_infer_enum_token_from_write_contract) and generalized enum nudge hints
(_extract_missing_token_hints, _attempt_contract_nudge).
"""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import RetryReason, SkillResult
from autoskillit.execution.backends.claude import ClaudeResultParser
from autoskillit.execution.headless import (
    _attempt_contract_nudge,
    _extract_missing_token_hints,
    _infer_enum_token_from_write_contract,
    _parse_single_enum_binding,
)
from autoskillit.execution.session import ClaudeSessionResult
from autoskillit.recipe._contracts_types import SkillContract, SkillOutput
from autoskillit.recipe.contracts import get_skill_contract, load_bundled_manifest
from tests.conftest import _make_result
from tests.execution.conftest import _mock_backend, _success_session_json

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_plan_contract(
    *,
    write_behavior: str | None = "conditional",
    write_expected_when: list[str] | None = None,
    verdict_allowed_values: list[str] | None = None,
) -> SkillContract:
    """Build a make-plan-shaped SkillContract (verdict enum bound to a conditional write)."""
    return SkillContract(
        inputs=(),
        outputs=[
            SkillOutput(
                name="verdict",
                type="string",
                allowed_values=(
                    verdict_allowed_values
                    if verdict_allowed_values is not None
                    else ["plan", "false_positive"]
                ),
            ),
            SkillOutput(name="plan_path", type="file_path"),
            SkillOutput(name="plan_parts", type="file_path_list"),
        ],
        expected_output_patterns=[r"verdict[ \t]*=[ \t]*(plan|false_positive)"],
        write_behavior=write_behavior,
        write_expected_when=(
            write_expected_when
            if write_expected_when is not None
            else [r"verdict[ \t]*=[ \t]*plan"]
        ),
    )


def _write_ndjson(result_text: str, file_path: str) -> str:
    content = [
        {"type": "tool_use", "name": "Write", "id": "t0", "input": {"file_path": file_path}}
    ]
    records = [
        json.dumps({"type": "assistant", "message": {"content": content}}),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": result_text,
                "session_id": "test-session",
            }
        ),
    ]
    return "\n".join(records)


class TestParseSingleEnumBinding:
    def test_sound_single_binding_returns_token_and_value(self):
        contract = _make_plan_contract()
        assert _parse_single_enum_binding(contract) == ("verdict", "plan")

    def test_no_binding_for_none_contract(self):
        assert _parse_single_enum_binding(None) is None

    def test_no_binding_for_unbound_contract(self):
        """audit-impl shape: no write_expected_when at all."""
        contract = _make_plan_contract(write_behavior=None, write_expected_when=[])
        assert _parse_single_enum_binding(contract) is None

    def test_no_binding_for_multi_entry_write_expected_when(self):
        contract = _make_plan_contract(
            write_expected_when=[r"verdict[ \t]*=[ \t]*plan", r"other[ \t]*=[ \t]*x"]
        )
        assert _parse_single_enum_binding(contract) is None

    def test_no_binding_for_alternation_value(self):
        contract = _make_plan_contract(write_expected_when=[r"verdict[ \t]*=[ \t]*(plan|other)"])
        assert _parse_single_enum_binding(contract) is None

    def test_no_binding_when_value_not_in_allowed_values(self):
        contract = _make_plan_contract(write_expected_when=[r"verdict[ \t]*=[ \t]*archived"])
        assert _parse_single_enum_binding(contract) is None


class TestInferEnumTokenFromWriteContract:
    def test_infers_when_companion_path_exists_on_disk(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("plan body")
        contract = _make_plan_contract()
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"plan_path = {plan_file}\nplan_parts = {plan_file}\n%%ORDER_UP%%",
            session_id="test-session",
        )
        result = _infer_enum_token_from_write_contract(
            session,
            contract.expected_output_patterns,
            contract,
            write_call_count=1,
        )
        assert result is not None
        assert "verdict = plan" in result.result

    def test_no_inference_without_write_evidence(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("plan body")
        contract = _make_plan_contract()
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"plan_path = {plan_file}\nplan_parts = {plan_file}\n%%ORDER_UP%%",
            session_id="test-session",
        )
        result = _infer_enum_token_from_write_contract(
            session,
            contract.expected_output_patterns,
            contract,
            write_call_count=0,
            file_changes=(),
        )
        assert result is None

    def test_no_inference_when_companion_file_missing(self, tmp_path):
        missing_path = tmp_path / "does_not_exist.md"
        contract = _make_plan_contract()
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"plan_path = {missing_path}\nplan_parts = {missing_path}\n%%ORDER_UP%%",
            session_id="test-session",
        )
        result = _infer_enum_token_from_write_contract(
            session,
            contract.expected_output_patterns,
            contract,
            write_call_count=1,
        )
        assert result is None

    def test_no_inference_for_unbound_contract(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("plan body")
        contract = _make_plan_contract(write_behavior=None, write_expected_when=[])
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"plan_path = {plan_file}\nplan_parts = {plan_file}\n%%ORDER_UP%%",
            session_id="test-session",
        )
        result = _infer_enum_token_from_write_contract(
            session,
            contract.expected_output_patterns,
            contract,
            write_call_count=1,
        )
        assert result is None

    @pytest.mark.parametrize(
        "write_expected_when",
        [
            [r"verdict[ \t]*=[ \t]*plan", r"other[ \t]*=[ \t]*x"],
            [r"verdict[ \t]*=[ \t]*(plan|other)"],
        ],
        ids=["two-entries", "alternation-value"],
    )
    def test_no_inference_for_multi_binding_or_complex_pattern(
        self, tmp_path, write_expected_when
    ):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("plan body")
        contract = _make_plan_contract(write_expected_when=write_expected_when)
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"plan_path = {plan_file}\nplan_parts = {plan_file}\n%%ORDER_UP%%",
            session_id="test-session",
        )
        result = _infer_enum_token_from_write_contract(
            session,
            contract.expected_output_patterns,
            contract,
            write_call_count=1,
        )
        assert result is None

    def test_no_inference_when_value_not_in_allowed_values(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("plan body")
        contract = _make_plan_contract(write_expected_when=[r"verdict[ \t]*=[ \t]*archived"])
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"plan_path = {plan_file}\nplan_parts = {plan_file}\n%%ORDER_UP%%",
            session_id="test-session",
        )
        result = _infer_enum_token_from_write_contract(
            session,
            contract.expected_output_patterns,
            contract,
            write_call_count=1,
        )
        assert result is None


class TestBuildSkillResultIncidentReproduction:
    """Full-stack _build_skill_result reproduction of the #4305 incident shape."""

    def test_incident_reproduction_enum_inference_recovers(self, tmp_path):
        from autoskillit.core.types import ChannelConfirmation
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.execution.headless import _build_skill_result

        plan_file = tmp_path / "plan.md"
        plan_file.write_text("plan body")
        contract = _make_plan_contract()

        result_text = f"plan_path = {plan_file}\nplan_parts = {plan_file}\nsummary\n%%ORDER_UP%%"
        stdout = _write_ndjson(result_text, str(plan_file))
        subprocess_result = _make_result(
            stdout=stdout, channel_confirmation=ChannelConfirmation.CHANNEL_A
        )

        sr = _build_skill_result(
            subprocess_result,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=contract.expected_output_patterns,
            backend=ClaudeCodeBackend(),
            skill_contract=contract,
        )

        assert sr.success is True
        assert sr.retry_reason == RetryReason.NONE
        assert "verdict = plan" in sr.result

    def test_enum_inference_after_path_synthesis_unmonitored(self, tmp_path):
        from autoskillit.core.types import ChannelConfirmation
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.execution.headless import _build_skill_result

        plan_file = tmp_path / "plan.md"
        plan_file.write_text("plan body")
        contract = _make_plan_contract()

        # No plan_path/plan_parts token lines in the result text — only the write
        # tool_use carries the path. Include a path-capture pattern so the existing
        # UNMONITORED synthesis stage fires and injects plan_path first.
        expected_patterns = [*contract.expected_output_patterns, r"plan_path\s*=\s*/.+"]
        result_text = "summary\n%%ORDER_UP%%"
        stdout = _write_ndjson(result_text, str(plan_file))
        subprocess_result = _make_result(
            stdout=stdout, channel_confirmation=ChannelConfirmation.UNMONITORED
        )

        sr = _build_skill_result(
            subprocess_result,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=expected_patterns,
            backend=ClaudeCodeBackend(),
            skill_contract=contract,
        )

        assert sr.success is True
        assert sr.retry_reason == RetryReason.NONE
        assert "verdict = plan" in sr.result
        assert f"plan_path = {plan_file}" in sr.result


class TestEnumHintGeneration:
    def test_enum_hint_generated_for_missing_enum_token(self):
        contract = _make_plan_contract()
        stdout = _write_ndjson("plan summary\n%%ORDER_UP%%", "/tmp/plan.md")
        hints = _extract_missing_token_hints(
            stdout,
            contract.expected_output_patterns,
            ClaudeResultParser(),
            frozenset({"Write", "Edit"}),
            skill_contract=contract,
        )
        assert len(hints) == 1
        hint = hints[0]
        from autoskillit.execution.headless._headless_recovery import _EnumHint

        assert isinstance(hint, _EnumHint)
        assert hint.token == "verdict"
        assert set(hint.allowed_values) == {"plan", "false_positive"}

    def test_path_hint_behavior_unchanged(self):
        """Regression: missing path-capture token still produces the (token, path) hint."""
        stdout = _write_ndjson("plan summary\n%%ORDER_UP%%", "/tmp/out.md")
        hints = _extract_missing_token_hints(
            stdout,
            [r"plan_path\s*=\s*/.+"],
            ClaudeResultParser(),
            frozenset({"Write", "Edit"}),
        )
        assert hints == [("plan_path", "/tmp/out.md")]


class TestEnumNudgeIntegration:
    @pytest.mark.anyio
    async def test_enum_nudge_success_requires_pattern_match(self, tmp_path):
        from tests.fakes import MockSubprocessRunner

        contract = _make_plan_contract()
        backend = _mock_backend(session_resume_capable=True)

        skill_result = SkillResult(
            success=False,
            result="plan summary",
            session_id="test-session",
            subtype="adjudicated_failure",
            is_error=False,
            exit_code=0,
            needs_retry=True,
            retry_reason=RetryReason.CONTRACT_RECOVERY,
            stderr="",
        )
        subprocess_result = _make_result(
            stdout=_write_ndjson("plan summary\n%%ORDER_UP%%", "/tmp/plan.md")
        )

        runner = MockSubprocessRunner()
        runner.push(_make_result(stdout=_success_session_json("verdict = plan\n%%ORDER_UP%%")))

        patched = await _attempt_contract_nudge(
            skill_result,
            subprocess_result,
            contract.expected_output_patterns,
            "%%ORDER_UP%%",
            str(tmp_path),
            runner,
            backend=backend,
            result_parser=ClaudeResultParser(),
            skill_contract=contract,
        )
        assert patched is not None
        assert patched.success is True
        assert "verdict = plan" in patched.result

        runner.push(
            _make_result(stdout=_success_session_json("not the required token\n%%ORDER_UP%%"))
        )

        rejected = await _attempt_contract_nudge(
            skill_result,
            subprocess_result,
            contract.expected_output_patterns,
            "%%ORDER_UP%%",
            str(tmp_path),
            runner,
            backend=backend,
            result_parser=ClaudeResultParser(),
            skill_contract=contract,
        )
        assert rejected is None


class TestBundledContractActivation:
    """Real-bundled-manifest activation proofs for #4305 Part A metadata (skill_contracts.yaml)."""

    def test_validate_audit_contract_enables_enum_inference(self):
        manifest = load_bundled_manifest()
        contract = get_skill_contract("validate-audit", manifest)
        assert _parse_single_enum_binding(contract) == ("verdict", "validated")

    def test_validate_test_audit_contract_enables_enum_inference(self):
        manifest = load_bundled_manifest()
        contract = get_skill_contract("validate-test-audit", manifest)
        assert _parse_single_enum_binding(contract) == ("verdict", "validated")

    def test_audit_impl_contract_enables_enum_nudge_hint(self):
        manifest = load_bundled_manifest()
        contract = get_skill_contract("audit-impl", manifest)
        assert contract is not None

        assert _parse_single_enum_binding(contract) is None

        stdout = _write_ndjson("no verdict token here\n%%ORDER_UP%%", "")
        hints = _extract_missing_token_hints(
            stdout,
            contract.expected_output_patterns,
            ClaudeResultParser(),
            frozenset({"Write", "Edit"}),
            skill_contract=contract,
        )
        assert len(hints) == 1
        hint = hints[0]
        from autoskillit.execution.headless._headless_recovery import _EnumHint

        assert isinstance(hint, _EnumHint)
        assert hint.token == "verdict"
        assert set(hint.allowed_values) == {"GO", "NO GO"}
