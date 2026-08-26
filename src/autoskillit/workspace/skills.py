"""Skill resolution facade.

Decomposed into the following sibling shards behind this stable facade:

- ``skills_records`` — pure data types and closure BFS
- ``skills_overrides`` — project-local override discovery
- ``skills_exploration`` — exploration-sidecar + marker binding
- ``skills_visibility`` — visibility policy helpers
- ``skills_frontmatter`` — single frontmatter parse entry point

The facade re-exports every public symbol and every test-imported private
symbol the existing test suite reaches via this module. The
``DefaultSkillResolver`` class stays co-located because every shard feeds it;
splitting it would fragment the fall-through precedence (project-local →
bundled → reject) that the acceptance criteria require unchanged.
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import (
    PACK_REGISTRY,
    RETIRED_SKILL_NAMES,
    SkillContractError,
    SkillExecutionRole,
    SkillResolver,
    SkillSource,
    SkillSourceRef,
    SkillVisibilitySpec,
    pkg_root,
    validate_skill_capability_roles,
)

# Re-exports — every symbol the existing test suite reaches via this facade
# must continue to resolve. Imports create module-global bindings so
# monkeypatch.setattr on these names updates the value subsequent reads see.
from autoskillit.workspace.skills_exploration import (
    _bind_exploration_vector_markers,
    _load_exploration_sidecar,
    _parse_exploration_sidecar,
    replace_exploration_vector_bodies,
)
from autoskillit.workspace.skills_frontmatter import _skill_info_from_frontmatter
from autoskillit.workspace.skills_overrides import (
    _OVERRIDE_SEARCH_DIRS,
    ProjectLocalOverride,
    _project_skill_path,
    detect_project_local_overrides,
    override_names,
)
from autoskillit.workspace.skills_records import (
    EffectiveSkillCatalog,
    EffectiveSkillInvocation,
    SkillCatalogEntry,
    SkillExclusion,
    SkillInfo,
    SkillInvalidity,
    compute_skill_closure,
    invalidity_hints,
    logger,
    render_skill_invalidities,
)
from autoskillit.workspace.skills_visibility import (
    _skill_is_visible,
    _visibility_policy,
)

_INTERNAL_SKILLS: frozenset[str] = frozenset({"sous-chef"})

_LIST_ALL_CACHE: list[SkillInfo] | None = None
_LIST_ALL_CACHE_KEY: tuple[float, float] = (0.0, 0.0)


def _dir_mtime(path: Path) -> float:
    """Return directory mtime, or 0.0 if the path is inaccessible."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


