"""Backend integration coverage for session-skill refusal materialization."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    SkillContractError,
    SkillExecutionRole,
    SkillSemanticAdaptationResult,
    SkillSemanticOperation,
    SkillSource,
)
from autoskillit.execution.backends import CodexBackend
from autoskillit.workspace import (
    DefaultSessionSkillManager,
    EffectiveSkillInvocation,
    SkillInfo,
    SkillProjectionContext,
    SkillsDirectoryProvider,
)
from autoskillit.workspace.skills import _skill_info_from_frontmatter

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _child_spawn_skill(tmp_path: Path) -> SkillInfo:
    skill_path = tmp_path / "semantic-skill" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\n"
        "name: semantic-skill\n"
        "description: semantic fixture\n"
        "semantic_version: 1\n"
        "semantic_requirements:\n"
        "  logical_roles:\n"
        "    - name: worker\n"
        "      purpose: perform delegated work\n"
        "  child_spawns:\n"
        "    - role: worker\n"
        "---\n"
        "Delegate to the worker.\n",
        encoding="utf-8",
    )
    return _skill_info_from_frontmatter(
        "semantic-skill",
        SkillSource.PROJECT_LOCAL,
        skill_path,
    )


def test_materialize_invocation_uses_structured_refusal_and_preserves_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _child_spawn_skill(tmp_path)
    invocation = EffectiveSkillInvocation(
        root=skill,
        closure=(skill,),
        capability_union=skill.uses_capabilities,
        project_root=tmp_path,
        execution_role=SkillExecutionRole.SESSION,
    )
    diagnostic = "Child delegation is unavailable in this Codex environment."
    monkeypatch.setattr(
        CodexBackend,
        "adapt_skill_semantics",
        lambda _self, _plan: SkillSemanticAdaptationResult(
            unsupported_operation=SkillSemanticOperation.CHILD_SPAWN,
            diagnostic=diagnostic,
        ),
    )
    backend = CodexBackend()
    context = SkillProjectionContext(
        cwd=tmp_path,
        invocation=invocation,
        backend=backend,
    )
    persistent_root = tmp_path / "persistent" / "codex-sessions"
    manager = DefaultSessionSkillManager(
        SkillsDirectoryProvider(),
        ephemeral_root=tmp_path / "ephemeral",
        persistent_roots={"codex": persistent_root},
    )

    with pytest.raises(SkillContractError) as exc_info:
        manager.materialize_invocation("structured-refusal", invocation, context)

    assert str(exc_info.value) == diagnostic
    assert not (persistent_root / "structured-refusal").exists()
