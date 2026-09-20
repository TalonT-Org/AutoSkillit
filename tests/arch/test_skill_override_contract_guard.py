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
from autoskillit.workspace.skills._format import read_skill_frontmatter
from tests._git_inventory import git_ls_files

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_SKILL_ROOTS = (pkg_root() / "skills", pkg_root() / "skills_extended")
_EXPECTED_TRACKED_SHADOW_PAIR_COUNT = 12
_EXPECTED_TRACKED_FLOOR_EXCLUSIONS = {
    ".claude/skills/audit-arch/SKILL.md": (
        "project-local override weakens bundled semantic requirements: "
        "evidence.required, evidence.independent"
    ),
    ".claude/skills/audit-bugs/SKILL.md": (
        "project-local override weakens bundled semantic requirements: "
        "concurrency.required, evidence.required, evidence.independent"
    ),
    ".claude/skills/audit-cohesion/SKILL.md": (
        "project-local override weakens bundled semantic requirements: "
        "concurrency.required, evidence.required, evidence.independent"
    ),
    ".claude/skills/audit-defense-standards/SKILL.md": (
        "project-local override weakens bundled semantic requirements: "
        "concurrency.required, evidence.required, evidence.independent"
    ),
    ".claude/skills/audit-tests/SKILL.md": (
        "project-local override weakens bundled semantic requirements: "
        "concurrency.required, evidence.required, evidence.independent"
    ),
    ".claude/skills/design-guards/SKILL.md": (
        "project-local override weakens bundled semantic requirements: "
        "concurrency.required, evidence.required, evidence.independent"
    ),
    ".claude/skills/elaborate-phase/SKILL.md": (
        "project-local override weakens bundled semantic requirements: "
        "concurrency.required, evidence.required, evidence.independent"
    ),
    ".claude/skills/make-arch-diag/SKILL.md": (
        "project-local override weakens bundled semantic requirements: semantic_requirements"
    ),
    ".claude/skills/make-req/SKILL.md": (
        "project-local override weakens bundled semantic requirements: "
        "concurrency.required, evidence.required, evidence.independent"
    ),
    ".claude/skills/verify-diag/SKILL.md": (
        "project-local override weakens bundled semantic requirements: "
        "concurrency.required, evidence.required, evidence.independent"
    ),
}
_EXPECTED_TRACKED_FLOOR_NAMES = frozenset(
    Path(path).parent.name for path in _EXPECTED_TRACKED_FLOOR_EXCLUSIONS
)


def _bundled_skill_paths() -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for root in _BUNDLED_SKILL_ROOTS:
        for path in root.glob("*/SKILL.md"):
            paths[path.parent.name] = path
    return paths


def _tracked_project_local_skill_paths() -> tuple[Path, ...]:
    return tuple(
        _REPOSITORY_ROOT / relative_path
        for relative_path in git_ls_files(_REPOSITORY_ROOT, *ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS)
        if Path(relative_path).name == "SKILL.md"
    )


def _on_disk_project_local_skill_paths() -> tuple[Path, ...]:
    tracked = set(_tracked_project_local_skill_paths())
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


def _requires_join(path: Path) -> bool:
    parsed = read_skill_frontmatter(path)
    requirements = parsed.data.get("semantic_requirements") if parsed.data else None
    if not isinstance(requirements, dict):
        return False
    join = requirements.get("join")
    return isinstance(join, dict) and join.get("required") is True


def _ignore_provenance(path: Path) -> str:
    result = subprocess.run(
        ["git", "check-ignore", "-v", "--", str(path.relative_to(_REPOSITORY_ROOT))],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "not ignored"


_TRACKED_SHADOW_PAIRS = _shadow_pairs(_tracked_project_local_skill_paths())
_ON_DISK_SHADOW_PAIRS = _shadow_pairs(_on_disk_project_local_skill_paths())
_REQUIRED_JOIN_SKILLS = frozenset(
    name
    for name, _local_path, bundled_path in _ON_DISK_SHADOW_PAIRS
    if _requires_join(bundled_path)
)


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
    assert len(_TRACKED_SHADOW_PAIRS) == _EXPECTED_TRACKED_SHADOW_PAIR_COUNT


@pytest.mark.parametrize(
    "execution_role", (SkillExecutionRole.SESSION, SkillExecutionRole.ORCHESTRATOR)
)
def test_repository_root_reports_only_expected_contract_floor_exclusions(execution_role) -> None:
    """Tracked project shadows are explicit exclusions and no other override weakens a floor."""
    catalog = DefaultSkillResolver().list_effective(
        _REPOSITORY_ROOT,
        execution_role,
        cook_session=True,
    )
    details = _floor_exclusion_details(catalog)

    assert details == {
        path: (detail,) for path, detail in _EXPECTED_TRACKED_FLOOR_EXCLUSIONS.items()
    }, "Unexpected project-local contract-floor exclusions:\n" + _floor_exclusion_failure_details(
        details
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
        if name in _EXPECTED_TRACKED_FLOOR_NAMES and local.source is not bundled.source:
            failures.append(f"{name}: expected bundled fallback after contract-floor exclusion")
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
