"""T7: ineligible projection context routes to the pluginless-explorer fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    ExplorationVectorDisposition,
    RepositoryProfileId,
    SkillExecutionRole,
    SkillSource,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _exploration_projection_context(
    tmp_path: Path,
    *,
    explorer_provisioning_eligible: bool | None,
):
    """Build a real catalog + projection context spanning every exploration-bearing skill.

    Exploration vectors live exclusively under ``skills_extended/`` (SkillSource
    .BUNDLED_EXTENDED), not the 3-entry ``skills/`` (SkillSource.BUNDLED) root, so both
    sources must be included or the catalog contains zero exploration-bearing skills.
    """
    from autoskillit.execution.backends.claude import ClaudeCodeBackend
    from autoskillit.workspace import SkillProjectionContext
    from autoskillit.workspace.skills import (
        DefaultSkillResolver,
        EffectiveSkillCatalog,
        SkillCatalogEntry,
    )

    source_infos = tuple(
        s
        for s in DefaultSkillResolver().list_all()
        if s.source in {SkillSource.BUNDLED, SkillSource.BUNDLED_EXTENDED}
        and s.execution_role is SkillExecutionRole.SESSION
    )
    # Filtered to MIGRATED-disposition vectors only: a skill whose exploration.yaml
    # declares vectors that are all RETAINED (e.g. "scope", which has 12 retained:
    # and no vectors: at all) never enters the `if migrated:` branch in
    # materialization.py, so it renders neither the fallback text nor the
    # eligible-path dispatch text — its preflight text is the untouched static
    # blockquote (out of scope; see the rectify plan's Step 1 placement decision).
    exploration_skill_names = {
        skill.name
        for skill in source_infos
        if any(
            vector.disposition is ExplorationVectorDisposition.MIGRATED
            for vector in skill.exploration_vectors
        )
    }
    if not exploration_skill_names:
        pytest.skip("no session-role skills with migrated exploration vectors")

    catalog = EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in source_infos),
        execution_role=SkillExecutionRole.SESSION,
    )
    context = SkillProjectionContext(
        cwd=tmp_path,
        catalog=catalog,
        backend=ClaudeCodeBackend(),
        resolved_exploration_profile=RepositoryProfileId.LANGUAGE_NEUTRAL,
        explorer_provisioning_eligible=explorer_provisioning_eligible,
    )
    return catalog, context, exploration_skill_names


def test_ineligible_context_renders_unavailable_text(tmp_path: Path) -> None:
    """When explorer_provisioning_eligible is False, projected text routes to the fallback."""
    from autoskillit.workspace import materialize_agent_skill_tree

    catalog, context, exploration_skill_names = _exploration_projection_context(
        tmp_path, explorer_provisioning_eligible=False
    )

    destination = tmp_path / "skills"
    documents = materialize_agent_skill_tree(destination, catalog, context)

    for name in sorted(exploration_skill_names):
        content = documents[name].content
        assert "autoskillit:pluginless-explorer" in content, (
            f"Expected exploration skill {name!r} to route to the pluginless-explorer "
            "fallback when eligible=False"
        )
        assert "do not dispatch this exploration vector" not in content, (
            f"Expected exploration skill {name!r} to no longer render the bare "
            "'do not dispatch' suppression text when eligible=False"
        )


def test_ineligible_context_projects_pluginless_explorer_dispatch(tmp_path: Path) -> None:
    """project_agent_skill_document names pluginless-explorer and the authorized codes."""
    from autoskillit.workspace._projected_artifact.materialization import (
        project_agent_skill_document,
    )

    _catalog, context, exploration_skill_names = _exploration_projection_context(
        tmp_path, explorer_provisioning_eligible=False
    )
    skill_info = next(entry for entry in context.skills if entry.name in exploration_skill_names)

    document = project_agent_skill_document(skill_info, context)

    assert "autoskillit:pluginless-explorer" in document.content
    for code in (
        "session_type_ineligible",
        "exploration_store_unavailable",
        "no_session_id",
        "service_not_configured",
        "snapshot_truncated",
        "store_closed",
    ):
        assert code in document.content, f"Expected authorized fallback code {code!r} in content"


def test_eligible_none_preserves_dispatch(tmp_path: Path) -> None:
    """When explorer_provisioning_eligible is None (default), dispatch is preserved."""
    from autoskillit.workspace import materialize_agent_skill_tree

    catalog, context, exploration_skill_names = _exploration_projection_context(
        tmp_path, explorer_provisioning_eligible=None
    )

    destination = tmp_path / "skills"
    documents = materialize_agent_skill_tree(destination, catalog, context)

    for name in sorted(exploration_skill_names):
        content = documents[name].content
        assert "Explorer provisioning is unavailable" not in content, (
            f"Skill {name!r} should NOT contain unavailability text when eligible=None"
        )