class DefaultSkillResolver:
    """List bundled skills from both the skills/ and skills_extended/ directories."""

    def __init__(self) -> None:
        self._dir = bundled_skills_dir()
        self._extended_dir = bundled_skills_extended_dir()
        self._resolve_cache: dict[str, SkillInfo | None] = {}

    def resolve(self, name: str) -> SkillInfo | None:
        """Resolve a skill name to its path. Checks skills/ before skills_extended/."""
        if name in self._resolve_cache:
            return self._resolve_cache[name]
        for directory, source in (
            (self._dir, SkillSource.BUNDLED),
            (self._extended_dir, SkillSource.BUNDLED_EXTENDED),
        ):
            skill_path = directory / name / "SKILL.md"
            if skill_path.is_file():
                info = _skill_info_from_frontmatter(
                    name,
                    source,
                    skill_path,
                    source_ref=SkillSourceRef(
                        origin=source,
                        logical_name=name,
                        skill_path=skill_path,
                    ),
                )
                self._resolve_cache[name] = info
                return info
        self._resolve_cache[name] = None
        return None

    def list_all(self) -> list[SkillInfo]:
        """List all public bundled skills from both directories."""
        global _LIST_ALL_CACHE, _LIST_ALL_CACHE_KEY
        key = (_dir_mtime(self._dir), _dir_mtime(self._extended_dir))
        if _LIST_ALL_CACHE is not None and _LIST_ALL_CACHE_KEY == key:
            return list(_LIST_ALL_CACHE)
        bundled = _scan_directory(SkillSource.BUNDLED, self._dir)
        extended = _scan_directory(SkillSource.BUNDLED_EXTENDED, self._extended_dir)
        combined = sorted(bundled + extended, key=lambda s: s.name)
        # Structural guard: no name may appear in both directories.
        names = [s.name for s in combined]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise RuntimeError(
                f"Skill name collision across skills/ and skills_extended/: {sorted(dupes)}"
            )
        _LIST_ALL_CACHE = combined
        _LIST_ALL_CACHE_KEY = key
        return list(combined)

    def resolve_effective(self, name: str, project_root: Path | None) -> SkillInfo | None:
        """Resolve the current highest-precedence VALID source.

        Recipe-loader fall-through semantics (``recipe/io.py``): the first
        *valid* project-local candidate, in precedence order, wins. An
        invalid higher-precedence local copy is logged and skipped rather
        than shadowing a valid bundled twin or a valid lower-precedence
        local copy — it never escapes to a composition-root gate. If the
        name has no valid source anywhere, the highest-precedence invalid
        local candidate is returned so callers can still report why.
        """
        normalized_root = project_root.resolve() if project_root is not None else None
        first_invalid: SkillInfo | None = None
        if normalized_root is not None:
            for precedence, search_dir in enumerate(_OVERRIDE_SEARCH_DIRS):
                skill_path = _project_skill_path(
                    normalized_root,
                    normalized_root / search_dir,
                    name,
                )
                if skill_path is None:
                    continue
                candidate = _skill_info_from_frontmatter(
                    name,
                    SkillSource.PROJECT_LOCAL,
                    skill_path,
                    source_ref=SkillSourceRef(
                        origin=SkillSource.PROJECT_LOCAL,
                        logical_name=name,
                        skill_path=skill_path,
                        search_dir=search_dir,
                        precedence=precedence,
                    ),
                )
                if not candidate.invalidities:
                    return candidate
                logger.warning(
                    "project_local_skill_rejected",
                    skill=name,
                    path=str(skill_path),
                    reason=render_skill_invalidities(candidate.invalidities),
                    hints=invalidity_hints(candidate.invalidities),
                )
                if first_invalid is None:
                    first_invalid = candidate
        bundled = self.resolve(name)
        return bundled if bundled is not None else first_invalid

    def resolve_local_candidate(self, name: str, project_root: Path | None) -> SkillInfo | None:
        """Return the first-path-match project-local candidate, valid or not.

        Preserves ``resolve_effective``'s pre-fall-through semantics: the
        first project-local search dir containing the name wins regardless
        of validity, falling through to the bundled source only when no
        project-local directory contains the name at all. ``_llm_triage``
        is the one caller whose purpose is comparing the raw, possibly
        stale, on-disk content against a stored baseline — under
        ``resolve_effective``'s fall-through it would silently receive a
        bundled twin's substitute content instead of the file it needs to
        triage.
        """
        normalized_root = project_root.resolve() if project_root is not None else None
        if normalized_root is not None:
            for precedence, search_dir in enumerate(_OVERRIDE_SEARCH_DIRS):
                skill_path = _project_skill_path(
                    normalized_root,
                    normalized_root / search_dir,
                    name,
                )
                if skill_path is None:
                    continue
                candidate = _skill_info_from_frontmatter(
                    name,
                    SkillSource.PROJECT_LOCAL,
                    skill_path,
                    source_ref=SkillSourceRef(
                        origin=SkillSource.PROJECT_LOCAL,
                        logical_name=name,
                        skill_path=skill_path,
                        search_dir=search_dir,
                        precedence=precedence,
                    ),
                )
                if candidate.invalidities:
                    logger.warning(
                        "project_local_skill_rejected",
                        skill=name,
                        path=str(skill_path),
                        reason=render_skill_invalidities(candidate.invalidities),
                        hints=invalidity_hints(candidate.invalidities),
                    )
                return candidate
        return self.resolve(name)

    def _list_effective_unfiltered(
        self, project_root: Path | None
    ) -> tuple[tuple[SkillInfo, ...], tuple[SkillExclusion, ...]]:
        normalized_root = project_root.resolve() if project_root is not None else None
        by_name = {skill.name: skill for skill in self.list_all()}
        pending_exclusions: list[SkillInfo] = []
        if normalized_root is not None:
            selected: set[str] = set()
            for precedence, search_dir in enumerate(_OVERRIDE_SEARCH_DIRS):
                search_root = normalized_root / search_dir
                try:
                    entries = sorted(search_root.iterdir(), key=lambda entry: entry.name)
                except (FileNotFoundError, NotADirectoryError):
                    continue
                except OSError as exc:
                    raise SkillContractError(
                        f"cannot inspect project-local skill search root {search_root}: {exc}"
                    ) from exc
                for entry in entries:
                    if entry.name in selected:
                        continue
                    skill_path = _project_skill_path(
                        normalized_root,
                        search_root,
                        entry.name,
                    )
                    if skill_path is None:
                        continue
                    candidate = _skill_info_from_frontmatter(
                        entry.name,
                        SkillSource.PROJECT_LOCAL,
                        skill_path,
                        source_ref=SkillSourceRef(
                            origin=SkillSource.PROJECT_LOCAL,
                            logical_name=entry.name,
                            skill_path=skill_path,
                            search_dir=search_dir,
                            precedence=precedence,
                        ),
                    )
                    if candidate.invalidities:
                        logger.warning(
                            "project_local_skill_rejected",
                            skill=entry.name,
                            path=str(skill_path),
                            reason=render_skill_invalidities(candidate.invalidities),
                            hints=invalidity_hints(candidate.invalidities),
                        )
                        pending_exclusions.append(candidate)
                        continue
                    # Recipe-loader "seen"-on-success rule: only a valid candidate
                    # claims the name, so an invalid higher-precedence copy never
                    # clobbers a valid bundled entry or blocks a valid lower-
                    # precedence local copy from being found on the next pass.
                    selected.add(entry.name)
                    by_name[entry.name] = candidate
        exclusions = tuple(
            SkillExclusion.from_skill_info(
                candidate,
                fallback=(by_name[candidate.name].source if candidate.name in by_name else None),
            )
            for candidate in pending_exclusions
        )
        return tuple(sorted(by_name.values(), key=lambda skill: skill.name)), exclusions

    def scan_effective(
        self, project_root: Path | None
    ) -> tuple[tuple[SkillInfo, ...], tuple[SkillExclusion, ...]]:
        """Public pair-returning scan: effective skills plus excluded candidates.

        Doctor and other operator-facing tooling call this directly instead
        of reaching for the underscore-prefixed internal implementation.
        """
        normalized_root = project_root.resolve() if project_root is not None else None
        return self._list_effective_unfiltered(normalized_root)

    def list_effective(
        self,
        project_root: Path | None,
        execution_role: SkillExecutionRole,
        *,
        visibility: SkillVisibilitySpec | None = None,
        cook_session: bool = False,
        recipe_packs: frozenset[str] | None = None,
        recipe_features: frozenset[str] | None = None,
        allow_only: frozenset[str] | None = None,
    ) -> EffectiveSkillCatalog:
        """Return a fresh, immutable catalog authorized by role and visibility."""
        normalized_root = project_root.resolve() if project_root is not None else None
        effective_skills, exclusions = self._list_effective_unfiltered(normalized_root)
        # Invalid project-local candidates never reach this point — they were
        # already diverted into `exclusions` above. Any survivor here is a
        # bundled/extended packaging bug, guarded at merge time (T7).
        invalid = tuple(skill for skill in effective_skills if skill.invalidities)
        if invalid:
            details = "; ".join(
                f"{skill.name!r}: {render_skill_invalidities(skill.invalidities)}"
                for skill in invalid
            )
            raise SkillContractError(
                f"bundled skill catalog contains invalid contracts (packaging bug): {details}"
            )
        disabled, custom_tags, features, experimental_enabled = _visibility_policy(
            visibility,
            cook_session=cook_session,
            recipe_packs=recipe_packs,
            recipe_features=recipe_features,
        )
        namespace_sources = {
            skill.name: skill.source
            for skill in effective_skills
            if skill.execution_role is execution_role
            and _skill_is_visible(
                skill,
                disabled=disabled,
                custom_tags=custom_tags,
                features=features,
                experimental_enabled=experimental_enabled,
                allow_only=None,
            )
        }
        skills = tuple(
            SkillCatalogEntry.from_skill_info(skill)
            for skill in effective_skills
            if skill.execution_role is execution_role
            and _skill_is_visible(
                skill,
                disabled=disabled,
                custom_tags=custom_tags,
                features=features,
                experimental_enabled=experimental_enabled,
                allow_only=allow_only,
            )
        )
        if execution_role is SkillExecutionRole.ORCHESTRATOR:
            available_internal = tuple(
                skill
                for name in sorted(_INTERNAL_SKILLS)
                if (skill := self.resolve_effective(name, normalized_root)) is not None
                and not skill.invalidities
                and skill.execution_role is execution_role
                and _skill_is_visible(
                    skill,
                    disabled=disabled,
                    custom_tags=custom_tags,
                    features=features,
                    experimental_enabled=experimental_enabled,
                    allow_only=None,
                )
            )
            namespace_sources.update({skill.name: skill.source for skill in available_internal})
            skills = tuple(skill for skill in skills if skill.name not in _INTERNAL_SKILLS)
            internal = tuple(
                SkillCatalogEntry.from_skill_info(skill)
                for skill in available_internal
                if _skill_is_visible(
                    skill,
                    disabled=disabled,
                    custom_tags=custom_tags,
                    features=features,
                    experimental_enabled=experimental_enabled,
                    allow_only=allow_only,
                )
            )
            skills = tuple(sorted((*skills, *internal), key=lambda skill: skill.name))
        return EffectiveSkillCatalog(
            skills=skills,
            execution_role=execution_role,
            namespace_sources=namespace_sources,
            exclusions=exclusions,
        )

    def resolve_invocation(
        self,
        name: str,
        project_root: Path | None,
        execution_role: SkillExecutionRole,
        *,
        visibility: SkillVisibilitySpec | None = None,
        recipe_packs: frozenset[str] | None = None,
        recipe_features: frozenset[str] | None = None,
    ) -> EffectiveSkillInvocation:
        """Resolve and validate a root plus every direct/pack-expanded dependency."""
        normalized_root = project_root.resolve() if project_root is not None else None
        root = self.resolve_effective(name, normalized_root)
        if root is None:
            raise SkillContractError(f"skill {name!r} was not found in any effective source")

        closure: list[SkillInfo] = []
        visited: set[str] = set()
        visiting: set[str] = set()
        resolved_by_name = {root.name: root}
        pack_catalog: tuple[SkillInfo, ...] | None = None

        def resolve_member(dependency: str) -> SkillInfo | None:
            if dependency in resolved_by_name:
                return resolved_by_name[dependency]
            member = self.resolve_effective(dependency, normalized_root)
            if member is not None:
                resolved_by_name[dependency] = member
            return member

        def validate_member(skill: SkillInfo) -> None:
            if skill.invalidities:
                raise SkillContractError(
                    f"invalid contract for {skill.name!r}: "
                    f"{render_skill_invalidities(skill.invalidities)}"
                )
            if skill.execution_role is not execution_role:
                actual = (
                    skill.execution_role.value if skill.execution_role is not None else "invalid"
                )
                raise SkillContractError(
                    f"skill {skill.name!r} requires {actual} execution role; "
                    f"invocation requires {execution_role.value}"
                )
            validate_skill_capability_roles(skill.uses_capabilities, execution_role)

        def visit(skill: SkillInfo) -> None:
            nonlocal pack_catalog
            if skill.name in visited:
                return
            if skill.name in visiting:
                return
            validate_member(skill)
            visiting.add(skill.name)
            closure.append(skill)
            for dependency in skill.activate_deps:
                if dependency in PACK_REGISTRY:
                    if pack_catalog is None:
                        pack_catalog, _pack_exclusions = self._list_effective_unfiltered(
                            normalized_root
                        )
                        for candidate in pack_catalog:
                            resolved_by_name.setdefault(candidate.name, candidate)
                    members = sorted(
                        (
                            candidate
                            for candidate in pack_catalog
                            if dependency in candidate.categories
                        ),
                        key=lambda candidate: candidate.name,
                    )
                    for member in members:
                        visit(member)
                    continue
                dependency_member = resolve_member(dependency)
                if dependency_member is None:
                    raise SkillContractError(
                        f"skill {skill.name!r} has unresolved dependency {dependency!r}"
                    )
                visit(dependency_member)
            visiting.remove(skill.name)
            visited.add(skill.name)

        visit(root)
        if visibility is not None or recipe_packs is not None or recipe_features is not None:
            requested_members = frozenset(member.name for member in closure)
            disabled, custom_tags, features, experimental_enabled = _visibility_policy(
                visibility,
                cook_session=False,
                recipe_packs=recipe_packs,
                recipe_features=recipe_features,
            )
            hidden = tuple(
                member.name
                for member in closure
                if not _skill_is_visible(
                    member,
                    disabled=disabled,
                    custom_tags=custom_tags,
                    features=features,
                    experimental_enabled=experimental_enabled,
                    allow_only=requested_members,
                )
            )
            if hidden:
                raise SkillContractError(
                    "effective invocation is disabled by configured subset/pack policy: "
                    f"{sorted(hidden)}"
                )
        capability_union = frozenset().union(*(member.uses_capabilities for member in closure))
        return EffectiveSkillInvocation(
            root=root,
            closure=tuple(closure),
            capability_union=capability_union,
            project_root=normalized_root,
            execution_role=execution_role,
        )


