"""Project-local override search-directory invariants."""

from __future__ import annotations

import pytest

from autoskillit.core import ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS, SkillSource

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


@pytest.mark.parametrize(
    "search_dir",
    ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS,
)
def test_resolver_observes_every_project_local_search_dir(tmp_path, search_dir):
    """The resolver uses every canonical project-local search directory."""
    from autoskillit.workspace.skills import DefaultSkillResolver

    skill_path = tmp_path / search_dir / "resolver-search-dir" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\n"
        "name: resolver-search-dir\n"
        "description: Resolver search directory fixture.\n"
        "uses_capabilities: [test_check]\n"
        "execution_role: session\n"
        "---\n"
        "Call `test_check()`.\n"
    )

    resolved = DefaultSkillResolver().resolve_effective("resolver-search-dir", tmp_path)

    assert resolved is not None
    assert resolved.source is SkillSource.PROJECT_LOCAL
    assert resolved.path == skill_path


def test_override_search_dirs_is_canonical_constant():
    """Override search-dir constant must equal its canonical source by identity."""
    from autoskillit.core.types._type_backend import ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS
    from autoskillit.workspace.skills._overrides import _OVERRIDE_SEARCH_DIRS

    assert _OVERRIDE_SEARCH_DIRS is ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS, (
        "_OVERRIDE_SEARCH_DIRS must be ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS (identity, not copy)"
    )
