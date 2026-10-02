"""Regression cases pinned to the external hook protocol contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._hook_protocol_oracle import (
    CLAUDE_EXIT2_BLOCKING_EVENTS,
    CLAUDE_HOOK_SPECIFIC_OUTPUT_FIELDS,
    CODEX_EVENTS,
    CODEX_OUTPUT_EVENTS,
    CODEX_PROTOCOL_SOURCE,
    CODEX_PROTOCOL_VERSION,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_STOPPED,
    claude_verdict,
    codex_verdict,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


@pytest.mark.parametrize("event", ["PreCompact", "PostCompact"])
def test_codex_compact_halt_preserves_system_message(event: str) -> None:
    # codex-rs/hooks/src/events/compact.rs:260-278 records warnings before stopping.
    verdict = codex_verdict(
        event,
        exit_code=0,
        stdout=json.dumps(
            {"continue": False, "stopReason": "stop now", "systemMessage": "limit reached"}
        ),
        stderr="",
    )

    assert verdict.status == STATUS_STOPPED
    assert verdict.message == "stop now"
    assert verdict.system_message == "limit reached"


@pytest.mark.parametrize(
    (
        "event",
        "exit_code",
        "stdout",
        "stderr",
        "status",
        "message",
        "contexts",
        "updated_input",
    ),
    [
        pytest.param(
            "Stop",
            2,
            "",
            "   ",
            STATUS_FAILED,
            None,
            (),
            None,
            id="exit_code_two_without_stderr_does_not_block",
        ),
        pytest.param(
            "Stop",
            2,
            '{"decision":"block","reason":"stdout"}',
            "keep going",
            STATUS_BLOCKED,
            "keep going",
            (),
            None,
            id="exit_code_two_uses_stderr_feedback_only",
        ),
        pytest.param(
            "SessionStart",
            0,
            '{"hookSpecificOutput":{"hookEventName":"SessionStart"',
            "",
            STATUS_FAILED,
            None,
            (),
            None,
            id="invalid_json_like_stdout_fails_instead_of_becoming_model_context",
        ),
        pytest.param(
            "SessionStart",
            0,
            "  resume context  ",
            "",
            STATUS_COMPLETED,
            None,
            ("resume context",),
            None,
            id="plain_stdout_becomes_model_context",
        ),
        pytest.param(
            "PostToolUse",
            0,
            '{"hookSpecificOutput":{"hookEventName":"PostToolUse","updatedMCPToolOutput":{"ok":true}}}',
            "",
            STATUS_FAILED,
            None,
            (),
            None,
            id="unsupported_updated_mcp_tool_output_fails_open",
        ),
        pytest.param(
            "PostToolUse",
            0,
            (
                '{"hookSpecificOutput":{"hookEventName":"PostToolUse",'
                '"additionalContext":"inspect result"}}'
            ),
            "",
            STATUS_COMPLETED,
            None,
            ("inspect result",),
            None,
            id="additional_context_is_recorded",
        ),
        pytest.param(
            "PreToolUse",
            0,
            '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}',
            "",
            STATUS_FAILED,
            None,
            (),
            None,
            id="permission_decision_allow_without_updated_input_fails_open",
        ),
        pytest.param(
            "PreToolUse",
            0,
            '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask"}}',
            "",
            STATUS_FAILED,
            None,
            (),
            None,
            id="unsupported_permission_decision_fails_open",
        ),
        pytest.param(
            "Stop",
            0,
            "still working",
            "",
            STATUS_FAILED,
            None,
            (),
            None,
            id="invalid_stdout_fails_instead_of_silently_nooping",
        ),
        pytest.param(
            "Stop",
            0,
            '{"decision":"block","reason":"  "}',
            "",
            STATUS_FAILED,
            None,
            (),
            None,
            id="block_decision_with_blank_reason_fails_instead_of_blocking",
        ),
        pytest.param(
            "PreToolUse",
            0,
            "diagnostic only",
            "",
            STATUS_COMPLETED,
            None,
            (),
            None,
            id="plain_stdout_is_ignored",
        ),
        pytest.param(
            "PreCompact",
            0,
            '{"continue":false,"stopReason":"nope"}',
            "",
            STATUS_STOPPED,
            "nope",
            (),
            None,
            id="continue_false_stops_before_compaction",
        ),
        pytest.param(
            "PreCompact",
            0,
            '{"decision":"block","reason":"policy blocked compaction"}',
            "",
            STATUS_FAILED,
            None,
            (),
            None,
            id="block_decision_is_not_supported_for_pre_compact",
        ),
    ],
)
def test_codex_source_cases(
    event: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    status: str,
    message: str | None,
    contexts: tuple[str, ...],
    updated_input: object | None,
) -> None:
    verdict = codex_verdict(event, exit_code=exit_code, stdout=stdout, stderr=stderr)
    assert verdict.status == status
    assert verdict.message == message
    assert verdict.contexts == contexts
    assert verdict.updated_input == updated_input


@pytest.mark.parametrize(
    ("event", "stdout", "status", "contexts"),
    [
        pytest.param(
            "PreToolUse",
            (
                '{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
                '"additionalContext":"review policy"}}'
            ),
            STATUS_COMPLETED,
            ("review policy",),
            id="pre_tool_use_additional_context_only_is_model_context",
        ),
        pytest.param(
            "SessionStart",
            '{"additionalContext":"old top-level shape"}',
            STATUS_FAILED,
            (),
            id="session_start_top_level_additional_context_is_rejected",
        ),
    ],
)
def test_codex_shape_cases(
    event: str, stdout: str, status: str, contexts: tuple[str, ...]
) -> None:
    verdict = codex_verdict(event, exit_code=0, stdout=stdout, stderr="")
    assert verdict.status == status
    assert verdict.contexts == contexts


def test_stop_exit_two_ignores_stdout_when_stderr_is_empty() -> None:
    verdict = codex_verdict(
        "Stop",
        exit_code=2,
        stdout='{"decision":"block","reason":"stdout cannot block"}',
        stderr="",
    )
    assert verdict.status == STATUS_FAILED
    assert verdict.message is None


def test_codex_protocol_tracks_minimum_cli_version() -> None:
    from autoskillit.execution.backends._codex_discovery import CODEX_CLI_MIN_VERSION

    schema_dir = (
        Path(__file__).parents[1] / "fixtures" / "codex_hook_protocol" / CODEX_PROTOCOL_VERSION
    )
    schemas = sorted(schema_dir.glob("*.command.*.schema.json"))
    assert CODEX_PROTOCOL_VERSION == CODEX_CLI_MIN_VERSION
    assert "rust-v0.156.1" in CODEX_PROTOCOL_SOURCE
    assert "b412ff32c417f855c2b2d1581b77058eed87c84b" in CODEX_PROTOCOL_SOURCE
    assert schemas
    assert all("$schema" in json.loads(path.read_text(encoding="utf-8")) for path in schemas)

    input_events = {
        "".join(part.capitalize() for part in path.name.split(".command.", 1)[0].split("-"))
        for path in schemas
        if path.name.endswith(".command.input.schema.json")
    }
    output_events = {
        "".join(part.capitalize() for part in path.name.split(".command.", 1)[0].split("-"))
        for path in schemas
        if path.name.endswith(".command.output.schema.json")
    }
    assert CODEX_EVENTS == frozenset(input_events)
    assert CODEX_OUTPUT_EVENTS == frozenset(output_events)
    assert "SessionEnd" in CODEX_EVENTS
    assert "SessionEnd" not in CODEX_OUTPUT_EVENTS


def test_codex_rejects_events_outside_its_domain() -> None:
    with pytest.raises(ValueError, match="does not emit"):
        codex_verdict("PostToolUseFailure", exit_code=0, stdout="", stderr="")


# Claude's Exit code 2 behavior per event table lists these events as blockable.
@pytest.mark.parametrize(
    "event",
    sorted(CLAUDE_EXIT2_BLOCKING_EVENTS),
)
def test_claude_exit_two_blocks_documented_events(event: str) -> None:
    verdict = claude_verdict(event, exit_code=2, stdout="", stderr="blocked by policy")
    assert verdict.status == STATUS_BLOCKED
    assert verdict.message == "blocked by policy"


def test_claude_exit_two_prefers_a_valid_json_block_reason() -> None:
    verdict = claude_verdict(
        "Stop",
        exit_code=2,
        stdout='{"decision":"block","reason":"continue this turn"}',
        stderr="fallback",
    )
    assert verdict.status == STATUS_BLOCKED
    assert verdict.message == "continue this turn"


def test_claude_exit_two_session_start_is_non_blocking() -> None:
    verdict = claude_verdict("SessionStart", exit_code=2, stdout="", stderr="user-facing error")
    assert verdict.status == STATUS_COMPLETED
    assert verdict.message is None


@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure"])
def test_claude_exit_two_after_tool_use_delivers_stderr(event: str) -> None:
    verdict = claude_verdict(event, exit_code=2, stdout="", stderr="tool already ran")
    assert verdict.status == STATUS_COMPLETED
    assert verdict.message == "tool already ran"


def test_claude_unclosed_object_prefix_is_plain_text() -> None:
    verdict = claude_verdict(
        "SessionStart",
        exit_code=0,
        stdout='{"hookSpecificOutput":',
        stderr="",
    )
    assert verdict.status == STATUS_COMPLETED
    assert verdict.contexts == ('{"hookSpecificOutput":',)


def test_claude_precompact_ignores_continue_and_system_message() -> None:
    verdict = claude_verdict(
        "PreCompact",
        exit_code=0,
        stdout='{"continue":false,"stopReason":"wait","systemMessage":"notice"}',
        stderr="",
    )
    assert verdict.status == STATUS_COMPLETED
    assert verdict.message is None
    assert verdict.system_message is None


def test_claude_hook_specific_key_sets_are_event_scoped() -> None:
    assert CLAUDE_HOOK_SPECIFIC_OUTPUT_FIELDS == {
        "PreToolUse": frozenset(
            {
                "permissionDecision",
                "permissionDecisionReason",
                "updatedInput",
                "additionalContext",
            }
        ),
        "PostToolUse": frozenset(
            {
                "additionalContext",
                "classifierContext",
                "updatedToolOutput",
                "updatedMCPToolOutput",
            }
        ),
        "PostToolUseFailure": frozenset({"additionalContext"}),
        "SessionStart": frozenset(
            {
                "additionalContext",
                "initialUserMessage",
                "sessionTitle",
                "watchPaths",
                "reloadSkills",
            }
        ),
        "UserPromptSubmit": frozenset({"additionalContext"}),
        "UserPromptExpansion": frozenset({"additionalContext"}),
        "Stop": frozenset({"additionalContext"}),
        "SubagentStop": frozenset({"additionalContext"}),
        "PreCompact": frozenset(),
        "PostCompact": frozenset(),
        "SessionEnd": frozenset(),
        "SubagentStart": frozenset(),
        "PostModelSwitch": frozenset({"additionalContext"}),
    }


@pytest.mark.parametrize(
    "stdout",
    [
        '{"message":"unsupported"}',
        '{"updatedToolResult":{"ok":true}}',
        '{"additionalContext":"unsupported top-level field"}',
    ],
)
def test_claude_unknown_output_keys_have_no_effect(stdout: str) -> None:
    verdict = claude_verdict("PostToolUse", exit_code=0, stdout=stdout, stderr="")
    assert verdict.status == STATUS_FAILED


def test_claude_pre_tool_use_deny_and_rewrite_effects() -> None:
    denied = claude_verdict(
        "PreToolUse",
        exit_code=0,
        stdout=(
            '{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
            '"permissionDecision":"deny","permissionDecisionReason":"blocked"}}'
        ),
        stderr="",
    )
    rewritten = claude_verdict(
        "PreToolUse",
        exit_code=0,
        stdout=(
            '{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
            '"permissionDecision":"allow","updatedInput":{"command":"safe"}}}'
        ),
        stderr="",
    )
    assert denied.status == STATUS_BLOCKED
    assert denied.message == "blocked"
    assert rewritten.status == STATUS_COMPLETED
    assert rewritten.updated_input == {"command": "safe"}


def test_claude_post_tool_use_can_rewrite_mcp_output() -> None:
    verdict = claude_verdict(
        "PostToolUse",
        exit_code=0,
        stdout=(
            '{"hookSpecificOutput":{"hookEventName":"PostToolUse",'
            '"updatedMCPToolOutput":{"ok":true}}}'
        ),
        stderr="",
    )
    assert verdict.status == STATUS_COMPLETED
    assert verdict.mcp_output == {"ok": True}


def test_claude_stop_can_deliver_system_message() -> None:
    verdict = claude_verdict(
        "Stop", exit_code=0, stdout='{"systemMessage":"All checks passed"}', stderr=""
    )
    assert verdict.status == STATUS_COMPLETED
    assert verdict.system_message == "All checks passed"
