"""Map declared logical roles to backend-native agent names."""

from __future__ import annotations

from autoskillit.core import (
    CLAUDE_PLUGIN_AGENT_NAMESPACE,
    DELEGATED_WORKER_ROLE,
    SkillSemanticPlan,
    load_bundled_agent_definitions,
)

__all__ = [
    "CLAUDE_DELEGATED_WORKER_AGENT",
    "CLAUDE_SPAWNABLE_BUILT_IN_AGENT_NAMES",
    "claude_resolvable_agent_names",
    "map_declared_logical_roles",
]

CLAUDE_DELEGATED_WORKER_AGENT = "general-purpose"

# Common CLI/headless sessions share these; claude-code-guide is excluded from sdk-cli.
CLAUDE_SPAWNABLE_BUILT_IN_AGENT_NAMES = (
    CLAUDE_DELEGATED_WORKER_AGENT,
    "Explore",
    "Plan",
    "claude",
    "statusline-setup",
)


def claude_resolvable_agent_names() -> frozenset[str]:
    """Return built-in and plugin-provided agent names resolvable in Claude sessions."""
    return frozenset(CLAUDE_SPAWNABLE_BUILT_IN_AGENT_NAMES) | {
        f"{CLAUDE_PLUGIN_AGENT_NAMESPACE}{definition.name}"
        for definition in load_bundled_agent_definitions()
    }


def map_declared_logical_roles(
    plan: SkillSemanticPlan,
    *,
    delegated_worker_agent: str,
    agent_namespace: str,
) -> dict[str, str]:
    """Map each statically declared logical role to its native agent name."""
    return {
        role.name: (
            delegated_worker_agent
            if role.name == DELEGATED_WORKER_ROLE
            else f"{agent_namespace}{role.name}"
        )
        for role in plan.logical_roles
        if not role.runtime_bound
    }
