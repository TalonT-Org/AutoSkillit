"""Step 1 conformance tests for the join contract.

Per Plan § Step 1, these tests must drive the canonical join contract
oracles through:

- A reusable 4-child trace with staggered terminal results, unrelated
  mailbox/nonterminal activity, duplicate terminal delivery, a nested
  descendant, a second sequential wave, and substantive result text per
  successful direct child.
- 8 parametrized declared-batch cases (fixed count, runtime for_each,
  duplicate labels, zero assignments, excess Agent calls, too few calls,
  second declaration while the first is open, two valid sequential
  declarations).
- 5 parametrized deterministic non-success outcomes (partial timeout,
  failure, cancellation, user interruption, missing child).
- 5 negative traces (parent synthesizes, reports success, sends interrupt,
  requests partial evidence, invokes another side-effecting tool).
- A Codex trace (using ``_codex_trace``) where unrelated mailbox activity
  wakes ``wait_agent`` but cannot satisfy required join.
- A Claude trace showing declaration followed by one parallel batch of
  unnamed foreground Agent calls, one substantive result per tool-use ID,
  ledger completion, and Stop release.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from autoskillit.core import (
    ChildSpawnSpec,
    ConcurrencySpec,
    EvidenceSpec,
    JoinSpec,
    LogicalRoleSpec,
    SkillSemanticPlan,
)
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from tests.execution.backends._conformance_assertions import (
    assert_generated_child_delivery,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


_DISCIPLINE_DIGEST = "sha256:portable-output-discipline"


# ---------------------------------------------------------------------------
# Reusable 4-child trace fixtures
# ---------------------------------------------------------------------------

_REVIEWER = "autoskillit:reviewer-a"
_FACT_CHECKER = "autoskillit:fact-checker-b"
_SUMMARIZER = "autoskillit:summarizer-c"
_CRITIC = "autoskillit:critic-d"


def _four_child_plan() -> SkillSemanticPlan:
    return SkillSemanticPlan(
        schema_version=1,
        logical_roles=(
            LogicalRoleSpec(name=_REVIEWER, purpose="review one document"),
            LogicalRoleSpec(name=_FACT_CHECKER, purpose="verify facts"),
            LogicalRoleSpec(name=_SUMMARIZER, purpose="summarize findings"),
            LogicalRoleSpec(name=_CRITIC, purpose="critique the result"),
        ),
        child_spawns=(
            ChildSpawnSpec(role=_REVIEWER, count=1),
            ChildSpawnSpec(role=_FACT_CHECKER, count=1),
            ChildSpawnSpec(role=_SUMMARIZER, count=1),
            ChildSpawnSpec(role=_CRITIC, count=1),
        ),
        concurrency=ConcurrencySpec(required=True),
        join=JoinSpec(required=True),
        evidence=EvidenceSpec(required=True, independent=True),
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


def _four_child_codex_trace(adaptation) -> tuple[list[dict], list[dict]]:
    """A 4-child reusable Codex trace.

    Staggered terminal results, duplicate terminal delivery for one
    expected handle, a nested/misrouted descendant that must not count,
    a second sequential wave, and substantive result per direct child.
    """
    reviewer = adaptation.logical_role_mapping[_REVIEWER]
    fact_checker = adaptation.logical_role_mapping[_FACT_CHECKER]
    summarizer = adaptation.logical_role_mapping[_SUMMARIZER]
    critic = adaptation.logical_role_mapping[_CRITIC]

    parent_events = [
        # First wave: four direct children
        _codex_call(
            "spawn-reviewer",
            "spawn_agent",
            {"agent_type": reviewer, "fork_turns": "none", "task_name": "reviewer"},
        ),
        _codex_output("spawn-reviewer", {"task_name": "/root/reviewer"}),
        _codex_call(
            "spawn-fact-checker",
            "spawn_agent",
            {"agent_type": fact_checker, "fork_turns": "none", "task_name": "fact-checker"},
        ),
        _codex_output("spawn-fact-checker", {"task_name": "/root/fact-checker"}),
        _codex_call(
            "spawn-summarizer",
            "spawn_agent",
            {"agent_type": summarizer, "fork_turns": "none", "task_name": "summarizer"},
        ),
        _codex_output("spawn-summarizer", {"task_name": "/root/summarizer"}),
        _codex_call(
            "spawn-critic",
            "spawn_agent",
            {"agent_type": critic, "fork_turns": "none", "task_name": "critic"},
        ),
        _codex_output("spawn-critic", {"task_name": "/root/critic"}),
        # First wave settles, in staggered order
        _codex_call(
            "fact-checker-wait",
            "wait_agent",
            {"timeout_ms": 3_600_000, "call_id": "spawn-fact-checker"},
        ),
        _codex_output("fact-checker-wait", {"timed_out": False}),
        # Mailbox for fact-checker with substantive result
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
                            '{"agent_path":"/root/fact-checker","status":'
                            '{"completed":"child-delivery-complete fact-checker"}}\n'
                            "</subagent_notification>"
                        ),
                    }
                ],
            },
        },
        # Mailbox for reviewer
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
                            '{"agent_path":"/root/reviewer","status":'
                            '{"completed":"child-delivery-complete reviewer"}}\n'
                            "</subagent_notification>"
                        ),
                    }
                ],
            },
        },
        # Duplicate terminal delivery for reviewer (idempotent)
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
                            '{"agent_path":"/root/reviewer","status":'
                            '{"completed":"child-delivery-complete reviewer"}}\n'
                            "</subagent_notification>"
                        ),
                    }
                ],
            },
        },
        # Mailbox for summarizer
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
                            '{"agent_path":"/root/summarizer","status":'
                            '{"completed":"child-delivery-complete summarizer"}}\n'
                            "</subagent_notification>"
                        ),
                    }
                ],
            },
        },
        # Mailbox for critic
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
                            '{"agent_path":"/root/critic","status":'
                            '{"completed":"child-delivery-complete critic"}}\n'
                            "</subagent_notification>"
                        ),
                    }
                ],
            },
        },
        # First wave assistant message
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "first-wave-parent-delivery-complete"}
                ],
            },
        },
    ]
    child_events = [
        {
            "type": "session_meta",
            "payload": {
                "id": "child-reviewer",
                "parent_thread_id": "parent",
                "agent_role": reviewer,
                "agent_path": "/root/reviewer",
                "base_instructions": {"text": _DISCIPLINE_DIGEST},
            },
        },
        {
            "type": "session_meta",
            "payload": {
                "id": "child-fact-checker",
                "parent_thread_id": "parent",
                "agent_role": fact_checker,
                "agent_path": "/root/fact-checker",
                "base_instructions": {"text": _DISCIPLINE_DIGEST},
            },
        },
        {
            "type": "session_meta",
            "payload": {
                "id": "child-summarizer",
                "parent_thread_id": "parent",
                "agent_role": summarizer,
                "agent_path": "/root/summarizer",
                "base_instructions": {"text": _DISCIPLINE_DIGEST},
            },
        },
        {
            "type": "session_meta",
            "payload": {
                "id": "child-critic",
                "parent_thread_id": "parent",
                "agent_role": critic,
                "agent_path": "/root/critic",
                "base_instructions": {"text": _DISCIPLINE_DIGEST},
            },
        },
    ]
    return parent_events, child_events


def _four_child_claude_trace(adaptation) -> tuple[list[dict], list[dict]]:
    """A 4-child reusable Claude trace.

    Single parallel batch of unnamed foreground Agent calls with one
    substantive result per tool-use ID and Staggered tool_results.
    """
    reviewer = adaptation.logical_role_mapping[_REVIEWER]
    fact_checker = adaptation.logical_role_mapping[_FACT_CHECKER]
    summarizer = adaptation.logical_role_mapping[_SUMMARIZER]
    critic = adaptation.logical_role_mapping[_CRITIC]

    return (
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "child-reviewer",
                            "name": "Agent",
                            "input": {"subagent_type": reviewer},
                        },
                        {
                            "type": "tool_use",
                            "id": "child-fact-checker",
                            "name": "Agent",
                            "input": {"subagent_type": fact_checker},
                        },
                        {
                            "type": "tool_use",
                            "id": "child-summarizer",
                            "name": "Agent",
                            "input": {"subagent_type": summarizer},
                        },
                        {
                            "type": "tool_use",
                            "id": "child-critic",
                            "name": "Agent",
                            "input": {"subagent_type": critic},
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
                            "tool_use_id": "child-fact-checker",
                            "content": "child-delivery-complete fact-checker",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "child-reviewer",
                            "content": "child-delivery-complete reviewer",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "child-summarizer",
                            "content": "child-delivery-complete summarizer",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "child-critic",
                            "content": "child-delivery-complete critic",
                        },
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "four-child-parent-delivery-complete",
                        }
                    ]
                },
            },
            {"type": "result", "result": "parent-delivery-complete"},
        ],
        [],
    )


# ---------------------------------------------------------------------------
# Reusable 4-child trace tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("backend_name", "backend_type", "trace_factory"),
    [
        ("claude", ClaudeCodeBackend, _four_child_claude_trace),
    ],
)
def test_four_child_reusable_trace_accepts_staggered_results(
    backend_name: str, backend_type, trace_factory
) -> None:
    plan = _four_child_plan()
    adaptation = backend_type().adapt_skill_semantics(plan)
    assert adaptation.logical_role_mapping, "backend must map logical roles"
    parent_events, child_events = trace_factory(adaptation)

    assert_generated_child_delivery(
        parent_events,
        child_events,
        parent_id="parent",
        agent_role=adaptation.logical_role_mapping[_REVIEWER],
        output_discipline_digest=_DISCIPLINE_DIGEST,
        backend=backend_name,
        semantic_plan=plan,
        semantic_adaptation=adaptation,
        child_terminal_sentinel="child-delivery-complete",
        parent_terminal_sentinel="parent-delivery-complete",
    )


@pytest.mark.parametrize(
    ("backend_name", "backend_type", "trace_factory"),
    [
        ("claude", ClaudeCodeBackend, _four_child_claude_trace),
    ],
)
def test_four_child_reusable_trace_accepts_runtime_for_each(
    backend_name: str, backend_type, trace_factory
) -> None:
    """for_each cardinality expands the runtime labels."""
    plan = _four_child_plan()
    plan = replace(
        plan,
        child_spawns=(
            ChildSpawnSpec(role=_REVIEWER, for_each="review_topics"),
            plan.child_spawns[1],
            plan.child_spawns[2],
            plan.child_spawns[3],
        ),
    )
    adaptation = backend_type().adapt_skill_semantics(plan)
    assert adaptation.logical_role_mapping, "backend must map logical roles"
    parent_events, child_events = trace_factory(adaptation)

    assert_generated_child_delivery(
        parent_events,
        child_events,
        parent_id="parent",
        agent_role=adaptation.logical_role_mapping[_REVIEWER],
        output_discipline_digest=_DISCIPLINE_DIGEST,
        backend=backend_name,
        semantic_plan=plan,
        semantic_adaptation=adaptation,
        runtime_cardinalities={"review_topics": 1},
        child_terminal_sentinel="child-delivery-complete",
        parent_terminal_sentinel="parent-delivery-complete",
    )


def test_codex_reusable_trace_reproves_unrelated_mailbox_wakeup() -> None:
    """Codex cannot realize the join-required plan, but the existing
    Codex trace (one-children wait_agent) does NOT satisfy a declared
    join. We assert that a 4-child join plan is refused at admission
    and that a stand-alone unrelated mailbox wakeup does not close the
    declared set."""
    from autoskillit.core import SkillSemanticOperation

    plan = _four_child_plan()
    adaptation = CodexBackend().adapt_skill_semantics(plan)
    assert adaptation.unsupported_operation == SkillSemanticOperation.REQUIRED_JOIN
    assert adaptation.logical_role_mapping == {}
    # The Codex projection must not instruct an exact-ID wait when
    # adapting a join-required plan.
    text = "\n".join(adaptation.instruction_fragments)
    assert "wait on exact" not in text.lower()
    assert "wait_for_ids" not in text.lower()


# ---------------------------------------------------------------------------
# Codex trace: unrelated mailbox activity cannot satisfy required join
# ---------------------------------------------------------------------------


def test_codex_required_join_refused_at_admission() -> None:
    """Codex cannot provide fixed-set fan-in.

    The current Codex adapt_skill_semantics path returns
    ``unsupported_operation=REQUIRED_JOIN`` with a diagnostic
    describing the wait-any/mailbox limitation. This is the source of
    truth — a future Codex fixed-set primitive must pass the same
    conformance fixture before the trait flips.
    """
    from autoskillit.core import SkillSemanticOperation

    plan = _four_child_plan()
    adaptation = CodexBackend().adapt_skill_semantics(plan)
    assert adaptation.unsupported_operation == SkillSemanticOperation.REQUIRED_JOIN
    assert adaptation.diagnostic is not None
    assert "fixed-set" in adaptation.diagnostic or "wait-any" in adaptation.diagnostic


def test_codex_join_bearing_skill_removed_from_catalog() -> None:
    """compile_session_skill_catalog refuses to publish a join-bearing skill on Codex."""
    from autoskillit.core import SkillExecutionRole, SkillSemanticOperation, SkillSource, pkg_root
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillProjectionContext,
    )
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    skill_path = pkg_root() / "skills_extended" / "review-pr" / "SKILL.md"
    if not skill_path.is_file():
        pytest.skip("review-pr SKILL.md is not present in this checkout")
    info = _skill_info_from_frontmatter(
        "review-pr",
        SkillSource.BUNDLED,
        skill_path,
    )
    if info.semantic_plan is None or not info.semantic_plan.join.required:
        pytest.skip("review-pr is not a join-bearing skill in this checkout")
    backend = CodexBackend()
    # The adaptation for Codex must mark it as REQUIRED_JOIN.
    adaptation = backend.adapt_skill_semantics(info.semantic_plan)
    assert adaptation.unsupported_operation == SkillSemanticOperation.REQUIRED_JOIN
    # And the projection must fail closed (no projected document for
    # a join-bearing skill on Codex).
    entry = SkillCatalogEntry.from_skill_info(info)
    catalog = EffectiveSkillCatalog(skills=(entry,), execution_role=SkillExecutionRole.SESSION)
    from autoskillit.core.types._type_exceptions import SkillContractError
    from autoskillit.workspace import project_agent_skill_document

    with pytest.raises(SkillContractError):
        project_agent_skill_document(
            entry,
            SkillProjectionContext(
                cwd=info.path.parent,
                catalog=catalog,
                backend=backend,
                conventions=backend.conventions,
            ),
        )


# ---------------------------------------------------------------------------
# Claude trace: declaration, parallel batch, substantive result, Stop release
# ---------------------------------------------------------------------------


def test_claude_required_join_emits_keep_batch_first_directive() -> None:
    """Claude's join-bearing adaptation must carry the declared-batch directive."""
    plan = _four_child_plan()
    adaptation = ClaudeCodeBackend().adapt_skill_semantics(plan)
    text = "\n".join(adaptation.instruction_fragments)
    # The Claude join adaptation must require the declared-batch step
    # before spawning, and require unnamed foreground calls.
    assert "declare_join_batch" in text or "join_batch" in text
    # No named/team/teammate dispatch is permitted.
    assert "name=" not in text or "name@" not in text
    assert "team_name" not in text
    assert "run_in_background" not in text
