"""Deterministic contracts for MCP startup recovery policy and rendering."""

from __future__ import annotations

import pytest

from autoskillit.cli import _prompts
from tests.cli._mcp_startup_recovery_harness import (
    McpStartupRecoveryHarness,
    assert_quiet_bounded_trace,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


_KNOWN_BAD_FIVE_CASE_CONTRACT = (
    "RECIPE STARTUP RESULT ORDER — evaluate these cases in order:\n"
    "1. A pre-dispatch symbol-resolution error (for example, No such tool or an "
    "AutoSkillit-generated bad symbol) requires runtime tool discovery, then one "
    "dispatch using the discovered symbol.\n"
    "2. A discovery, transport, unavailable, tool_use_error, or isError:true "
    "failure requires one silent retry. Only if that retry also fails at transport "
    'may you output "AutoSkillit MCP server did not start — ending session." and end.\n'
    "3. An isError:false MCP response is a structured application result, including "
    "when its JSON contains success:false. Parse it before applying failure rules.\n"
    "4. If the application result contains a recovery manifest, process it before "
    "any generic success:false branch: preserve recipe_pull, recipe_flow, "
    "initialization_id, required_sections, page-plan and pagination identities; "
    "pull every flow_records page in order, then the entrypoint named-step pages. "
    "Forward every advertised immutable identity plus initialization_id, "
    "page_plan_sha256, part, and continuation; reject mismatched versions, digests, "
    "sizes, records, skipped parts, or changed bindings. After exact reconstruction, "
    "call complete_recipe_initialization(initialization_id) and require its receipt "
    "before the first execution or mutation tool.\n"
    "5. Only a structured, nonrecoverable application error is terminal. Print its "
    "user_visible_message verbatim (or the raw application result if absent), do "
    "not call AskUserQuestion, and do not diagnose it as MCP startup failure."
)


def test_startup_policy_is_attempt_bounded_and_has_one_terminal_message() -> None:
    spec = _prompts._MCP_STARTUP_RECOVERY_SPEC

    assert spec.attempt_cap > 1
    assert spec.render().count(spec.exhaustion_message) == 1


@pytest.mark.parametrize("attempt", range(1, 3))
def test_every_pre_dispatch_failure_retries_before_the_cap(attempt: int) -> None:
    event = _prompts._MCP_STARTUP_RECOVERY_SPEC.reduce_pre_dispatch_failure(attempt)

    assert event.kind is _prompts.McpStartupRecoveryEventKind.RETRY
    assert event.attempt == attempt
    assert event.message is None


def test_pre_dispatch_failure_exhausts_exactly_at_the_cap() -> None:
    spec = _prompts._MCP_STARTUP_RECOVERY_SPEC

    event = spec.reduce_pre_dispatch_failure(spec.attempt_cap)

    assert event.kind is _prompts.McpStartupRecoveryEventKind.EXHAUSTED
    assert event.message == spec.exhaustion_message


@pytest.mark.parametrize(
    "kind",
    [
        _prompts.McpStartupRecoveryEventKind.TOOL_ERROR_RESULT,
        _prompts.McpStartupRecoveryEventKind.APPLICATION_RESULT,
    ],
)
def test_received_results_never_reenter_pre_dispatch_recovery(
    kind: _prompts.McpStartupRecoveryEventKind,
) -> None:
    event = _prompts._MCP_STARTUP_RECOVERY_SPEC.reduce_received_result(kind)

    assert event.kind is kind
    assert event.attempt is None


def test_rendered_contract_has_explicit_phase_boundary_and_all_silence_atoms() -> None:
    rendered = _prompts._MCP_STARTUP_RECOVERY_SPEC.render()
    pre_dispatch = rendered.split("POST-RECEIPT", maxsplit=1)[0]

    assert "PRE-DISPATCH" in pre_dispatch
    assert "every failure" in pre_dispatch.lower()
    assert "before classifying" in pre_dispatch.lower()
    assert "do not explain" in pre_dispatch.lower()
    assert "do not troubleshoot" in pre_dispatch.lower()
    assert "do not output a free-text question" in pre_dispatch.lower()
    assert "do not call AskUserQuestion" in pre_dispatch


def test_known_bad_five_case_contract_fails_clause_validation() -> None:
    failures = _prompts._MCP_STARTUP_RECOVERY_SPEC.validate_rendered(_KNOWN_BAD_FIVE_CASE_CONTRACT)

    assert "MCP-PRE-UNIVERSAL-RETRY" in failures
    assert "MCP-PRE-NO-EXPLANATION" in failures
    assert "MCP-PRE-NO-TROUBLESHOOTING" in failures
    assert "MCP-PRE-NO-FREE-TEXT-QUESTION" in failures


@pytest.mark.parametrize(
    ("clause_id", "replacement"),
    [
        ("MCP-PRE-UNIVERSAL-RETRY", "some failures"),
        ("MCP-PRE-NO-EXPLANATION", "may explain"),
        ("MCP-PRE-NO-TROUBLESHOOTING", "may troubleshoot"),
        ("MCP-PRE-NO-FREE-TEXT-QUESTION", "may output a free-text question"),
        ("MCP-PRE-NO-ASK-USER", "may call AskUserQuestion"),
    ],
)
def test_required_semantic_atom_mutation_fails_its_own_clause(
    clause_id: str,
    replacement: str,
) -> None:
    spec = _prompts._MCP_STARTUP_RECOVERY_SPEC
    clause = next(clause for clause in spec.clauses if clause.clause_id == clause_id)
    mutant = spec.render().replace(clause.text, replacement)

    assert clause_id in spec.validate_rendered(mutant)


def test_unrelated_decoy_vocabulary_cannot_satisfy_missing_clause() -> None:
    spec = _prompts._MCP_STARTUP_RECOVERY_SPEC
    clause = next(
        clause for clause in spec.clauses if clause.clause_id == "MCP-PRE-NO-EXPLANATION"
    )
    mutant = spec.render().replace(clause.render(), "")
    mutant += "\nUnrelated: do not explain recipe errors after startup retry."

    assert clause.clause_id in spec.validate_rendered(mutant)


def test_clause_relocation_to_wrong_phase_fails_its_owner() -> None:
    spec = _prompts._MCP_STARTUP_RECOVERY_SPEC
    clause = next(clause for clause in spec.clauses if clause.clause_id == "MCP-PRE-NO-ASK-USER")
    mutant = spec.render().replace(clause.render(), "")
    mutant += f"\n{clause.render()}"

    assert clause.clause_id in spec.validate_rendered(mutant)


def test_contradictory_duplicate_with_same_clause_identity_fails() -> None:
    spec = _prompts._MCP_STARTUP_RECOVERY_SPEC
    clause = next(
        clause for clause in spec.clauses if clause.clause_id == "MCP-PRE-UNIVERSAL-RETRY"
    )
    mutant = spec.render() + f"\n[{clause.clause_id}] Some pre-dispatch failures may be explained."

    assert clause.clause_id in spec.validate_rendered(mutant)


def test_canonical_instruction_is_rendered_from_the_policy() -> None:
    assert _prompts._MCP_RETRY_INSTRUCTION == _prompts._MCP_STARTUP_RECOVERY_SPEC.render()


def test_immediate_success_has_no_retry_or_user_visible_event() -> None:
    harness = McpStartupRecoveryHarness()

    harness.received_result(_prompts.McpStartupRecoveryEventKind.APPLICATION_RESULT)

    assert harness.dispatch_attempts == 1
    assert harness.user_visible_events == []
    assert_quiet_bounded_trace(harness)


@pytest.mark.parametrize("success_attempt", [2, 3])
def test_success_on_each_later_attempt_stops_dispatch(
    success_attempt: int,
) -> None:
    harness = McpStartupRecoveryHarness()

    for _ in range(1, success_attempt):
        event = harness.pre_dispatch_failure()
        assert event.kind is _prompts.McpStartupRecoveryEventKind.RETRY
    harness.received_result(_prompts.McpStartupRecoveryEventKind.APPLICATION_RESULT)

    assert harness.dispatch_attempts == success_attempt
    with pytest.raises(RuntimeError, match="terminal"):
        harness.pre_dispatch_failure()
    assert_quiet_bounded_trace(harness)


def test_exhaustion_emits_the_fixed_message_once_and_stops_dispatch() -> None:
    harness = McpStartupRecoveryHarness()

    for _ in range(harness.spec.attempt_cap):
        harness.pre_dispatch_failure()

    assert harness.events[-1].kind is _prompts.McpStartupRecoveryEventKind.EXHAUSTED
    with pytest.raises(RuntimeError, match="terminal"):
        harness.pre_dispatch_failure()
    assert_quiet_bounded_trace(harness)
