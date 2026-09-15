"""Specialized Codex explorer child conformance assertions."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GeneratedChildEvidence:
    """Authoritative identity fields extracted from one linked Codex child."""

    child_id: str
    parent_id: str
    agent_role: str
    agent_path: str
    cli_version: str
    parent_model: str
    parent_sandbox_mode: str
    model: str
    reasoning_effort: str
    sandbox_mode: str
    approval_policy: str
    network_policy: str
    definition_digest: str


def _strict_parent_call_evidence(
    parent_events: list[dict],
) -> tuple[list[dict], dict[str, object]]:
    function_calls = [
        event.get("payload", {})
        for event in parent_events
        if event.get("type") == "response_item"
        and event.get("payload", {}).get("type") == "function_call"
    ]
    call_outputs = {
        str(payload.get("call_id", "")): payload.get("output", "")
        for event in parent_events
        if event.get("type") == "response_item"
        and (payload := event.get("payload", {})).get("type") == "function_call_output"
    }
    return function_calls, call_outputs


def _assert_parent_spawn_and_wait(
    function_calls: list[dict],
    call_outputs: dict[str, object],
    agent_role: str,
) -> tuple[dict, str]:
    spawn_calls = [call for call in function_calls if call.get("name") == "spawn_agent"]
    assert len(spawn_calls) == 1, f"expected one spawn_agent call, got {len(spawn_calls)}"
    spawn = spawn_calls[0]
    spawn_args = json.loads(str(spawn.get("arguments", "{}")))
    assert spawn_args.get("agent_type") == agent_role
    task_name = spawn_args.get("task_name")
    assert isinstance(task_name, str) and task_name, "spawn_agent omitted task_name"
    assert spawn_args.get("fork_turns") == "none"
    spawn_output = json.loads(str(call_outputs.get(str(spawn.get("call_id", "")), "{}")))
    canonical_task_name = spawn_output.get("task_name")
    assert isinstance(canonical_task_name, str) and canonical_task_name, (
        "spawn_agent returned no canonical task_name"
    )
    assert canonical_task_name == task_name or canonical_task_name.endswith(f"/{task_name}")

    wait_calls = [call for call in function_calls if call.get("name") == "wait_agent"]
    assert wait_calls, "parent made no wait_agent call after spawning the child"
    wait_outputs = [
        str(call_outputs.get(str(wait_call.get("call_id", "")), "")) for wait_call in wait_calls
    ]
    assert any(
        isinstance((parsed := json.loads(output)), dict) and parsed.get("timed_out") is False
        for output in wait_outputs
    ), "wait_agent never returned successfully"
    return spawn, canonical_task_name


def _spawn_record(meta: dict) -> dict:
    source = meta.get("source", {})
    if not isinstance(source, dict):
        return {}
    subagent = source.get("subagent", {})
    if not isinstance(subagent, dict):
        return {}
    record = subagent.get("thread_spawn", {})
    return record if isinstance(record, dict) else {}


def _developer_instruction_text(child_events: list[dict]) -> str:
    developer_blocks: list[str] = []
    for event in child_events:
        payload = event.get("payload", {})
        if (
            event.get("type") != "response_item"
            or payload.get("type") != "message"
            or payload.get("role") != "developer"
        ):
            continue
        content = payload.get("content", [])
        if isinstance(content, str):
            developer_blocks.append(content)
        elif isinstance(content, list):
            developer_blocks.extend(
                str(block.get("text", "")) for block in content if isinstance(block, dict)
            )
    return "\n".join(developer_blocks)


def _assert_linked_child_evidence(
    parent_events: list[dict],
    child_events: list[dict],
    parent_id: str,
    agent_role: str,
    output_discipline_digest: str,
    expected_definition_digest: str | None,
    spawn: dict,
    canonical_task_name: str,
) -> tuple[str, str, str, str]:
    child_session_metas = [
        event.get("payload", {}) for event in child_events if event.get("type") == "session_meta"
    ]
    linked_children = []
    for meta in child_session_metas:
        spawn_record = _spawn_record(meta)
        linked_parent = (
            meta.get("forked_from_id")
            or meta.get("parent_thread_id")
            or spawn_record.get("parent_thread_id")
        )
        if linked_parent == parent_id:
            linked_children.append((meta, spawn_record))
    assert len(linked_children) == 1, (
        f"expected one child linked to {parent_id}, got {len(linked_children)}"
    )
    child, spawn_record = linked_children[0]
    child_id = child.get("id")
    assert isinstance(child_id, str) and child_id, "child session_meta omitted id"
    assert child_id != parent_id
    linked_parent = (
        child.get("forked_from_id")
        or child.get("parent_thread_id")
        or spawn_record.get("parent_thread_id")
    )
    assert linked_parent == parent_id
    observed_role = child.get("agent_role") or spawn_record.get("agent_role")
    observed_path = child.get("agent_path") or spawn_record.get("agent_path")
    assert observed_role == agent_role
    assert isinstance(observed_path, str) and observed_path, (
        "child session_meta omitted agent_path"
    )
    assert observed_path == canonical_task_name
    spawn_activities = [
        event.get("payload", {})
        for event in parent_events
        if event.get("type") == "event_msg"
        and event.get("payload", {}).get("type") == "sub_agent_activity"
        and event.get("payload", {}).get("event_id") == spawn.get("call_id")
        and event.get("payload", {}).get("agent_thread_id") == child_id
        and event.get("payload", {}).get("agent_path") == observed_path
        and event.get("payload", {}).get("kind") == "started"
    ]
    assert len(spawn_activities) == 1, (
        "parent rollout did not bind the spawn call to the linked child session"
    )
    assert any(
        event.get("type") == "event_msg"
        and event.get("payload", {}).get("type") == "task_complete"
        for event in child_events
    ), "child rollout omitted task_complete"
    cli_version = child.get("cli_version")
    assert isinstance(cli_version, str) and cli_version, "child session_meta omitted cli_version"
    base_instructions = child.get("base_instructions", {})
    assert isinstance(base_instructions, dict)
    base_text = base_instructions.get("text", "")
    assert isinstance(base_text, str)
    developer_text = _developer_instruction_text(child_events)
    assert output_discipline_digest in base_text or output_discipline_digest in developer_text, (
        "generated child instructions omitted output discipline digest"
    )
    if expected_definition_digest is not None:
        assert expected_definition_digest in base_text, (
            "child session_meta base_instructions omitted the canonical definition digest"
        )
    return child_id, observed_role, observed_path, cli_version


def _one(owner: str, field_name: str, values: list[object]) -> object:
    unique = {json.dumps(value, sort_keys=True) for value in values}
    assert len(unique) == 1, f"{owner} turn_context has conflicting {field_name}: {values!r}"
    value = values[0]
    assert value is not None and value != "", f"{owner} turn_context omitted {field_name}"
    return value


def _turn_contexts(events: list[dict]) -> list[dict]:
    return [
        event.get("payload", {})
        for event in events
        if event.get("type") == "turn_context" and isinstance(event.get("payload"), dict)
    ]


def _assert_parent_turn_context(
    parent_events: list[dict],
    expected_parent_model: str | None,
    expected_parent_sandbox_mode: str,
) -> tuple[str, str]:
    parent_turn_contexts = _turn_contexts(parent_events)
    assert parent_turn_contexts, "parent rollout omitted turn_context"
    parent_sandbox_policy = _one(
        "parent",
        "sandbox_policy",
        [context.get("sandbox_policy") for context in parent_turn_contexts],
    )
    parent_approval_policy = _one(
        "parent",
        "approval_policy",
        [context.get("approval_policy") for context in parent_turn_contexts],
    )
    parent_permission_profile = _one(
        "parent",
        "permission_profile",
        [context.get("permission_profile") for context in parent_turn_contexts],
    )
    assert isinstance(parent_sandbox_policy, dict)
    parent_model = _one(
        "parent",
        "model",
        [context.get("model") for context in parent_turn_contexts],
    )
    assert isinstance(parent_model, str)
    assert isinstance(parent_approval_policy, str)
    assert isinstance(parent_permission_profile, dict)
    parent_sandbox_mode = parent_sandbox_policy.get("type")
    parent_network_policy = parent_permission_profile.get("network")
    assert isinstance(parent_sandbox_mode, str) and parent_sandbox_mode
    assert isinstance(parent_network_policy, str) and parent_network_policy
    assert parent_sandbox_mode == expected_parent_sandbox_mode
    if expected_parent_model is not None:
        assert parent_model == expected_parent_model
    assert parent_approval_policy == "never"
    assert parent_network_policy == "restricted"
    return parent_model, parent_sandbox_mode


def _assert_child_turn_context(
    child_events: list[dict],
    expected_model: str | None,
    expected_reasoning_effort: str | None,
    expected_sandbox_mode: str | None,
) -> tuple[str, str, str, str, str]:
    turn_contexts = _turn_contexts(child_events)
    assert turn_contexts, "child rollout omitted turn_context"
    model = _one("child", "model", [context.get("model") for context in turn_contexts])
    effort = _one("child", "effort", [context.get("effort") for context in turn_contexts])
    sandbox_policy = _one(
        "child",
        "sandbox_policy",
        [context.get("sandbox_policy") for context in turn_contexts],
    )
    approval_policy = _one(
        "child",
        "approval_policy",
        [context.get("approval_policy") for context in turn_contexts],
    )
    permission_profile = _one(
        "child",
        "permission_profile",
        [context.get("permission_profile") for context in turn_contexts],
    )
    assert isinstance(model, str)
    assert isinstance(effort, str)
    assert isinstance(sandbox_policy, dict)
    assert isinstance(approval_policy, str)
    assert isinstance(permission_profile, dict)
    sandbox_mode = sandbox_policy.get("type")
    network_policy = permission_profile.get("network")
    assert isinstance(sandbox_mode, str) and sandbox_mode
    assert isinstance(network_policy, str) and network_policy
    if expected_model is not None:
        assert model == expected_model
    if expected_reasoning_effort is not None:
        assert effort == expected_reasoning_effort
    if expected_sandbox_mode is not None:
        assert sandbox_mode == expected_sandbox_mode
    return model, effort, sandbox_mode, approval_policy, network_policy


def assert_generated_codex_child_delivery(
    parent_events: list[dict],
    child_events: list[dict],
    *,
    parent_id: str,
    agent_role: str,
    output_discipline_digest: str,
    expected_parent_model: str | None = None,
    expected_parent_sandbox_mode: str,
    expected_model: str | None = None,
    expected_reasoning_effort: str | None = None,
    expected_sandbox_mode: str | None = None,
    expected_definition_digest: str | None = None,
) -> GeneratedChildEvidence:
    """Assert a generated Codex role reached one completed, linked child session."""
    function_calls, call_outputs = _strict_parent_call_evidence(parent_events)
    spawn, canonical_task_name = _assert_parent_spawn_and_wait(
        function_calls,
        call_outputs,
        agent_role,
    )

    child_id, observed_role, observed_path, cli_version = _assert_linked_child_evidence(
        parent_events,
        child_events,
        parent_id,
        agent_role,
        output_discipline_digest,
        expected_definition_digest,
        spawn,
        canonical_task_name,
    )

    parent_model, parent_sandbox_mode = _assert_parent_turn_context(
        parent_events,
        expected_parent_model,
        expected_parent_sandbox_mode,
    )

    model, effort, sandbox_mode, approval_policy, network_policy = _assert_child_turn_context(
        child_events,
        expected_model,
        expected_reasoning_effort,
        expected_sandbox_mode,
    )
    definition_digest = expected_definition_digest or ""
    return GeneratedChildEvidence(
        child_id=child_id,
        parent_id=parent_id,
        agent_role=observed_role,
        agent_path=observed_path,
        cli_version=cli_version,
        parent_model=parent_model,
        parent_sandbox_mode=parent_sandbox_mode,
        model=model,
        reasoning_effort=effort,
        sandbox_mode=sandbox_mode,
        approval_policy=approval_policy,
        network_policy=network_policy,
        definition_digest=definition_digest,
    )
