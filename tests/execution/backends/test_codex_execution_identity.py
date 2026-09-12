from __future__ import annotations

import json

import pytest

from autoskillit.core import ChildExecutionIdentity, ExecutionIdentity
from autoskillit.execution.backends import extract_codex_execution_identity
from autoskillit.execution.backends._codex_execution_identity import (
    extract_codex_child_metadata,
)

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


def test_extracts_observed_child_metadata_without_a_requested_plan(tmp_path) -> None:
    child = tmp_path / "child.jsonl"
    _write_rollout(
        child,
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "child-thread-id",
                    "session_id": "root-session-id",
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": "parent-thread-id",
                                "agent_role": "plan-foundation-auditor",
                            }
                        }
                    },
                },
            },
            {
                "type": "turn_context",
                "payload": {"model": "gpt-5.6-sol", "effort": "medium"},
            },
        ],
    )

    metadata = extract_codex_child_metadata(
        child,
        expected_parent_id="parent-thread-id",
        expected_child_id="child-thread-id",
    )

    assert metadata == {
        "backend": "codex",
        "parent_session_id": "parent-thread-id",
        "child_id": "child-thread-id",
        "role": "plan-foundation-auditor",
        "effective_model": "gpt-5.6-sol",
        "effective_effort": "medium",
    }


@pytest.mark.parametrize(
    ("payload", "expected_child_id", "match"),
    [
        pytest.param(
            {"id": "child-id", "parent_thread_id": "other-parent"},
            "child-id",
            "not linked",
            id="wrong-parent",
        ),
        pytest.param(
            {
                "id": "child-id",
                "forked_from_id": "parent-id",
                "parent_thread_id": "other-parent",
            },
            "child-id",
            "conflicting parent linkage",
            id="contradictory-parent-links",
        ),
        pytest.param(
            {"id": "different-child", "parent_thread_id": "parent-id"},
            "child-id",
            "invalid child id",
            id="wrong-child",
        ),
        pytest.param(
            {
                "id": "child-id",
                "parent_thread_id": "parent-id",
                "agent_role": "role-a",
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": "parent-id",
                            "agent_role": "role-b",
                        }
                    }
                },
            },
            "child-id",
            "conflicting agent_role",
            id="contradictory-role",
        ),
    ],
)
def test_observed_child_metadata_rejects_structural_mismatches(
    tmp_path, payload, expected_child_id, match
) -> None:
    child = tmp_path / "child.jsonl"
    _write_rollout(child, [{"type": "session_meta", "payload": payload}])

    with pytest.raises(ValueError, match=match):
        extract_codex_child_metadata(
            child,
            expected_parent_id="parent-id",
            expected_child_id=expected_child_id,
        )


def test_observed_child_metadata_keeps_role_when_native_settings_conflict(tmp_path) -> None:
    child = tmp_path / "child.jsonl"
    _write_rollout(
        child,
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "child-id",
                    "parent_thread_id": "parent-id",
                    "agent_role": "plan-foundation-auditor",
                },
            },
            {"type": "turn_context", "payload": {"model": "model-a", "effort": "high"}},
            {"type": "turn_context", "payload": {"model": "model-b", "effort": "low"}},
        ],
    )

    metadata = extract_codex_child_metadata(
        child, expected_parent_id="parent-id", expected_child_id="child-id"
    )

    assert metadata["role"] == "plan-foundation-auditor"
    assert "effective_model" not in metadata
    assert "effective_effort" not in metadata
    assert metadata["metadata_conflicts"] == ["effective_model", "effective_effort"]


def test_observed_child_metadata_treats_null_role_as_unresolved(tmp_path) -> None:
    child = tmp_path / "child.jsonl"
    _write_rollout(
        child,
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "child-id",
                    "parent_thread_id": "parent-id",
                    "agent_role": None,
                },
            }
        ],
    )

    metadata = extract_codex_child_metadata(
        child, expected_parent_id="parent-id", expected_child_id="child-id"
    )

    assert "role" not in metadata


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
