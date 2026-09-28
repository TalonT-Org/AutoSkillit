"""Test-side models of the Codex and Claude Code hook output contracts."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, NamedTuple

from jsonschema import validators

CODEX_PROTOCOL_VERSION: str = "0.156.1"
CODEX_PROTOCOL_SOURCE: str = (
    "openai/codex rust-v0.156.1 (b412ff32c417f855c2b2d1581b77058eed87c84b)"
)
CLAUDE_PROTOCOL_SOURCE: str = "https://code.claude.com/docs/en/hooks.md (retrieved 2026-09-28)"

_CODEX_SCHEMA_DIR = (
    Path(__file__).parent / "fixtures" / "codex_hook_protocol" / CODEX_PROTOCOL_VERSION
)
_CODEX_SCHEMA_PATHS = tuple(sorted(_CODEX_SCHEMA_DIR.glob("*.command.*.schema.json")))


def _event_name(path: Path) -> str:
    slug = path.name.split(".command.", 1)[0]
    return "".join(part.capitalize() for part in slug.split("-"))


_INPUT_SCHEMA_PATHS = tuple(
    path for path in _CODEX_SCHEMA_PATHS if path.name.endswith(".command.input.schema.json")
)
_OUTPUT_SCHEMA_PATHS = tuple(
    path for path in _CODEX_SCHEMA_PATHS if path.name.endswith(".command.output.schema.json")
)

CODEX_EVENTS: frozenset[str] = frozenset(_event_name(path) for path in _INPUT_SCHEMA_PATHS)
CODEX_OUTPUT_EVENTS: frozenset[str] = frozenset(_event_name(path) for path in _OUTPUT_SCHEMA_PATHS)
CODEX_EXIT2_BLOCKING_EVENTS: frozenset[str] = frozenset(
    {
        "PreToolUse",
        "PostToolUse",
        "PermissionRequest",
        "UserPromptSubmit",
        "Stop",
        "SubagentStop",
    }
)

_SCHEMA_VALIDATORS: dict[tuple[str, str], Any] = {}
for _path in _CODEX_SCHEMA_PATHS:
    _schema = json.loads(_path.read_text(encoding="utf-8"))
    _validator_class = validators.validator_for(_schema)
    _validator_class.check_schema(_schema)
    _kind = "input" if _path.name.endswith(".input.schema.json") else "output"
    _SCHEMA_VALIDATORS[(_kind, _event_name(_path))] = _validator_class(_schema)


class HookVerdict(NamedTuple):
    status: Literal["completed", "blocked", "failed", "stopped"]
    message: str | None
    contexts: tuple[str, ...]
    updated_input: object | None
    mcp_output: object | None
    system_message: str | None
    error: str | None


STATUS_COMPLETED = "completed"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"

EXPECTED_EFFECT: Mapping[str, Callable[[HookVerdict], bool]] = {
    "deny": lambda verdict: verdict.status == STATUS_BLOCKED and bool(verdict.message),
    "block": lambda verdict: (
        verdict.status == STATUS_BLOCKED
        or (verdict.status == STATUS_COMPLETED and bool(verdict.message))
    ),
    "context": lambda verdict: verdict.status == STATUS_COMPLETED and bool(verdict.contexts),
    "rewrite_input": lambda verdict: verdict.updated_input is not None,
    "rewrite_mcp_output": lambda verdict: verdict.mcp_output is not None,
    "notify": lambda verdict: bool(verdict.system_message),
    "halt": lambda verdict: verdict.status == STATUS_STOPPED,
}


def _completed(
    *,
    message: str | None = None,
    contexts: tuple[str, ...] = (),
    updated_input: object | None = None,
    mcp_output: object | None = None,
    system_message: str | None = None,
) -> HookVerdict:
    return HookVerdict(
        "completed",
        message,
        contexts,
        updated_input,
        mcp_output,
        system_message,
        None,
    )


def _blocked(message: str | None) -> HookVerdict:
    return HookVerdict("blocked", message, (), None, None, None, None)


def _stopped(message: str | None) -> HookVerdict:
    return HookVerdict("stopped", message, (), None, None, None, None)


def _failed(error: str) -> HookVerdict:
    return HookVerdict("failed", None, (), None, None, None, error)


def _trimmed_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _additional_context(value: Mapping[str, Any]) -> tuple[str, ...]:
    specific = value.get("hookSpecificOutput")
    if not isinstance(specific, dict):
        return ()
    context = _trimmed_text(specific.get("additionalContext"))
    return (context,) if context is not None else ()


def _system_message(value: Mapping[str, Any]) -> str | None:
    return _trimmed_text(value.get("systemMessage"))


def _unsupported_pre_tool_use(value: Mapping[str, Any]) -> str | None:
    if value.get("continue", True) is False:
        return "PreToolUse hook returned unsupported continue:false"
    if value.get("stopReason") is not None:
        return "PreToolUse hook returned unsupported stopReason"
    if value.get("suppressOutput", False):
        return "PreToolUse hook returned unsupported suppressOutput"

    specific = value.get("hookSpecificOutput")
    use_specific_decision = isinstance(specific, dict) and any(
        key in specific
        for key in ("permissionDecision", "permissionDecisionReason", "updatedInput")
    )
    if use_specific_decision:
        assert isinstance(specific, dict)
        decision = specific.get("permissionDecision")
        if "updatedInput" in specific and decision != "allow":
            return "PreToolUse hook returned updatedInput without permissionDecision:allow"
        if decision == "allow" and "updatedInput" not in specific:
            return "PreToolUse hook returned unsupported permissionDecision:allow"
        if decision == "ask":
            return "PreToolUse hook returned unsupported permissionDecision:ask"
        if decision == "deny" and _trimmed_text(specific.get("permissionDecisionReason")) is None:
            return (
                "PreToolUse hook returned permissionDecision:deny without a "
                "non-empty permissionDecisionReason"
            )
        if decision is None and specific.get("permissionDecisionReason") is not None:
            return "PreToolUse hook returned permissionDecisionReason without permissionDecision"
        return None

    decision = value.get("decision")
    reason = value.get("reason")
    if decision == "approve":
        return "PreToolUse hook returned unsupported decision:approve"
    if decision == "block" and _trimmed_text(reason) is None:
        return "PreToolUse hook returned decision:block without a non-empty reason"
    if decision is None and reason is not None:
        return "PreToolUse hook returned reason without decision"
    return None


# Codex output_parser.rs:110-169,343-486; events/pre_tool_use.rs:193-286.
def _codex_pre_tool_use(value: Mapping[str, Any]) -> HookVerdict:
    invalid = _unsupported_pre_tool_use(value)
    if invalid is not None:
        return _failed(invalid)

    contexts = _additional_context(value)
    specific = value.get("hookSpecificOutput")
    if isinstance(specific, dict):
        permission_decision = specific.get("permissionDecision")
        if permission_decision == "deny":
            return _blocked(_trimmed_text(specific.get("permissionDecisionReason")))
        if permission_decision == "allow":
            return _completed(
                contexts=contexts,
                updated_input=specific.get("updatedInput"),
                system_message=_system_message(value),
            )

    if value.get("decision") == "block":
        return _blocked(_trimmed_text(value.get("reason")))
    return _completed(contexts=contexts, system_message=_system_message(value))


def _unsupported_permission_request(value: Mapping[str, Any]) -> str | None:
    if value.get("continue", True) is False:
        return "PermissionRequest hook returned unsupported continue:false"
    if value.get("stopReason") is not None:
        return "PermissionRequest hook returned unsupported stopReason"
    if value.get("suppressOutput", False):
        return "PermissionRequest hook returned unsupported suppressOutput"

    specific = value.get("hookSpecificOutput")
    if not isinstance(specific, dict):
        return None
    decision = specific.get("decision")
    if not isinstance(decision, dict):
        return None
    if decision.get("updatedInput") is not None:
        return "PermissionRequest hook returned unsupported updatedInput"
    if decision.get("updatedPermissions") is not None:
        return "PermissionRequest hook returned unsupported updatedPermissions"
    if decision.get("interrupt") is True:
        return "PermissionRequest hook returned unsupported interrupt:true"
    return None


# Codex output_parser.rs:171-190,354-398; events/permission_request.rs:188-275.
def _codex_permission_request(value: Mapping[str, Any]) -> HookVerdict:
    invalid = _unsupported_permission_request(value)
    if invalid is not None:
        return _failed(invalid)

    specific = value.get("hookSpecificOutput")
    decision = specific.get("decision") if isinstance(specific, dict) else None
    if not isinstance(decision, dict):
        return _completed(system_message=_system_message(value))
    if decision.get("behavior") == "deny":
        message = _trimmed_text(decision.get("message"))
        return _blocked(message or "PermissionRequest hook denied approval")
    return _completed(system_message=_system_message(value))


def _codex_post_tool_use(value: Mapping[str, Any]) -> HookVerdict:
    if value.get("continue", True) is False:
        return _stopped(
            _trimmed_text(value.get("stopReason")) or "PostToolUse hook stopped execution"
        )
    if value.get("suppressOutput", False):
        return _failed("PostToolUse hook returned unsupported suppressOutput")

    specific = value.get("hookSpecificOutput")
    if isinstance(specific, dict) and "updatedMCPToolOutput" in specific:
        return _failed("PostToolUse hook returned unsupported updatedMCPToolOutput")

    decision = value.get("decision")
    reason = value.get("reason")
    if decision == "block":
        if _trimmed_text(reason) is None:
            return _failed("PostToolUse hook returned decision:block without a non-empty reason")
        return _blocked(_trimmed_text(reason))
    if decision is None and reason is not None:
        return _failed("PostToolUse hook returned reason without decision")

    return _completed(
        contexts=_additional_context(value),
        system_message=_system_message(value),
    )


def _codex_session_start(value: Mapping[str, Any]) -> HookVerdict:
    contexts = _additional_context(value)
    if value.get("continue", True) is False:
        return _stopped(_trimmed_text(value.get("stopReason")))
    return _completed(contexts=contexts, system_message=_system_message(value))


def _codex_subagent_start(value: Mapping[str, Any]) -> HookVerdict:
    return _completed(
        contexts=_additional_context(value),
        system_message=_system_message(value),
    )


def _codex_user_prompt_submit(value: Mapping[str, Any]) -> HookVerdict:
    contexts = _additional_context(value)
    if value.get("continue", True) is False:
        return _stopped(_trimmed_text(value.get("stopReason")))
    if value.get("decision") == "block":
        reason = _trimmed_text(value.get("reason"))
        if reason is None:
            return _failed(
                "UserPromptSubmit hook returned decision:block without a non-empty reason"
            )
        return _blocked(reason)
    return _completed(contexts=contexts, system_message=_system_message(value))


def _codex_stop(value: Mapping[str, Any], *, event: str) -> HookVerdict:
    if value.get("continue", True) is False:
        return _stopped(_trimmed_text(value.get("stopReason")))
    if value.get("decision") == "block":
        reason = _trimmed_text(value.get("reason"))
        if reason is None:
            return _failed(f"{event} hook returned decision:block without a non-empty reason")
        return _blocked(reason)
    return _completed(system_message=_system_message(value))


def _codex_stop_event(value: Mapping[str, Any]) -> HookVerdict:
    return _codex_stop(value, event="Stop")


def _codex_subagent_stop(value: Mapping[str, Any]) -> HookVerdict:
    return _codex_stop(value, event="SubagentStop")


def _codex_pre_compact(value: Mapping[str, Any]) -> HookVerdict:
    if value.get("continue", True) is False:
        return _stopped(_trimmed_text(value.get("stopReason")))
    return _completed(system_message=_system_message(value))


def _codex_post_compact(value: Mapping[str, Any]) -> HookVerdict:
    if value.get("continue", True) is False:
        return _stopped(_trimmed_text(value.get("stopReason")))
    return _completed(system_message=_system_message(value))


def _codex_interrupt(value: Mapping[str, Any]) -> HookVerdict:
    return _completed(system_message=_trimmed_text(value.get("systemMessage")))


def _codex_semantics(event: str, value: Mapping[str, Any]) -> HookVerdict:
    # Each event parser is deliberately explicit: their accepted fields differ.
    if event == "PreToolUse":
        return _codex_pre_tool_use(value)
    if event == "PostToolUse":
        return _codex_post_tool_use(value)
    if event == "PermissionRequest":
        return _codex_permission_request(value)
    if event == "SessionStart":
        return _codex_session_start(value)
    if event == "SubagentStart":
        return _codex_subagent_start(value)
    if event == "UserPromptSubmit":
        return _codex_user_prompt_submit(value)
    if event == "Stop":
        return _codex_stop_event(value)
    if event == "SubagentStop":
        return _codex_subagent_stop(value)
    if event == "PreCompact":
        return _codex_pre_compact(value)
    if event == "PostCompact":
        return _codex_post_compact(value)
    if event == "Interrupt":
        return _codex_interrupt(value)
    return _completed()


def _looks_like_json(stdout: str) -> bool:
    trimmed = stdout.lstrip()
    return trimmed.startswith("{") or trimmed.startswith("[")


def _validated_output(event: str, value: object) -> str | None:
    validator = _SCHEMA_VALIDATORS[("output", event)]
    errors = sorted(validator.iter_errors(value), key=str)
    return "; ".join(str(error) for error in errors) if errors else None


def codex_verdict(
    event: str,
    *,
    exit_code: int | None,
    stdout: str,
    stderr: str,
) -> HookVerdict:
    """Return Codex's hook verdict for an emitted process result."""
    if event not in CODEX_EVENTS:
        raise ValueError(f"Codex does not emit a {event!r} hook event")
    if exit_code is None:
        return _failed("hook process terminated without an exit code")
    if exit_code == 2:
        if event in CODEX_EXIT2_BLOCKING_EVENTS:
            reason = _trimmed_text(stderr)
            if reason is not None:
                return _blocked(reason)
            return _failed(f"{event} hook exited with code 2 without feedback on stderr")
        return _failed(f"hook exited with code {exit_code}")
    if exit_code != 0:
        return _failed(f"hook exited with code {exit_code}")
    if event not in CODEX_OUTPUT_EVENTS:
        return _completed()

    trimmed = stdout.strip()
    if not trimmed:
        return _completed()
    try:
        parsed = json.loads(trimmed)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        if _looks_like_json(stdout):
            return _failed(f"hook returned invalid {event} JSON output")
        if event in {"SessionStart", "SubagentStart", "UserPromptSubmit"}:
            return _completed(contexts=(trimmed,))
        if event in {"Stop", "SubagentStop", "Interrupt"}:
            return _failed(f"{event} hook returned non-JSON stdout")
        return _completed()

    validation_error = _validated_output(event, parsed)
    if validation_error is not None:
        return _failed(validation_error)
    return _codex_semantics(event, parsed)


