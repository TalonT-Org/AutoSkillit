"""Rendered child-spawn targets resolve in each backend's native agent namespace."""

from __future__ import annotations

import json
import re

import pytest

from autoskillit.core import AGENT_BACKEND_CLAUDE_CODE, AGENT_BACKEND_CODEX, pkg_root
from tests.contracts._projection_helpers import native_spawn_target_universes

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_RENDERED_TARGET = {
    AGENT_BACKEND_CLAUDE_CODE: re.compile(r"\bsubagent_type='([^']+)'"),
    AGENT_BACKEND_CODEX: re.compile(r"\bagent_type='([^']+)'"),
}


def test_universe_table_covers_every_registered_backend() -> None:
    from autoskillit.execution.backends import BACKEND_REGISTRY

    universes = native_spawn_target_universes()

    assert set(universes) == set(BACKEND_REGISTRY) == set(_RENDERED_TARGET)


def test_every_rendered_spawn_target_resolves_on_every_backend() -> None:
    from autoskillit.execution.backends import BACKEND_REGISTRY
    from autoskillit.workspace import DefaultSkillResolver

    universes = native_spawn_target_universes()
    plugin_name = json.loads(
        (pkg_root() / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )["name"]
    plans = tuple(
        (skill.name, skill.semantic_plan)
        for skill in DefaultSkillResolver().list_all()
        if skill.semantic_plan is not None
    )
    assert plans

    violations: list[str] = []
    claude_targets: set[str] = set()
    for backend_name, backend_factory in BACKEND_REGISTRY.items():
        backend = backend_factory()
        pattern = _RENDERED_TARGET[backend_name]
        for skill_name, plan in plans:
            assert plan is not None
            adaptation = backend.adapt_skill_semantics(plan)
            if adaptation.unsupported_operation is not None:
                continue

            rendered = {
                target
                for fragment in adaptation.instruction_fragments
                for target in pattern.findall(fragment)
            }
            if backend_name == AGENT_BACKEND_CLAUDE_CODE:
                claude_targets.update(rendered)

            unresolved = sorted(rendered - universes[backend_name])
            if unresolved:
                violations.append(
                    f"{skill_name}/{backend_name}: rendered targets outside universe {unresolved}"
                )

            for spawn in plan.child_spawns:
                if spawn.role in plan.runtime_bound_role_names:
                    continue
                mapped = adaptation.logical_role_mapping.get(spawn.role)
                if mapped not in rendered:
                    violations.append(
                        f"{skill_name}/{backend_name}: static role {spawn.role!r} maps to "
                        f"{mapped!r}, absent from rendered targets {sorted(rendered)}"
                    )

            expected_policy_keys = {
                adaptation.logical_role_mapping.get(policy.role)
                for policy in plan.child_model_policies
            }
            actual_policy_keys = set(adaptation.model_effort_policy)
            if actual_policy_keys != expected_policy_keys:
                violations.append(
                    f"{skill_name}/{backend_name}: model policy keys {sorted(actual_policy_keys)} "
                    "do not match mapped child-policy roles "
                    f"{sorted(expected_policy_keys, key=repr)}"
                )

    assert not violations, "native spawn target contract violations:\n" + "\n".join(
        sorted(violations)
    )
    assert "general-purpose" in claude_targets
    assert any(target.startswith(f"{plugin_name}:") for target in claude_targets)
