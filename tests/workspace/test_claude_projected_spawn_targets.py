"""Every child-spawn target in Claude-projected skills resolves to a native agent."""

from __future__ import annotations

import re
from importlib import import_module

import pytest

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    RepositoryProfileId,
    SkillExecutionRole,
    SkillSource,
)
from tests.contracts._projection_helpers import native_spawn_target_universes

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

_SPAWN_TARGET = re.compile(r"subagent_type=(['\"])([^'\"]+)\1")


def test_claude_projected_skill_targets_resolve(tmp_path) -> None:
    from autoskillit.workspace import (
        DefaultSkillResolver,
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillProjectionContext,
        compile_session_skill_catalog,
        materialize_agent_skill_tree,
    )

    backend = import_module("autoskillit.execution.backends").ClaudeCodeBackend()
    source_infos = tuple(
        skill
        for skill in DefaultSkillResolver().list_all()
        if skill.source in {SkillSource.BUNDLED, SkillSource.BUNDLED_EXTENDED}
        and skill.execution_role is SkillExecutionRole.SESSION
    )
    catalog = EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(skill) for skill in source_infos),
        execution_role=SkillExecutionRole.SESSION,
    )
    context = SkillProjectionContext(
        cwd=tmp_path,
        catalog=catalog,
        backend=backend,
        resolved_exploration_profile=RepositoryProfileId.LANGUAGE_NEUTRAL,
        provisioning_disposition=True,
    )

    compilation = compile_session_skill_catalog(catalog, backend)
    documents = materialize_agent_skill_tree(tmp_path / "skills", compilation.catalog, context)
    rendered_targets = {
        match.group(2)
        for document in documents.values()
        for match in _SPAWN_TARGET.finditer(document.content)
    }
    allowed = native_spawn_target_universes()[AGENT_BACKEND_CLAUDE_CODE]

    assert rendered_targets <= allowed, sorted(rendered_targets - allowed)
    assert "general-purpose" in rendered_targets
    assert any(target.startswith("autoskillit:") for target in rendered_targets)
