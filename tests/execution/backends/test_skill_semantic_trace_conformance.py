"""Shared Claude/Codex trace conformance for portable skill semantics."""

from __future__ import annotations

import json
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    CODEX_EFFORT_MAPPING,
    CODEX_MODEL_ALIASES,
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
            ChildSpawnSpec(role=_REVIEW_ROLE, count=1),
            ChildSpawnSpec(role=_WORKER_ROLE, count=1),
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
                "task_name": "review",
            },
        ),
        _codex_output("spawn-review", {"task_name": "/root/review"}),
        _codex_call(
            "spawn-worker",
            "spawn_agent",
            {
                "agent_type": worker,
                "fork_turns": "none",
                "task_name": "worker",
            },
        ),
        _codex_output("spawn-worker", {"task_name": "/root/worker"}),
        _codex_call("sibling", "Skill", {"skill": sibling}),
        _codex_output("sibling", {"result": "sibling-delivery-complete"}),
        _codex_call(
            "wait",
            "wait_agent",
            {"timeout_ms": 3_600_000},
        ),
        _codex_output("wait", {"timed_out": False}),
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "<subagent_notification>\n"
                            '{"agent_path":"/root/review","status":'
                            '{"completed":"child-delivery-complete review"}}\n'
                            "</subagent_notification>"
                        ),
                    }
                ],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "<subagent_notification>\n"
                            '{"agent_path":"/root/worker","status":'
                            '{"completed":"child-delivery-complete worker"}}\n'
                            "</subagent_notification>"
                        ),
                    }
                ],
            },
        },
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
                "agent_path": "/root/review",
                "base_instructions": {"text": _DISCIPLINE_DIGEST},
            },
        },
        {
            "type": "session_meta",
            "payload": {
                "id": "child-worker",
                "parent_thread_id": "parent",
                "agent_role": worker,
                "agent_path": "/root/worker",
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
def test_shared_oracle_resolves_runtime_child_cardinality(
    backend_name: str,
    backend_type: type[CodexBackend] | type[ClaudeCodeBackend],
    trace_factory,
) -> None:
    plan = _semantic_plan()
    plan = replace(
        plan,
        child_spawns=(
            ChildSpawnSpec(role=_REVIEW_ROLE, for_each="review_topics"),
            plan.child_spawns[1],
        ),
    )
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
        runtime_cardinalities={"review_topics": 1},
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
        child_spawns=(ChildSpawnSpec(role=_REVIEW_ROLE, count=1),),
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


def test_compose_pr_real_codex_trace_spawns_then_joins_registered_roles() -> None:
    skill_md = pkg_root() / "skills_extended" / "compose-pr" / "SKILL.md"
    info = _skill_info_from_frontmatter("compose-pr", SkillSource.BUNDLED, skill_md)
    assert not info.invalidities
    assert info.semantic_plan is not None
    plan = info.semantic_plan
    adaptation = CodexBackend().adapt_skill_semantics(plan)
    reader = adaptation.logical_role_mapping["pr-source-reader"]
    synthesizer = adaptation.logical_role_mapping["pr-synthesizer"]
    model, effort = adaptation.model_effort_policy[synthesizer]
    assert (reader, synthesizer) == ("pr-source-reader", "pr-synthesizer")
    assert (model, effort) == (
        CODEX_MODEL_ALIASES["sonnet"],
        CODEX_EFFORT_MAPPING["sonnet"],
    )

    parent_events = [
        _codex_call(
            "spawn-reader",
            "spawn_agent",
            {
                "agent_type": reader,
                "fork_turns": "none",
                "task_name": "reader",
            },
        ),
        _codex_output("spawn-reader", {"task_name": "/root/reader"}),
        _codex_call(
            "spawn-synthesizer",
            "spawn_agent",
            {
                "agent_type": synthesizer,
                "fork_turns": "none",
                "model": model,
                "reasoning_effort": effort,
                "task_name": "synthesizer",
            },
        ),
        _codex_output("spawn-synthesizer", {"task_name": "/root/synthesizer"}),
        _codex_call("wait", "wait_agent", {"timeout_ms": 3_600_000}),
        _codex_output("wait", {"timed_out": False}),
    ]
    for task_name in ("reader", "synthesizer"):
        parent_events.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "<subagent_notification>\n"
                                f'{{"agent_path":"/root/{task_name}","status":'
                                f'{{"completed":"child-delivery-complete {task_name}"}}}}\n'
                                "</subagent_notification>"
                            ),
                        }
                    ],
                },
            }
        )
    parent_events.append(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "parent-delivery-complete"}],
            },
        }
    )
    child_events = [
        {
            "type": "session_meta",
            "payload": {
                "id": f"child-{task_name}",
                "parent_thread_id": "parent",
                "agent_role": role,
                "agent_path": f"/root/{task_name}",
                "base_instructions": {"text": _DISCIPLINE_DIGEST},
            },
        }
        for task_name, role in (("reader", reader), ("synthesizer", synthesizer))
    ]

    assert_generated_child_delivery(
        parent_events,
        child_events,
        parent_id="parent",
        agent_role=synthesizer,
        output_discipline_digest=_DISCIPLINE_DIGEST,
        backend="codex",
        semantic_plan=plan,
        semantic_adaptation=adaptation,
        child_terminal_sentinel="child-delivery-complete",
        parent_terminal_sentinel="parent-delivery-complete",
    )


