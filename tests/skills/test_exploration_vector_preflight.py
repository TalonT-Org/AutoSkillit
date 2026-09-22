"""Projected exploration-preflight contracts for every session corridor (#4755)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    ExplorationVectorDisposition,
    RepositoryProfileId,
    SkillExecutionRole,
    SkillSource,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.workspace import SkillProjectionContext, materialize_agent_skill_tree
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]


def _exploration_catalog() -> tuple[EffectiveSkillCatalog, frozenset[str], frozenset[str]]:
    """Load the bundled session skills that projection delivers to an agent."""
    source_infos = tuple(
        skill
        for skill in DefaultSkillResolver().list_all()
        if skill.source in {SkillSource.BUNDLED, SkillSource.BUNDLED_EXTENDED}
        and skill.execution_role is SkillExecutionRole.SESSION
        and skill.exploration_vectors
    )
    exploration_skill_names = frozenset(skill.name for skill in source_infos)
    migrated_skill_names = frozenset(
        skill.name
        for skill in source_infos
        if any(
            vector.disposition is ExplorationVectorDisposition.MIGRATED
            for vector in skill.exploration_vectors
        )
    )
    assert exploration_skill_names, "expected bundled session skills with exploration vectors"
    return (
        EffectiveSkillCatalog(
            skills=tuple(SkillCatalogEntry.from_skill_info(skill) for skill in source_infos),
            execution_role=SkillExecutionRole.SESSION,
        ),
        exploration_skill_names,
        migrated_skill_names,
    )


@pytest.mark.parametrize(
    ("backend", "headless"),
    (
        pytest.param(ClaudeCodeBackend(), False, id="claude-interactive"),
        pytest.param(ClaudeCodeBackend(), True, id="claude-headless"),
        pytest.param(CodexBackend(), False, id="codex-interactive"),
        pytest.param(CodexBackend(), True, id="codex-headless"),
    ),
)
def test_projected_exploration_preflight_matches_session_authority(
    tmp_path: Path,
    backend: ClaudeCodeBackend | CodexBackend,
    headless: bool,
) -> None:
    """Only interactive Claude projections instruct session-scoped provisioning.

    The identity-guard bridge is exempted for Codex and headless corridors, so
    their projected bytes must never direct an agent to call
    ``enable_exploration``. Retained-only skills stay in the contract because
    their vector bodies do not reach the migrated-vector renderer.
    """
    catalog, exploration_skill_names, migrated_skill_names = _exploration_catalog()
    session_scoped_provisioning = (
        backend.capabilities.session_scoped_explorer_capable and not headless
    )
    if isinstance(backend, CodexBackend):
        # Canonical join-required skills remain rejected on Codex; this
        # substitution isolates rendering without claiming production admission.
        catalog = EffectiveSkillCatalog(
            skills=tuple(replace(entry, semantic_plan=None) for entry in catalog.skills),
            execution_role=catalog.execution_role,
        )
    context = SkillProjectionContext(
        cwd=tmp_path,
        catalog=catalog,
        backend=backend,
        explorer_provisioning_eligible=session_scoped_provisioning,
        resolved_exploration_profile=RepositoryProfileId.LANGUAGE_NEUTRAL,
    )

    documents = materialize_agent_skill_tree(tmp_path / "skills", catalog, context)

    retained_only_skill_names = exploration_skill_names - migrated_skill_names
    assert retained_only_skill_names, "expected a bundled retained-only exploration skill"
    for skill_name in sorted(exploration_skill_names):
        content = documents[skill_name].content
        expected = session_scoped_provisioning and skill_name in migrated_skill_names
        assert ("enable_exploration" in content) is expected, (
            f"{backend.name} headless={headless}: projected {skill_name!r} "
            f"session-scoped preflight expected={expected}"
        )
