"""T7: ineligible projection context suppresses explorer dispatch text."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    SkillExecutionRole,
    SkillSource,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def test_ineligible_context_renders_unavailable_text(tmp_path: Path) -> None:
    """When explorer_provisioning_eligible is False, dispatch text is suppressed."""
    from autoskillit.workspace import SkillProjectionContext, materialize_agent_skill_tree
    from autoskillit.workspace.skills import (
        DefaultSkillResolver,
        EffectiveSkillCatalog,
        SkillCatalogEntry,
    )

    source_infos = tuple(
        s for s in DefaultSkillResolver().list_all() if s.source is SkillSource.BUNDLED
    )
    has_exploration = any(s.exploration_vectors for s in source_infos)
    if not has_exploration:
        pytest.skip("no bundled skills with exploration vectors")

    catalog = EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in source_infos),
        execution_role=SkillExecutionRole.SESSION,
    )
    context = SkillProjectionContext(
        cwd=tmp_path,
        catalog=catalog,
        explorer_provisioning_eligible=False,
    )

    destination = tmp_path / "skills"
    documents = materialize_agent_skill_tree(destination, catalog, context)

    for name, doc in documents.items():
        if "do not dispatch this exploration vector" in doc.content:
            return
        if "Explorer provisioning is unavailable" in doc.content:
            return
    if has_exploration:
        pytest.fail(
            "Expected at least one skill to contain 'do not dispatch' or "
            "'Explorer provisioning is unavailable' when eligible=False"
        )


def test_eligible_none_preserves_dispatch(tmp_path: Path) -> None:
    """When explorer_provisioning_eligible is None (default), dispatch is preserved."""
    from autoskillit.workspace import SkillProjectionContext, materialize_agent_skill_tree
    from autoskillit.workspace.skills import (
        DefaultSkillResolver,
        EffectiveSkillCatalog,
        SkillCatalogEntry,
    )

    source_infos = tuple(
        s for s in DefaultSkillResolver().list_all() if s.source is SkillSource.BUNDLED
    )
    has_exploration = any(s.exploration_vectors for s in source_infos)
    if not has_exploration:
        pytest.skip("no bundled skills with exploration vectors")

    catalog = EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in source_infos),
        execution_role=SkillExecutionRole.SESSION,
    )
    context = SkillProjectionContext(
        cwd=tmp_path,
        catalog=catalog,
    )

    destination = tmp_path / "skills"
    documents = materialize_agent_skill_tree(destination, catalog, context)

    for name, doc in documents.items():
        assert "Explorer provisioning is unavailable" not in doc.content, (
            f"Skill {name!r} should NOT contain unavailability text when eligible=None"
        )