def test_dynamic_child_spawn_adapters_preserve_runtime_cardinality() -> None:
    role = "autoskillit:web-evidence-researcher"
    plan = SkillSemanticPlan(
        schema_version=1,
        logical_roles=(LogicalRoleSpec(name=role, purpose="research one topic"),),
        child_spawns=(ChildSpawnSpec(role=role, for_each="research_topics"),),
    )

    claude = ClaudeCodeBackend().adapt_skill_semantics(plan)
    codex = CodexBackend().adapt_skill_semantics(plan)

    claude_text = "\n".join(claude.instruction_fragments)
    codex_text = "\n".join(codex.instruction_fragments)
    assert "per runtime item in 'research_topics'" in claude_text
    assert "subagent_type='autoskillit:web-evidence-researcher'" in claude_text
    assert "model=" not in claude_text
    assert "once per runtime item in 'research_topics'" in codex_text
    assert "agent_type='web-evidence-researcher'" in codex_text
    assert "fork_turns='none'" in codex_text
    assert "model=" not in codex_text
    assert "reasoning_effort=" not in codex_text


def test_review_approach_projects_the_real_named_web_role() -> None:
    skill_md = pkg_root() / "skills_extended" / "review-approach" / "SKILL.md"
    info = _skill_info_from_frontmatter("review-approach", SkillSource.BUNDLED, skill_md)

    assert not info.invalidities
    assert info.semantic_plan is not None
    plan = info.semantic_plan
    role = "autoskillit:web-evidence-researcher"
    assert tuple(item.name for item in plan.logical_roles) == (role,)
    assert plan.child_spawns == (ChildSpawnSpec(role=role, for_each="research_topics"),)
    assert not plan.child_model_policies

    claude_text = "\n".join(ClaudeCodeBackend().adapt_skill_semantics(plan).instruction_fragments)
    codex_text = "\n".join(CodexBackend().adapt_skill_semantics(plan).instruction_fragments)
    assert "subagent_type='autoskillit:web-evidence-researcher'" in claude_text
    assert "per runtime item in 'research_topics'" in claude_text
    assert "model=" not in claude_text
    assert "agent_type='web-evidence-researcher'" in codex_text
    assert "once per runtime item in 'research_topics'" in codex_text
    assert "fork_turns='none'" in codex_text
    assert "model=" not in codex_text
    assert "reasoning_effort=" not in codex_text


def test_analyze_pipeline_health_projects_the_real_terminal_reader() -> None:
    skill_md = pkg_root() / "skills_extended" / "analyze-pipeline-health" / "SKILL.md"
    info = _skill_info_from_frontmatter(
        "analyze-pipeline-health",
        SkillSource.BUNDLED,
        skill_md,
    )

    assert not info.invalidities
    assert info.semantic_plan is not None
    plan = info.semantic_plan
    role = "autoskillit:session-log-reader"
    assert tuple(item.name for item in plan.logical_roles) == (role,)
    assert plan.child_spawns == (ChildSpawnSpec(role=role, for_each="reader_packets"),)
    assert not plan.child_model_policies

    claude_text = "\n".join(ClaudeCodeBackend().adapt_skill_semantics(plan).instruction_fragments)
    codex_text = "\n".join(CodexBackend().adapt_skill_semantics(plan).instruction_fragments)
    assert "subagent_type='autoskillit:session-log-reader'" in claude_text
    assert "per runtime item in 'reader_packets'" in claude_text
    assert "model=" not in claude_text
    assert "agent_type='session-log-reader'" in codex_text
    assert "once per runtime item in 'reader_packets'" in codex_text
    assert "fork_turns='none'" in codex_text
    assert "model=" not in codex_text
    assert "reasoning_effort=" not in codex_text


@pytest.mark.parametrize(
    ("skill_name", "role", "collection", "source_text"),
    [
        (
            "planner-elaborate-assignments",
            "delegated-worker",
            "assignment_ids",
            "assignment_ids = metadata.assignment_ids",
        ),
        (
            "planner-elaborate-wps",
            "wp-elaborator",
            "pending_wp_ids",
            "Set `pending_wp_ids` to the WP IDs remaining after that filter",
        ),
    ],
)
def test_real_planner_workflows_project_their_runtime_collections(
    skill_name: str, role: str, collection: str, source_text: str
) -> None:
    skill_md = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
    info = _skill_info_from_frontmatter(skill_name, SkillSource.BUNDLED, skill_md)

    assert not info.invalidities
    assert info.semantic_plan is not None
    assert info.semantic_plan.child_spawns == (ChildSpawnSpec(role=role, for_each=collection),)
    assert source_text in skill_md.read_text(encoding="utf-8")
    for backend in (ClaudeCodeBackend(), CodexBackend()):
        rendered = "\n".join(
            backend.adapt_skill_semantics(info.semantic_plan).instruction_fragments
        )
        assert collection in rendered
        assert " 1 " not in rendered


@pytest.mark.parametrize("skill_name", ["review-pr", "enrich-issues"])
def test_real_semantic_skill_materializes_through_codex_adapter(skill_name: str) -> None:
    skill_md = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
    info = _skill_info_from_frontmatter(skill_name, SkillSource.BUNDLED, skill_md)
    assert not info.invalidities
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
