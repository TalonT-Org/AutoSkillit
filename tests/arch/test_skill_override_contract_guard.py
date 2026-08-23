"""Project-local skill overrides must not weaken bundled semantic contracts."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.core import SkillSemanticOperation, SkillSource
from autoskillit.core.paths import pkg_root
from autoskillit.core.types._type_backend import ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS
from autoskillit.execution.backends import CodexBackend
from autoskillit.workspace.skill_format import read_skill_frontmatter
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_SKILL_ROOTS = (pkg_root() / "skills", pkg_root() / "skills_extended")
_EXPECTED_SHADOW_PAIR_COUNT = 12
_REQUIRED_JOIN_SKILLS = frozenset(
    {
        "audit-arch",
        "audit-bugs",
        "audit-cohesion",
        "audit-defense-standards",
        "audit-tests",
        "design-guards",
        "elaborate-phase",
        "make-req",
        "verify-diag",
    }
)


def _tracked_project_local_skill_paths() -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "ls-files", "--", *ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS],
        cwd=_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(
        _REPOSITORY_ROOT / relative_path
        for relative_path in result.stdout.splitlines()
        if Path(relative_path).name == "SKILL.md"
    )


def _bundled_skill_paths() -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for root in _BUNDLED_SKILL_ROOTS:
        for path in root.glob("*/SKILL.md"):
            paths[path.parent.name] = path
    return paths


def _shadow_pairs() -> tuple[tuple[str, Path, Path], ...]:
    bundled_paths = _bundled_skill_paths()
    return tuple(
        (local_path.parent.name, local_path, bundled_paths[local_path.parent.name])
        for local_path in _tracked_project_local_skill_paths()
        if local_path.parent.name in bundled_paths
    )


def _requires_join(data: dict[str, object]) -> bool:
    requirements = data.get("semantic_requirements")
    if not isinstance(requirements, dict):
        return False
    join = requirements.get("join")
    return isinstance(join, dict) and join.get("required") is True


def test_tracked_override_shadow_pair_inventory_is_reviewed() -> None:
    """A new tracked shadow pair must consciously update this guard's inventory."""
    assert len(_shadow_pairs()) == _EXPECTED_SHADOW_PAIR_COUNT


def test_project_local_overrides_preserve_bundled_semantic_contracts() -> None:
    """Same-name project-local overrides cannot lower bundled semantic requirements."""
    failures: list[str] = []
    for _name, local_path, bundled_path in _shadow_pairs():
        local = read_skill_frontmatter(local_path)
        bundled = read_skill_frontmatter(bundled_path)
        if local.data is None:
            failures.append(f"{local_path}: invalid local frontmatter ({local.error})")
            continue
        if bundled.data is None:
            failures.append(f"{bundled_path}: invalid bundled frontmatter ({bundled.error})")
            continue

        if _requires_join(bundled.data) and not _requires_join(local.data):
            failures.append(
                f"{local_path}: must retain semantic_requirements.join.required: true "
                f"because bundled {bundled_path} requires a fixed-set join"
            )

    assert not failures, "Project-local overrides weaken bundled contracts:\n" + "\n".join(
        f"- {failure}" for failure in failures
    )


def test_required_join_overrides_resolve_and_codex_refuses_them() -> None:
    """Repository-local required-join overrides remain refused by Codex admission."""
    bundled_required_join_skills = frozenset(
        name
        for name, _local_path, bundled_path in _shadow_pairs()
        if (parsed := read_skill_frontmatter(bundled_path)).data is not None
        and _requires_join(parsed.data)
    )
    assert _REQUIRED_JOIN_SKILLS <= bundled_required_join_skills

    resolver = DefaultSkillResolver()
    codex = CodexBackend()
    failures: list[str] = []

    for name in sorted(_REQUIRED_JOIN_SKILLS):
        resolved = resolver.resolve_effective(name, _REPOSITORY_ROOT)
        if resolved is None:
            failures.append(f"{name}: resolver returned no skill")
            continue
        if resolved.source is not SkillSource.PROJECT_LOCAL:
            failures.append(
                f"{name}: resolver selected {resolved.source.value}, not project_local"
            )
            continue
        if resolved.semantic_plan is None:
            failures.append(f"{name}: resolved override has no semantic plan")
            continue

        adaptation = codex.adapt_skill_semantics(resolved.semantic_plan)
        if adaptation.unsupported_operation is not SkillSemanticOperation.REQUIRED_JOIN:
            failures.append(
                f"{name}: Codex admitted required join ({adaptation.unsupported_operation!r})"
            )

    assert not failures, (
        "Repository-local required-join overrides lost Codex refusal:\n"
        + "\n".join(f"- {failure}" for failure in failures)
    )