def validate_skill_tier_roles(
    visibility: SkillVisibilitySpec,
    resolver: SkillResolver,
    project_root: Path | None,
) -> None:
    """Reject configured L1 tiers containing non-SESSION skill contracts.

    Composition roots convert config into ``SkillVisibilitySpec`` before calling.
    """
    for tier_name, configured in (
        ("tier1", visibility.tier1_skills),
        ("tier2", visibility.tier2_skills),
        ("tier3", visibility.tier3_skills),
    ):
        for skill_name in configured:
            effective = resolver.resolve_effective(skill_name, project_root)
            if effective is None:
                raise SkillContractError(
                    f"configured {tier_name} skill {skill_name!r} was not found"
                )
            if effective.invalidities:
                # Only fires now for names whose *every* implementation is
                # invalid — resolve_effective already fell through to a valid
                # bundled twin or lower-precedence local copy for every other
                # case. Enrich with path/hints when the concrete resolver
                # (the overwhelmingly common case) supplied a real SkillInfo.
                message = (
                    f"configured {tier_name} skill {skill_name!r} is invalid: "
                    f"{render_skill_invalidities(effective.invalidities)}"
                )
                if isinstance(effective, SkillInfo):
                    kinds = sorted({item.kind.value for item in effective.invalidities})
                    hints = invalidity_hints(effective.invalidities)
                    message += f" (path: {effective.path}, kind: {', '.join(kinds)})"
                    if hints:
                        message += f"; hint: {'; '.join(hints)}"
                raise SkillContractError(f"{message}; run: autoskillit doctor")
            if effective.execution_role is not SkillExecutionRole.SESSION:
                role = (
                    effective.execution_role.value
                    if effective.execution_role is not None
                    else "invalid"
                )
                raise SkillContractError(
                    f"configured {tier_name} skill {skill_name!r} requires "
                    f"{role.upper()} execution role, not SESSION"
                )


