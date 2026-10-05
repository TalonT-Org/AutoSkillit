"""Claude session admission uses the native names its session can resolve."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest

from autoskillit.core import SkillContractError, SkillExecutionRole, SkillSource
from autoskillit.workspace import DefaultSkillResolver, SkillProjectionContext
from tests.workspace._helpers import _managed, _write_project_skill_override

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _write_child_spawn_skill(project_root: Path, name: str, role: str) -> Path:
    content = (
        "---\n"
        f"name: {name}\n"
        "description: Delegate one child task.\n"
        "uses_capabilities: []\n"
        "execution_role: session\n"
        "semantic_version: 1\n"
        "semantic_requirements:\n"
        "  logical_roles:\n"
        f"    - name: {role}\n"
        "      purpose: perform one child task\n"
        "  child_spawns:\n"
        f"    - role: {role}\n"
        "      count: 1\n"
        "write_paths: inherit\n"
        "---\n"
        "Delegate one child task using the declared semantic role.\n"
    )
    return _write_project_skill_override(project_root, name, content)


def test_claude_invocation_with_unresolvable_target_fails_closed(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _write_child_spawn_skill(project_root, "unresolvable-target", "nonexistent-agent")
    invocation = DefaultSkillResolver().resolve_invocation(
        "unresolvable-target",
        project_root,
        SkillExecutionRole.SESSION,
    )
    backend = import_module("autoskillit.execution.backends").ClaudeCodeBackend()
    manager = make_session_skill_manager()
    projection_context = SkillProjectionContext(
        cwd=project_root,
        invocation=invocation,
        backend=backend,
    )

    with pytest.raises(
        SkillContractError,
        match=r"native child-spawn targets are unavailable: \['autoskillit:nonexistent-agent'\]",
    ):
        manager.materialize_invocation(
            "unresolvable-target-admission",
            invocation,
            projection_context,
        )


def test_claude_catalog_prunes_unresolvable_skill(
    make_session_skill_manager,
) -> None:
    backend = import_module("autoskillit.execution.backends").ClaudeCodeBackend()
    manager = make_session_skill_manager()
    _write_child_spawn_skill(manager.ephemeral_root, "unresolvable-target", "nonexistent-agent")
    _write_child_spawn_skill(manager.ephemeral_root, "portable-review", "delegated-worker")

    with _managed(
        manager,
        "claude-catalog-admission",
        backend=backend,
        names=frozenset({"unresolvable-target", "portable-review"}),
    ) as managed:
        catalog_root = Path(managed.skills_dir.path) / "skills"
        unavailable = {
            item["skill"]: item["operation"]
            for item in managed.unavailability_payload["unavailable"]
        }

        assert unavailable == {"unresolvable-target": "child_spawn"}
        assert not (catalog_root / "unresolvable-target").exists()
        projected_skill = catalog_root / "portable-review" / "SKILL.md"
        assert projected_skill.is_file()
        assert "subagent_type='general-purpose'" in projected_skill.read_text(encoding="utf-8")


def test_claude_invocation_admits_runtime_bound_eval_agent(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    backend = import_module("autoskillit.execution.backends").ClaudeCodeBackend()
    invocation = DefaultSkillResolver().resolve_invocation(
        "eval-agent",
        _REPOSITORY_ROOT,
        SkillExecutionRole.SESSION,
    )
    assert invocation.root.source is SkillSource.PROJECT_LOCAL
    manager = make_session_skill_manager()
    session_id = "claude-eval-agent-admission"
    result = manager.materialize_invocation(
        session_id,
        invocation,
        SkillProjectionContext(
            cwd=_REPOSITORY_ROOT,
            invocation=invocation,
            backend=backend,
        ),
    )

    projected_skill = (
        Path(result.path) / backend.conventions.skills_subdir / "eval-agent" / "SKILL.md"
    )
    content = projected_skill.read_text(encoding="utf-8")
    assert "subagent_type set to 'autoskillit:'" in content
    assert "subagent_type='autoskillit:evaluated-agent'" not in content
    assert manager.cleanup_session(session_id) is True
