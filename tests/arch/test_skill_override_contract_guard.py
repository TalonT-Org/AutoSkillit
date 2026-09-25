"""Project-local overrides must retain the bundled admission contract."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.core import SkillExecutionRole, SkillInvalidityKind
from autoskillit.core.paths import pkg_root
from autoskillit.core.types._type_backend import ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS
from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.workspace import compile_session_skill_catalog
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
)
from tests._tracked_skills import tracked_project_local_skill_paths

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_SKILL_ROOTS = (pkg_root() / "skills", pkg_root() / "skills_extended")


def _bundled_skill_paths() -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for root in _BUNDLED_SKILL_ROOTS:
        for path in root.glob("*/SKILL.md"):
            paths[path.parent.name] = path
    return paths


def _on_disk_project_local_skill_paths() -> tuple[Path, ...]:
    tracked = set(tracked_project_local_skill_paths(_REPOSITORY_ROOT))
    return tuple(
        path
        for search_dir in ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS
        for path in sorted((_REPOSITORY_ROOT / search_dir).glob("*/SKILL.md"))
        if path not in tracked
    )


def _shadow_pairs(
    local_paths: tuple[Path, ...],
) -> tuple[tuple[str, Path, Path], ...]:
    bundled_paths = _bundled_skill_paths()
    return tuple(
        (local_path.parent.name, local_path, bundled_paths[local_path.parent.name])
        for local_path in local_paths
        if local_path.parent.name in bundled_paths
    )


def _ignore_provenance(path: Path) -> str:
    result = subprocess.run(
        ["git", "check-ignore", "-v", "--", str(path.relative_to(_REPOSITORY_ROOT))],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "not ignored"


_TRACKED_SHADOW_PAIRS = _shadow_pairs(tracked_project_local_skill_paths(_REPOSITORY_ROOT))
_ON_DISK_SHADOW_PAIRS = _shadow_pairs(_on_disk_project_local_skill_paths())


def _catalog_for(skill) -> EffectiveSkillCatalog:
    assert skill.execution_role is not None
    return EffectiveSkillCatalog(
        skills=(SkillCatalogEntry.from_skill_info(skill),),
        execution_role=skill.execution_role,
    )


def _admission(backend, skill) -> tuple[bool, tuple[str, ...]]:
    compiled = compile_session_skill_catalog(_catalog_for(skill), backend)
    return (
        bool(compiled.catalog.skills),
        tuple(item.operation.value for item in compiled.unavailable),
    )


def _floor_exclusion_details(catalog) -> dict[str, tuple[str, ...]]:
    return {
        exclusion.path.relative_to(_REPOSITORY_ROOT).as_posix(): tuple(
            invalidity.detail
            for invalidity in exclusion.invalidities
            if invalidity.kind is SkillInvalidityKind.CONTRACT_FLOOR_WEAKENED
        )
        for exclusion in catalog.exclusions
        if any(
            invalidity.kind is SkillInvalidityKind.CONTRACT_FLOOR_WEAKENED
            for invalidity in exclusion.invalidities
        )
    }


def _floor_exclusion_failure_details(details: dict[str, tuple[str, ...]]) -> str:
    return "\n".join(
        f"- {path}: {detail}; {_ignore_provenance(_REPOSITORY_ROOT / path)}"
        for path, detail in sorted(details.items())
    )


def test_tracked_override_shadow_pair_inventory_is_reviewed() -> None:
    """A new tracked shadow pair must consciously update this guard's inventory."""
    assert {name for name, _local_path, _bundled_path in _TRACKED_SHADOW_PAIRS} == frozenset(
        {"audit-arch", "audit-cohesion", "audit-tests", "promote-to-main", "validate-audit"}
    )


@pytest.mark.parametrize(
    "execution_role", (SkillExecutionRole.SESSION, SkillExecutionRole.ORCHESTRATOR)
)
def test_repository_root_reports_no_tracked_contract_floor_exclusions(execution_role) -> None:
    """No tracked project-local override weakens its bundled twin's contract floor."""
    catalog = DefaultSkillResolver().list_effective(
        _REPOSITORY_ROOT,
        execution_role,
        cook_session=True,
    )
    tracked_rel_paths = {
        path.relative_to(_REPOSITORY_ROOT).as_posix()
        for path in tracked_project_local_skill_paths(_REPOSITORY_ROOT)
    }
    details = {
        path: detail
        for path, detail in _floor_exclusion_details(catalog).items()
        if path in tracked_rel_paths
    }

    assert details == {}, (
        "Tracked project-local contract-floor exclusions:\n"
        + _floor_exclusion_failure_details(details)
    )


def test_project_local_override_admission_matches_bundled_twin_on_every_backend() -> None:
    """Resolution cannot silently admit a local shadow differently from its bundled twin."""
    resolver = DefaultSkillResolver()
    failures: list[str] = []
    pairs = (*_TRACKED_SHADOW_PAIRS, *_ON_DISK_SHADOW_PAIRS)
    for name in sorted({name for name, _local_path, _bundled_path in pairs}):
        local = resolver.resolve_effective(name, _REPOSITORY_ROOT)
        bundled = resolver.resolve(name)
        if local is None or bundled is None:
            failures.append(
                f"{name}: could not resolve both project-local and bundled definitions"
            )
            continue
        for backend_name, backend_type in BACKEND_REGISTRY.items():
            backend = backend_type()
            if _admission(backend, local) != _admission(backend, bundled):
                failures.append(
                    f"{name}: project-local admission differs from bundled on {backend_name}"
                )

    assert not failures, "Project-local overrides change backend admission:\n" + "\n".join(
        f"- {failure}" for failure in failures
    )
