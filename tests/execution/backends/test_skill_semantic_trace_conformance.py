"""Shared Claude/Codex trace conformance for portable skill semantics."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from autoskillit.core import (
    ChildModelPolicySpec,
    ChildSpawnSpec,
    ConcurrencySpec,
    EvidenceSpec,
    JoinSpec,
    LogicalRoleSpec,
    SiblingSkillSpec,
    SkillExecutionRole,
    SkillSemanticAdaptationResult,
    SkillSemanticPlan,
    SkillSource,
    pkg_root,
)
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from autoskillit.execution.backends.codex import _generate_agent_tomls
from autoskillit.workspace import (
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillProjectionContext,
    project_agent_skill_document,
)
from autoskillit.workspace.skills import _skill_info_from_frontmatter
from tests.execution.backends._conformance_assertions import (
    assert_generated_child_delivery,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_DISCIPLINE_DIGEST = "sha256:portable-output-discipline"
_REVIEW_ROLE = "autoskillit:pr-review-auditor-baseline"
_WORKER_ROLE = "delegated-worker"


def _semantic_plan() -> SkillSemanticPlan:
    return SkillSemanticPlan(
        schema_version=1,
        logical_roles=(
            LogicalRoleSpec(name=_REVIEW_ROLE, purpose="audit one independent dimension"),
            LogicalRoleSpec(name=_WORKER_ROLE, purpose="collect independent evidence"),
        ),
        child_spawns=(
            ChildSpawnSpec(role=_REVIEW_ROLE),
            ChildSpawnSpec(role=_WORKER_ROLE),
        ),
        concurrency=ConcurrencySpec(required=True),
        join=JoinSpec(required=True),
        evidence=EvidenceSpec(required=True, independent=True),
        child_model_policies=(
            ChildModelPolicySpec(
                role=_REVIEW_ROLE,
                model_class="sonnet",
                reasoning_effort="high",
            ),
        ),
        sibling_skills=(SiblingSkillSpec(name="smoke-task"),),
    )


def _codex_call(call_id: str, name: str, arguments: dict[str, object]) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def _codex_output(call_id: str, output: object) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(output),
        },
    }


def _codex_trace(
    adaptation: SkillSemanticAdaptationResult,
) -> tuple[list[dict], list[dict]]:
    reviewer = adaptation.logical_role_mapping[_REVIEW_ROLE]
    worker = adaptation.logical_role_mapping[_WORKER_ROLE]
    model, effort = adaptation.model_effort_policy[reviewer]
    sibling = adaptation.sibling_skill_targets["smoke-task"]
    parent_events = [
        _codex_call(
            "spawn-review",
            "spawn_agent",
            {
                "agent_type": reviewer,
                "fork_turns": "none",
                "model": model,
                "reasoning_effort": effort,
            },
        ),
        _codex_output("spawn-review", {"agent_id": "child-review"}),
        _codex_call(
            "spawn-worker",
            "spawn_agent",
            {"agent_type": worker, "fork_turns": "none"},
        ),
        _codex_output("spawn-worker", {"agent_id": "child-worker"}),
        _codex_call("sibling", "Skill", {"skill": sibling}),
        _codex_output("sibling", {"result": "sibling-delivery-complete"}),
        _codex_call(
            "wait",
            "wait_agent",
            {"targets": ["child-review", "child-worker"]},
        ),
        _codex_output(
            "wait",
            {
                "completed": [
                    {
                        "agent_id": "child-review",
                        "result": "child-delivery-complete review",
                    },
                    {
                        "agent_id": "child-worker",
                        "result": "child-delivery-complete worker",
                    },
                ]
            },
        ),
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "parent-delivery-complete"}],
            },
        },
    ]
    child_events = [
        {
            "type": "session_meta",
            "payload": {
                "id": "child-review",
                "parent_thread_id": "parent",
                "agent_role": reviewer,
                "base_instructions": {"text": _DISCIPLINE_DIGEST},
            },
        },
        {
            "type": "session_meta",
            "payload": {
                "id": "child-worker",
                "parent_thread_id": "parent",
                "agent_role": worker,
                "base_instructions": {"text": _DISCIPLINE_DIGEST},
            },
        },
    ]
    return parent_events, child_events


def _claude_trace(
    adaptation: SkillSemanticAdaptationResult,
) -> tuple[list[dict], list[dict]]:
    reviewer = adaptation.logical_role_mapping[_REVIEW_ROLE]
    worker = adaptation.logical_role_mapping[_WORKER_ROLE]
    model, _effort = adaptation.model_effort_policy[reviewer]
    sibling = adaptation.sibling_skill_targets["smoke-task"]
    return (
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "child-review",
                            "name": "Agent",
                            "input": {"subagent_type": reviewer, "model": model},
                        },
                        {
                            "type": "tool_use",
                            "id": "child-worker",
                            "name": "Agent",
                            "input": {"subagent_type": worker},
                        },
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "child-review",
                            "content": "child-delivery-complete review",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "child-worker",
                            "content": "child-delivery-complete worker",
                        },
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "sibling",
                            "name": "Skill",
                            "input": {"skill": sibling},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "sibling",
                            "content": "sibling-delivery-complete",
                        }
                    ]
                },
            },
            {"type": "result", "result": "parent-delivery-complete"},
        ],
        [],
    )


@pytest.mark.parametrize(
    ("backend_name", "backend_type", "trace_factory"),
    [
        ("codex", CodexBackend, _codex_trace),
        ("claude", ClaudeCodeBackend, _claude_trace),
    ],
)
def test_shared_oracle_accepts_backend_native_semantic_trace(
    backend_name: str,
    backend_type: type[CodexBackend] | type[ClaudeCodeBackend],
    trace_factory,
) -> None:
    plan = _semantic_plan()
    adaptation = backend_type().adapt_skill_semantics(plan)
    parent_events, child_events = trace_factory(adaptation)

    assert_generated_child_delivery(
        parent_events,
        child_events,
        parent_id="parent",
        agent_role=adaptation.logical_role_mapping[_REVIEW_ROLE],
        output_discipline_digest=_DISCIPLINE_DIGEST,
        backend=backend_name,
        semantic_plan=plan,
        semantic_adaptation=adaptation,
        child_terminal_sentinel="child-delivery-complete",
        sibling_result_sentinel="sibling-delivery-complete",
        parent_terminal_sentinel="parent-delivery-complete",
    )


@pytest.mark.parametrize(
    ("backend_name", "backend_type", "trace_factory"),
    [
        ("codex", CodexBackend, _codex_trace),
        ("claude", ClaudeCodeBackend, _claude_trace),
    ],
)
def test_shared_oracle_rejects_parent_success_before_child_delivery(
    backend_name: str,
    backend_type: type[CodexBackend] | type[ClaudeCodeBackend],
    trace_factory,
) -> None:
    plan = _semantic_plan()
    adaptation = backend_type().adapt_skill_semantics(plan)
    parent_events, child_events = trace_factory(adaptation)
    parent_events.insert(0, {"type": "result", "result": "parent-delivery-complete"})

    with pytest.raises(AssertionError, match="before every child terminal result"):
        assert_generated_child_delivery(
            parent_events,
            child_events,
            parent_id="parent",
            agent_role=adaptation.logical_role_mapping[_REVIEW_ROLE],
            output_discipline_digest=_DISCIPLINE_DIGEST,
            backend=backend_name,
            semantic_plan=plan,
            semantic_adaptation=adaptation,
            child_terminal_sentinel="child-delivery-complete",
            sibling_result_sentinel="sibling-delivery-complete",
            parent_terminal_sentinel="parent-delivery-complete",
        )


def test_codex_semantic_policy_matches_generated_native_role_toml(tmp_path: Path) -> None:
    _generate_agent_tomls(tmp_path)
    native_role = "pr-review-auditor-baseline"
    agent_config = tomllib.loads(
        (tmp_path / "agents" / f"{native_role}.toml").read_text(encoding="utf-8")
    )
    plan = SkillSemanticPlan(
        schema_version=1,
        logical_roles=(LogicalRoleSpec(name=_REVIEW_ROLE, purpose="audit a PR"),),
        child_spawns=(ChildSpawnSpec(role=_REVIEW_ROLE),),
        child_model_policies=(
            ChildModelPolicySpec(
                role=_REVIEW_ROLE,
                model_class="sonnet",
                reasoning_effort="medium",
            ),
        ),
    )
    adaptation = CodexBackend().adapt_skill_semantics(plan)

    assert adaptation.logical_role_mapping[_REVIEW_ROLE] == native_role
    assert adaptation.model_effort_policy[native_role] == (
        agent_config["model"],
        agent_config["model_reasoning_effort"],
    )


@pytest.mark.parametrize("skill_name", ["review-pr", "enrich-issues"])
def test_real_semantic_skill_materializes_through_codex_adapter(skill_name: str) -> None:
    skill_md = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
    info = _skill_info_from_frontmatter(skill_name, SkillSource.BUNDLED, skill_md)
    assert info.invalid_reason is None
    entry = SkillCatalogEntry.from_skill_info(info)
    catalog = EffectiveSkillCatalog(
        skills=(entry,),
        execution_role=SkillExecutionRole.SESSION,
    )
    backend = CodexBackend()

    document = project_agent_skill_document(
        entry,
        SkillProjectionContext(
            cwd=skill_md.parent,
            catalog=catalog,
            backend=backend,
            conventions=backend.conventions,
        ),
    )

    assert "spawn_agent" in document.content
    assert "wait_agent" in document.content
    assert "gpt-5.6-sol" in document.content
    assert document.semantic_digest
    assert document.adaptation_digest
