"""Runtime-selected logical roles stay unbound until Claude launches a child."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from autoskillit.core import (
    ChildSpawnSpec,
    JoinSpec,
    LogicalRoleSpec,
    SkillSemanticOperation,
    SkillSemanticPlan,
    pkg_root,
)
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend

_RUNTIME_ROLE = "evaluated-agent"
_STATIC_ROLE = "delegated-worker"
_RUNTIME_NATIVE_AGENT = "autoskillit:pr-review-auditor-baseline"
_DISCIPLINE_DIGEST = "runtime-bound-role-test-discipline"


def _runtime_bound_plan(*, runtime_only: bool = False) -> SkillSemanticPlan:
    roles = (
        LogicalRoleSpec(
            name=_RUNTIME_ROLE,
            purpose="evaluate the selected agent definition",
            runtime_bound=True,
        ),
    )
    spawns = (ChildSpawnSpec(role=_RUNTIME_ROLE, count=1),)
    if not runtime_only:
        roles += (LogicalRoleSpec(name=_STATIC_ROLE, purpose="collect evidence"),)
        spawns += (ChildSpawnSpec(role=_STATIC_ROLE, count=1),)
    return SkillSemanticPlan(
        schema_version=1,
        logical_roles=roles,
        child_spawns=spawns,
        join=JoinSpec(required=False),
    )


def test_claude_renders_runtime_bound_role_in_plugin_namespace() -> None:
    from autoskillit.core.plugins import CLAUDE_PLUGIN_AGENT_NAMESPACE

    plan = _runtime_bound_plan()
    adaptation = ClaudeCodeBackend().adapt_skill_semantics(plan)

    assert adaptation.unsupported_operation is None
    assert adaptation.runtime_bound_roles == frozenset({_RUNTIME_ROLE})
    assert _RUNTIME_ROLE not in adaptation.logical_role_mapping
    assert any(
        "bound at runtime to a bundled agent definition" in fragment
        for fragment in adaptation.instruction_fragments
    )
    assert any(
        f"subagent_type set to {CLAUDE_PLUGIN_AGENT_NAMESPACE!r}" in fragment
        for fragment in adaptation.instruction_fragments
    )
    assert not any(
        re.search(r"subagent_type=['\"]evaluated-agent", fragment)
        for fragment in adaptation.instruction_fragments
    )


def test_codex_refuses_runtime_bound_role_and_required_join_takes_precedence() -> None:
    plan = _runtime_bound_plan()
    adaptation = CodexBackend().adapt_skill_semantics(plan)

    assert adaptation.unsupported_operation is SkillSemanticOperation.CHILD_SPAWN
    assert adaptation.diagnostic is not None
    assert "runtime-selected bundled agent" in adaptation.diagnostic
    assert adaptation.instruction_fragments == ()

    required_join = CodexBackend().adapt_skill_semantics(
        replace(plan, join=JoinSpec(required=True))
    )
    assert required_join.unsupported_operation is SkillSemanticOperation.REQUIRED_JOIN
    assert required_join.instruction_fragments == ()


def test_codex_mapping_excludes_runtime_bound_roles() -> None:
    from autoskillit.execution.backends.codex import _codex_logical_role_mapping

    assert _codex_logical_role_mapping(_runtime_bound_plan()) == {_STATIC_ROLE: "worker"}


def test_claude_plugin_agent_namespace_matches_manifest() -> None:
    from autoskillit.core.plugins import CLAUDE_PLUGIN_AGENT_NAMESPACE

    manifest = json.loads(
        (pkg_root() / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert CLAUDE_PLUGIN_AGENT_NAMESPACE == manifest["name"] + ":"


def test_exploration_renderer_shares_plugin_agent_namespace() -> None:
    from autoskillit.core.plugins import CLAUDE_PLUGIN_AGENT_NAMESPACE
    from autoskillit.execution.backends._explorer_dispatch import (
        CLAUDE_EXPLORATION_DISPATCH_RENDERER,
    )

    assert (
        CLAUDE_EXPLORATION_DISPATCH_RENDERER.conventions.role_prefix
        == CLAUDE_PLUGIN_AGENT_NAMESPACE
    )


def _claude_trace(native_roles: tuple[str, ...]) -> list[dict]:
    calls = [
        {
            "type": "tool_use",
            "id": f"child-{index}",
            "name": "Agent",
            "input": {"subagent_type": native_role},
        }
        for index, native_role in enumerate(native_roles)
    ]
    results = [
        {
            "type": "tool_result",
            "tool_use_id": f"child-{index}",
            "content": "child-delivery-complete",
        }
        for index, _native_role in enumerate(native_roles)
    ]
    if not calls:
        return []
    return [
        {"type": "assistant", "message": {"content": calls}},
        {"type": "user", "message": {"content": results}},
    ]


def test_runtime_bound_conformance_uses_supplied_native_name_and_declared_count() -> None:
    from tests.execution.backends._conformance_assertions import (
        assert_generated_child_delivery,
    )

    plan = _runtime_bound_plan(runtime_only=True)
    adaptation = ClaudeCodeBackend().adapt_skill_semantics(plan)

    assert_generated_child_delivery(
        _claude_trace((_RUNTIME_NATIVE_AGENT,)),
        [],
        parent_id="parent",
        agent_role="unused-with-semantic-plan",
        output_discipline_digest=_DISCIPLINE_DIGEST,
        backend="claude",
        semantic_plan=plan,
        semantic_adaptation=adaptation,
        runtime_native_roles={_RUNTIME_ROLE: _RUNTIME_NATIVE_AGENT},
        child_terminal_sentinel="child-delivery-complete",
    )


@pytest.mark.parametrize(
    ("native_roles", "failure"),
    [
        ((), "expected 1 native child calls"),
        (("autoskillit:plan-foundation-auditor",), "native role mapping mismatch"),
    ],
    ids=("missing-child", "wrong-native-name"),
)
def test_runtime_bound_conformance_rejects_missing_or_wrong_child(
    native_roles: tuple[str, ...],
    failure: str,
) -> None:
    from tests.execution.backends._conformance_assertions import (
        assert_generated_child_delivery,
    )

    plan = _runtime_bound_plan(runtime_only=True)
    adaptation = ClaudeCodeBackend().adapt_skill_semantics(plan)

    with pytest.raises(AssertionError, match=failure):
        assert_generated_child_delivery(
            _claude_trace(native_roles),
            [],
            parent_id="parent",
            agent_role="unused-with-semantic-plan",
            output_discipline_digest=_DISCIPLINE_DIGEST,
            backend="claude",
            semantic_plan=plan,
            semantic_adaptation=adaptation,
            runtime_native_roles={_RUNTIME_ROLE: _RUNTIME_NATIVE_AGENT},
        )
