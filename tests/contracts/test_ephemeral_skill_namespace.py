"""Contract: ephemeral SKILL.md bodies use the correct namespace for the session context.

After init_session(), every written SKILL.md must reference cross-skills using the
namespace that matches how those skills are delivered in the session:
- BUNDLED_EXTENDED skills are delivered via --add-dir as bare /name
- BUNDLED skills are delivered via --plugin-dir as /autoskillit:name

A /autoskillit:<ref> reference in an ephemeral SKILL.md for an available
BUNDLED_EXTENDED target is wrong — the agent will not find it. Disabled or
otherwise unavailable targets are intentionally not projected as invocable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import (
    ClaudeDirectoryConventions,
    RepositoryProfileId,
    SkillExecutionRole,
    SkillSource,
    pkg_root,
)
from autoskillit.core.types import RecipeSource
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.io import load_recipe
from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
)
from autoskillit.workspace.skills import DefaultSkillResolver
from tests._tracked_recipes import tracked_recipe_paths
from tests.contracts._projection_helpers import non_exploration_catalog

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_PREFIXED_REF_RE = re.compile(r"/autoskillit:([a-z][a-z0-9-]*)")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_ephemeral_skill_md_namespace_matches_session_delivery(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    resolver = DefaultSkillResolver()
    catalog = non_exploration_catalog(
        resolver.list_effective(tmp_path, SkillExecutionRole.SESSION)
    )
    context = provider.catalog_projection_context(
        catalog, tmp_path, durable_scripts_root=pkg_root()
    )
    session_path = mgr.init_session("ns-check-session", catalog, context)

    skills_base = session_path / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
    violations: list[str] = []

    for skill_md in sorted(skills_base.glob("*/SKILL.md")):
        skill_name = skill_md.parent.name
        body = skill_md.read_text()
        for m in _PREFIXED_REF_RE.finditer(body):
            ref_name = m.group(1)
            if catalog.namespace_sources.get(ref_name) == SkillSource.BUNDLED_EXTENDED:
                line_no = body[: m.start()].count("\n") + 1
                violations.append(
                    f"{skill_name}/SKILL.md:{line_no}: /autoskillit:{ref_name} "
                    f"is BUNDLED_EXTENDED — must be /{ref_name} in ephemeral content"
                )

    assert not violations, (
        "Ephemeral SKILL.md bodies contain /autoskillit: references for BUNDLED_EXTENDED skills "
        "(delivered as bare /name via --add-dir):\n" + "\n".join(f"  - {v}" for v in violations)
    )


def test_bundled_recipe_skill_targets_resolve_and_materialize(tmp_path: Path) -> None:
    """Every static recipe skill target survives resolver and generated-home projection."""
    targets: set[str] = set()
    required_packs: set[str] = set()
    for recipe_path in sorted(
        tracked_recipe_paths(
            _PROJECT_ROOT,
            source=RecipeSource.BUILTIN,
            scan_dirs=(".",),
        )
    ):
        recipe = load_recipe(recipe_path)
        required_packs.update(recipe.requires_packs)
        for step_name, step in recipe.steps.items():
            if step.tool != "run_skill":
                continue
            skill_command = str(step.with_args.get("skill_command", ""))
            skill_name = resolve_skill_name(skill_command)
            if skill_name is None:
                assert "{" in skill_command and "}" in skill_command, (
                    f"{recipe_path.name}:{step_name} has an unparseable static skill command: "
                    f"{skill_command!r}"
                )
                continue
            targets.add(skill_name)

    assert targets
    provider = SkillsDirectoryProvider()
    resolved = {name: provider.resolver.resolve(name) for name in targets}
    unresolved = sorted(name for name, info in resolved.items() if info is None)
    assert not unresolved, f"Static bundled recipe targets missing from resolver: {unresolved}"
    add_dir_targets = frozenset(
        name
        for name, info in resolved.items()
        if info is not None and info.source is SkillSource.BUNDLED_EXTENDED
    )
    assert add_dir_targets
    manager = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path / "sessions")
    catalog = provider.resolver.list_effective(
        tmp_path,
        SkillExecutionRole.SESSION,
        cook_session=True,
        recipe_packs=frozenset(required_packs),
        allow_only=add_dir_targets,
    )
    projection_context = provider.catalog_projection_context(
        catalog,
        tmp_path,
        backend=ClaudeCodeBackend(),
        durable_scripts_root=pkg_root(),
        resolved_exploration_profile=RepositoryProfileId.AUTOSKILLIT,
    )
    assert {skill.name for skill in catalog.skills} == add_dir_targets
    generated_home = manager.init_session(
        "bundled-recipe-targets",
        catalog,
        projection_context,
    )
    skills_dir = generated_home / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
    missing = sorted(
        name for name in add_dir_targets if not (skills_dir / name / "SKILL.md").is_file()
    )
    assert not missing, f"Extended recipe targets missing from add-dir: {missing}"
