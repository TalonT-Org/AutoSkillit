"""Normalization of declared logical roles to backend-native agent names."""

from __future__ import annotations

import pytest

from autoskillit.core import ChildSpawnSpec, LogicalRoleSpec, SkillSemanticPlan

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.mark.parametrize(
    ("delegated_worker_agent", "agent_namespace", "expected"),
    [
        (
            "general-purpose",
            "autoskillit:",
            {
                "delegated-worker": "general-purpose",
                "plan-foundation-auditor": "autoskillit:plan-foundation-auditor",
            },
        ),
        (
            "worker",
            "",
            {
                "delegated-worker": "worker",
                "plan-foundation-auditor": "plan-foundation-auditor",
            },
        ),
    ],
    ids=("claude", "codex"),
)
def test_map_declared_logical_roles(
    delegated_worker_agent: str,
    agent_namespace: str,
    expected: dict[str, str],
) -> None:
    from autoskillit.execution.backends._native_roles import map_declared_logical_roles

    plan = SkillSemanticPlan(
        schema_version=1,
        child_spawns=(ChildSpawnSpec(role="evaluated-agent", count=1),),
        logical_roles=(
            LogicalRoleSpec(name="delegated-worker", purpose="perform general work"),
            LogicalRoleSpec(name="plan-foundation-auditor", purpose="audit a plan"),
            LogicalRoleSpec(
                name="evaluated-agent",
                purpose="use the selected bundled agent",
                runtime_bound=True,
            ),
        ),
    )

    assert (
        map_declared_logical_roles(
            plan,
            delegated_worker_agent=delegated_worker_agent,
            agent_namespace=agent_namespace,
        )
        == expected
    )