# These keys and event exceptions come from Claude's JSON output and
# per-event decision-control tables in hooks.md.
CLAUDE_HOOK_SPECIFIC_OUTPUT_FIELDS: Mapping[str, frozenset[str]] = {
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
CLAUDE_EXIT2_BLOCKING_EVENTS: frozenset[str] = frozenset(
    {
        "PreToolUse",
        "UserPromptSubmit",
        "UserPromptExpansion",
        "Stop",
        "SubagentStop",
        "PreCompact",
    }
)
_CLAUDE_CONTEXT_EVENTS = frozenset(
    {
        "UserPromptSubmit",
        "UserPromptExpansion",
        "SessionStart",
        "PostModelSwitch",
    }
)
_CLAUDE_DECISION_EVENTS = frozenset(
    {
        "PostToolUse",
        "UserPromptSubmit",
        "UserPromptExpansion",
        "Stop",
        "SubagentStop",
        "PreCompact",
    }
)
_CLAUDE_UNIVERSAL_FIELDS = frozenset(
    {"continue", "stopReason", "suppressOutput", "systemMessage", "terminalSequence"}
)


def _claude_candidate(stdout: str) -> tuple[dict[str, Any] | None, bool]:
    trimmed = stdout.strip()
    if not (trimmed.startswith("{") and trimmed.endswith("}")):
        return None, False
    try:
        value = json.loads(trimmed)
    except ValueError:
        return None, True
    return (value, True) if isinstance(value, dict) else (None, True)


def _claude_output_error(event: str, value: Mapping[str, Any]) -> str | None:
    allowed = set(_CLAUDE_UNIVERSAL_FIELDS)
    allowed.add("hookSpecificOutput")
    if event in _CLAUDE_DECISION_EVENTS:
        allowed.update({"decision", "reason"})
    unknown = set(value) - allowed
    if unknown:
        return f"unsupported top-level output fields for {event}: {sorted(unknown)!r}"

    if "continue" in value and not isinstance(value["continue"], bool):
        return "continue must be a boolean"
    for field in ("stopReason", "systemMessage"):
        if field in value and not isinstance(value[field], str):
            return f"{field} must be a string"
    if "suppressOutput" in value and not isinstance(value["suppressOutput"], bool):
        return "suppressOutput must be a boolean"
    if "terminalSequence" in value and not isinstance(value["terminalSequence"], str):
        return "terminalSequence must be a string"
    if "decision" in value and value["decision"] not in {"allow", "block"}:
        return "decision must be allow or block"
    if "reason" in value and not isinstance(value["reason"], str):
        return "reason must be a string"

    specific = value.get("hookSpecificOutput")
    if specific is None:
        return None
    if not isinstance(specific, dict):
        return "hookSpecificOutput must be an object"
    specific_fields = CLAUDE_HOOK_SPECIFIC_OUTPUT_FIELDS.get(event)
    if specific_fields is None:
        return f"hookSpecificOutput is not documented for {event}"
    allowed_specific = set(specific_fields) | {"hookEventName"}
    unknown_specific = set(specific) - allowed_specific
    if unknown_specific:
        return f"unsupported hookSpecificOutput fields for {event}: {sorted(unknown_specific)!r}"
    if specific.get("hookEventName") != event:
        return f"hookSpecificOutput.hookEventName must be {event}"
    for field in (
        "permissionDecisionReason",
        "additionalContext",
        "classifierContext",
        "initialUserMessage",
        "sessionTitle",
    ):
        if field in specific and not isinstance(specific[field], str):
            return f"hookSpecificOutput.{field} must be a string"
    if "permissionDecision" in specific and specific["permissionDecision"] not in {
        "allow",
        "deny",
        "ask",
        "defer",
    }:
        return "hookSpecificOutput.permissionDecision is unsupported"
    if "updatedInput" in specific and not isinstance(specific["updatedInput"], dict):
        return "hookSpecificOutput.updatedInput must be an object"
    if "watchPaths" in specific and not isinstance(specific["watchPaths"], list):
        return "hookSpecificOutput.watchPaths must be an array"
    if "reloadSkills" in specific and not isinstance(specific["reloadSkills"], list):
        return "hookSpecificOutput.reloadSkills must be an array"
    return None


def _claude_blocking_reason(event: str, value: Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    if event == "PreToolUse":
        specific = value.get("hookSpecificOutput")
        if isinstance(specific, dict) and specific.get("permissionDecision") == "deny":
            return _trimmed_text(specific.get("permissionDecisionReason"))
        return None
    if value.get("decision") == "block":
        return _trimmed_text(value.get("reason"))
    return None


def _claude_json_verdict(event: str, value: Mapping[str, Any]) -> HookVerdict:
    invalid = _claude_output_error(event, value)
    if invalid is not None:
        return _failed(invalid)

    specific = value.get("hookSpecificOutput")
    if not isinstance(specific, dict):
        specific = {}
    contexts = _trimmed_text(specific.get("additionalContext"))
    context_values = (contexts,) if contexts is not None else ()
    system_message = _trimmed_text(value.get("systemMessage"))

    if event == "PreCompact":
        # Claude hooks reference, PreCompact: systemMessage and continue are discarded.
        system_message = None
        continue_processing = True
    else:
        continue_processing = value.get("continue", True)
    if not continue_processing:
        return _stopped(_trimmed_text(value.get("stopReason")))

    if event == "PreToolUse":
        permission_decision = specific.get("permissionDecision")
        if permission_decision == "deny":
            reason = _trimmed_text(specific.get("permissionDecisionReason"))
            if reason is None:
                return _failed("permissionDecision:deny requires a reason")
            return _blocked(reason)
        if permission_decision == "allow" and "updatedInput" in specific:
            return _completed(
                contexts=context_values,
                updated_input=specific["updatedInput"],
                system_message=system_message,
            )
    if value.get("decision") == "block":
        reason = _trimmed_text(value.get("reason"))
        if reason is None:
            return _failed("decision:block requires a non-empty reason")
        if event == "PostToolUse":
            return _completed(
                message=reason,
                contexts=context_values,
                system_message=system_message,
            )
        return _blocked(reason)

    mcp_output = specific.get("updatedMCPToolOutput")
    return _completed(
        contexts=context_values,
        mcp_output=mcp_output,
        system_message=system_message,
    )


def claude_verdict(
    event: str,
    *,
    exit_code: int | None,
    stdout: str,
    stderr: str,
) -> HookVerdict:
    """Return Claude Code's documented hook verdict for a process result."""
    if exit_code is None:
        return _failed("hook process terminated without an exit code")

    candidate, looks_structured = _claude_candidate(stdout)
    if exit_code == 2:
        if event in CLAUDE_EXIT2_BLOCKING_EVENTS:
            reason = _claude_blocking_reason(event, candidate)
            return _blocked(reason or _trimmed_text(stderr))
        if event in {"PostToolUse", "PostToolUseFailure"}:
            return _completed(message=_trimmed_text(stderr))
        if event in {
            "SessionStart",
            "SubagentStart",
            "SessionEnd",
            "PostCompact",
            "PermissionRequest",
        }:
            return _completed()
        return _failed("hook exited with code 2")
    if exit_code != 0:
        return _failed(f"hook exited with code {exit_code}")

    trimmed = stdout.strip()
    if not trimmed:
        return _completed()
    if looks_structured:
        if candidate is None:
            return _failed(f"invalid Claude hook JSON output for {event}")
        return _claude_json_verdict(event, candidate)
    if event in _CLAUDE_CONTEXT_EVENTS:
        return _completed(contexts=(trimmed,))
    return _completed()


def run_hook(
    script_rel: str | Path,
    payload: dict[str, object] | str,
    *,
    env: Mapping[str, str] | None = None,
    unset: Iterable[str] = (),
    cwd: Path | None = None,
    timeout: float | None = 10,
):
    """Run one hook in a fresh interpreter and adapt its process result."""
    from autoskillit.hooks._runtime._hook_output import HookEmission
    from tests.conftest import production_interpreter_env

    script = Path(script_rel)
    if not script.is_absolute():
        script = Path(__file__).parents[1] / "src" / "autoskillit" / "hooks" / script
    input_text = payload if isinstance(payload, str) else json.dumps(payload)
    run_env = production_interpreter_env()
    run_env.pop("AUTOSKILLIT_STATE_ROOT", None)
    run_env.pop("AUTOSKILLIT_STATE_DIR", None)
    run_env.pop("AUTOSKILLIT_LOG_DIR", None)
    if env is None or "AUTOSKILLIT_HEADLESS" not in env:
        run_env.pop("AUTOSKILLIT_HEADLESS", None)
    for name in unset:
        run_env.pop(name, None)
    run_env.update(env or {})
    run_env.pop("AUTOSKILLIT_STATE_ROOT", None)

    with tempfile.TemporaryDirectory(prefix="hook-protocol-") as temp_dir:
        log_dir = Path(temp_dir) / "logs"
        log_dir.mkdir()
        if env is None or "AUTOSKILLIT_LOG_DIR" not in env:
            run_env["AUTOSKILLIT_LOG_DIR"] = str(log_dir)
        result = subprocess.run(
            [sys.executable, "-B", str(script)],
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            env=run_env,
            timeout=timeout,
        )
    return HookEmission(result.stdout, result.stderr, result.returncode)
