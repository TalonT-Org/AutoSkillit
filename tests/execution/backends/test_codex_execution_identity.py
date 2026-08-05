from __future__ import annotations

import json

import pytest

from autoskillit.core import ChildExecutionIdentity, ExecutionIdentity
from autoskillit.execution.backends import extract_codex_execution_identity

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _write_rollout(path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


def test_corrupt_compressed_rollout_uses_the_identity_error_boundary(tmp_path) -> None:
    parent = tmp_path / "parent.jsonl.zst"
    parent.write_bytes(b"not-a-zstandard-frame")
    requested = ExecutionIdentity(
        requested_parent_backend="codex",
        requested_parent_model="opus",
        requested_parent_effort="medium",
    )

    with pytest.raises(ValueError, match="invalid compressed Codex rollout"):
        extract_codex_execution_identity(parent, requested=requested)


def test_extracts_effective_identity_only_from_linked_rollouts(tmp_path) -> None:
    parent = tmp_path / "parent.jsonl"
    child = tmp_path / "child.jsonl"
    _write_rollout(
        parent,
        [
            {
                "type": "session_meta",
                "payload": {"id": "parent-id", "cli_version": "0.146.0"},
            },
            {
                "type": "turn_context",
                "payload": {"model": "gpt-5.5", "effort": "high"},
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "sub_agent_activity",
                    "kind": "started",
                    "agent_thread_id": "child-id",
                },
            },
        ],
    )
    _write_rollout(
        child,
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "child-id",
                    "cli_version": "0.146.0",
                    "base_instructions": {"text": "plan-sha definition-sha"},
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": "parent-id",
                                "agent_role": "semantic-code-navigator",
                            }
                        }
                    },
                },
            },
            {
                "type": "turn_context",
                "payload": {"model": "gpt-5.6-luna", "effort": "max"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "content": (
                        "task_id: inspect\n"
                        "router_plan_digest: plan-sha\n"
                        "role_definition_digest: definition-sha"
                    ),
                },
            },
        ],
    )
    requested = ExecutionIdentity(
        requested_parent_backend="codex",
        requested_parent_model="opus",
        requested_parent_effort="medium",
        children=(
            ChildExecutionIdentity(
                task_id="inspect",
                role="semantic-code-navigator",
                plan_digest="plan-sha",
                definition_digest="definition-sha",
                requested_backend="codex",
                requested_model="sonnet",
                requested_effort="high",
            ),
        ),
    )

    observed = extract_codex_execution_identity(
        parent,
        requested=requested,
        child_rollout_resolver=lambda child_id: child if child_id == "child-id" else None,
    )

    assert observed.requested_parent_model == "opus"
    assert observed.effective_parent_model == "gpt-5.5"
    assert observed.children[0].requested_model == "sonnet"
    assert observed.children[0].effective_model == "gpt-5.6-luna"
    assert observed.children[0].effective_effort == "max"
    assert observed.parent_session_id == "parent-id"
    assert observed.children[0].session_id == "child-id"
    assert observed.cli_version == "0.146.0"


@pytest.mark.parametrize(
    "roles",
    [
        ("semantic-code-navigator", "repository-structure-profiler"),
        ("semantic-code-navigator", "semantic-code-navigator"),
    ],
)
def test_extracts_all_children_in_task_order_for_mixed_and_repeated_roles(tmp_path, roles) -> None:
    parent = tmp_path / "parent.jsonl"
    _write_rollout(
        parent,
        [
            {"type": "session_meta", "payload": {"id": "parent-id"}},
            {"type": "turn_context", "payload": {"model": "gpt-5.5"}},
            *(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "sub_agent_activity",
                        "kind": "started",
                        "agent_thread_id": f"child-{index}",
                    },
                }
                for index in (1, 2)
            ),
        ],
    )
    paths = {}
    requested_children = []
    for index, (task_id, role) in enumerate(zip(("task-b", "task-a"), roles, strict=True), 1):
        path = tmp_path / f"child-{index}.jsonl"
        paths[f"child-{index}"] = path
        _write_rollout(
            path,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": f"child-{index}",
                        "parent_thread_id": "parent-id",
                        "agent_role": role,
                    },
                },
                {"type": "turn_context", "payload": {"model": "luna", "effort": "max"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "content": (
                            f"task_id: {task_id}\n"
                            "router_plan_digest: router-sha\n"
                            f"role_definition_digest: def-{index}"
                        ),
                    },
                },
            ],
        )
        requested_children.append(
            ChildExecutionIdentity(task_id, role, "router-sha", f"def-{index}")
        )

    observed = extract_codex_execution_identity(
        parent,
        requested=ExecutionIdentity(children=tuple(requested_children)),
        child_rollout_resolver=paths.get,
    )

    assert [child.task_id for child in observed.children] == ["task-a", "task-b"]
    assert {child.session_id for child in observed.children} == {"child-1", "child-2"}


def test_rejects_unlinked_child_rollout(tmp_path) -> None:
    parent = tmp_path / "parent.jsonl"
    child = tmp_path / "child.jsonl"
    _write_rollout(
        parent,
        [
            {"type": "session_meta", "payload": {"id": "parent-id"}},
            {"type": "turn_context", "payload": {"model": "gpt-5.5"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "sub_agent_activity",
                    "kind": "started",
                    "agent_thread_id": "child-id",
                },
            },
        ],
    )
    _write_rollout(
        child,
        [
            {
                "type": "session_meta",
                "payload": {"id": "child-id", "parent_thread_id": "other"},
            },
            {
                "type": "turn_context",
                "payload": {"model": "gpt-5.6-luna", "effort": "max"},
            },
        ],
    )

    with pytest.raises(ValueError, match="not linked"):
        extract_codex_execution_identity(
            parent,
            requested=ExecutionIdentity(
                children=(
                    ChildExecutionIdentity(
                        "inspect",
                        "semantic-code-navigator",
                        "plan-sha",
                        "definition-sha",
                    ),
                )
            ),
            child_rollout_paths=(child,),
        )


def test_rejects_conflicting_codex_owned_effective_values(tmp_path) -> None:
    parent = tmp_path / "parent.jsonl"
    _write_rollout(
        parent,
        [
            {"type": "session_meta", "payload": {"id": "parent-id"}},
            {"type": "turn_context", "payload": {"model": "gpt-5.5"}},
            {"type": "turn_context", "payload": {"model": "gpt-5.6"}},
        ],
    )

    with pytest.raises(ValueError, match="conflicting model"):
        extract_codex_execution_identity(parent, requested=ExecutionIdentity.empty())