def bundled_skills_dir() -> Path:
    """Return the path to the bundled skills directory."""
    return pkg_root() / "skills"


def bundled_skills_extended_dir() -> Path:
    """Return the path to the extended bundled skills directory (Tier 2+3)."""
    return pkg_root() / "skills_extended"


def _scan_directory(source: SkillSource, directory: Path) -> list[SkillInfo]:
    """Find all SKILL.md files in immediate subdirectories."""
    if not directory.is_dir():
        return []
    result: list[SkillInfo] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in RETIRED_SKILL_NAMES:
            raise RuntimeError(
                f"Retired skill name '{entry.name}' found at {entry}. Remove this directory."
            )
        if entry.name in _INTERNAL_SKILLS:
            continue
        skill_path = entry / "SKILL.md"
        if not skill_path.is_file():
            continue
        result.append(_skill_info_from_frontmatter(entry.name, source, skill_path))
    return result


__all__ = [
    "DefaultSkillResolver",
    "EffectiveSkillCatalog",
    "EffectiveSkillInvocation",
    "ProjectLocalOverride",
    "SkillCatalogEntry",
    "SkillExclusion",
    "SkillInfo",
    "SkillInvalidity",
    "_INTERNAL_SKILLS",
    "_LIST_ALL_CACHE",
    "_LIST_ALL_CACHE_KEY",
    "_OVERRIDE_SEARCH_DIRS",
    "_bind_exploration_vector_markers",
    "_load_exploration_sidecar",
    "_parse_exploration_sidecar",
    "_project_skill_path",
    "_scan_directory",
    "bundled_skills_dir",
    "bundled_skills_extended_dir",
    "compute_skill_closure",
    "detect_project_local_overrides",
    "invalidity_hints",
    "override_names",
    "replace_exploration_vector_bodies",
    "render_skill_invalidities",
    "validate_skill_tier_roles",
]
