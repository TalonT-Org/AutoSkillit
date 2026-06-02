"""Suppression-delivery symmetry invariants for project-local skill search dirs."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_all_project_local_search_dirs_equals_backend_conventions_union():
    """ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS must equal the union of all backends' conventions."""
    from autoskillit.core.types._type_backend import ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS
    from autoskillit.execution.backends import BACKEND_REGISTRY

    union: set[str] = set()
    for cls in BACKEND_REGISTRY.values():
        union.update(cls().conventions.project_local_skill_search_dirs)

    assert set(ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS) == union, (
        f"ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS={set(ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS)} "
        f"does not match union of backend conventions={union}"
    )


def test_override_search_dirs_is_canonical_constant():
    """workspace/skills._OVERRIDE_SEARCH_DIRS must be the canonical constant (identity)."""
    from autoskillit.core.types._type_backend import ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS
    from autoskillit.workspace.skills import _OVERRIDE_SEARCH_DIRS

    assert _OVERRIDE_SEARCH_DIRS is ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS, (
        "_OVERRIDE_SEARCH_DIRS must be ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS (identity, not copy)"
    )
