"""Skill resolution for bundled skills."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

from autoskillit.core import (
    ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS,
    FEATURE_REGISTRY,
    PACK_REGISTRY,
    RETIRED_SKILL_NAMES,
    SKILL_CAPABILITY_REGISTRY,
    FeatureLifecycle,
    SkillContractError,
    SkillExecutionRole,
    SkillResolver,
    SkillSource,
    SkillSourceIdentity,
    SkillSourceRef,
    derive_backend_requirements,
    get_logger,
    is_feature_enabled,
    pkg_root,
    validate_skill_capability_roles,
)
from autoskillit.workspace.skill_format import (
    SkillFrontmatterParseResult,
    parse_frontmatter_content,
    read_skill_frontmatter,
)

logger = get_logger(__name__)


def _contained_project_skill_path(search_root: Path, name: str) -> Path | None:
    """Return a non-symlinked SKILL.md contained by its project search root."""
    entry = search_root / name
    skill_path = entry / "SKILL.md"
    if search_root.is_symlink() or entry.is_symlink() or skill_path.is_symlink():
        return None
    if not entry.is_dir() or not skill_path.is_file():
        return None
    try:
        resolved_root = search_root.resolve(strict=True)
        resolved_skill = skill_path.resolve(strict=True)
    except OSError:
        return None
    if not resolved_skill.is_relative_to(resolved_root):
        return None
    return skill_path


@dataclass(frozen=True, slots=True)
class SkillInfo:
    """One exact, typed skill machine contract selected from a source."""

    name: str
    source: SkillSource
    path: Path
    source_ref: SkillSourceRef | None = None
    categories: frozenset[str] = frozenset()
    uses_capabilities: frozenset[str] = frozenset()
    execution_role: SkillExecutionRole | None = SkillExecutionRole.SESSION
    activate_deps: tuple[str, ...] = ()
    canonical_content: str = ""
    canonical_digest: str = ""
    frontmatter: SkillFrontmatterParseResult | None = None
    invalid_reason: str | None = None

    def __post_init__(self) -> None:
        if self.source_ref is None:
            object.__setattr__(
                self,
                "source_ref",
                SkillSourceRef(
                    origin=self.source,
                    logical_name=self.name,
                    skill_path=self.path,
                ),
            )
        if not self.canonical_content and self.path.is_file():
            try:
                object.__setattr__(
                    self,
                    "canonical_content",
                    self.path.read_text(encoding="utf-8"),
                )
            except (OSError, UnicodeDecodeError):
                pass
        if self.canonical_content and not self.canonical_digest:
            object.__setattr__(
                self,
                "canonical_digest",
                hashlib.sha256(self.canonical_content.encode()).hexdigest(),
            )
        if self.frontmatter is None and self.canonical_content:
            object.__setattr__(
                self,
                "frontmatter",
                parse_frontmatter_content(self.canonical_content),
            )

    @property
    def canonical_bytes(self) -> bytes:
        return self.canonical_content.encode("utf-8")

    @property
    def backend_requirements(self) -> frozenset[str]:
        """Backend requirements derived solely from the declared capability set."""
        return derive_backend_requirements(self.uses_capabilities)


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    """Path-free machine contract used by role-derived downstream catalogs."""

    name: str
    source: SkillSource
    source_identity: SkillSourceIdentity
    categories: frozenset[str]
    uses_capabilities: frozenset[str]
    execution_role: SkillExecutionRole
    activate_deps: tuple[str, ...]
    canonical_content: str
    canonical_digest: str
    frontmatter: SkillFrontmatterParseResult
    invalid_reason: str | None = None

    @classmethod
    def from_skill_info(cls, skill: SkillInfo) -> SkillCatalogEntry:
        """Remove private source paths while preserving the parsed contract."""
        if skill.invalid_reason is not None:
            raise SkillContractError(
                f"invalid contract for {skill.name!r}: {skill.invalid_reason}"
            )
        if skill.execution_role is None:
            raise SkillContractError(f"skill {skill.name!r} has no valid execution role")
        if skill.source_ref is None:
            raise SkillContractError(f"skill {skill.name!r} has no effective source identity")
        if skill.frontmatter is None:
            raise SkillContractError(f"skill {skill.name!r} has no parsed frontmatter")
        return cls(
            name=skill.name,
            source=skill.source,
            source_identity=skill.source_ref.identity,
            categories=skill.categories,
            uses_capabilities=skill.uses_capabilities,
            execution_role=skill.execution_role,
            activate_deps=skill.activate_deps,
            canonical_content=skill.canonical_content,
            canonical_digest=skill.canonical_digest,
            frontmatter=skill.frontmatter,
        )

    @property
    def backend_requirements(self) -> frozenset[str]:
        """Backend requirements derived solely from the declared capability set."""
        return derive_backend_requirements(self.uses_capabilities)


@dataclass(frozen=True, slots=True)
class EffectiveSkillCatalog:
    """Immutable role-filtered view of every effective skill source."""

    skills: tuple[SkillCatalogEntry, ...]
    execution_role: SkillExecutionRole
    namespace_sources: Mapping[str, SkillSource] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "namespace_sources",
            MappingProxyType(dict(self.namespace_sources)),
        )
        for skill in self.skills:
            if skill.invalid_reason is not None:
                raise SkillContractError(
                    f"invalid catalog contract for {skill.name!r}: {skill.invalid_reason}"
                )
            if skill.execution_role is not self.execution_role:
                raise SkillContractError(
                    f"catalog role {self.execution_role.value!r} cannot contain "
                    f"{skill.execution_role.value!r} skill {skill.name!r}"
                )


@dataclass(frozen=True, slots=True)
class EffectiveSkillInvocation:
    """Validated root plus its complete executable dependency closure."""

    root: SkillInfo
    closure: tuple[SkillInfo, ...]
    capability_union: frozenset[str]
    project_root: Path | None
    execution_role: SkillExecutionRole

    def __post_init__(self) -> None:
        if self.root not in self.closure:
            raise SkillContractError("effective invocation root is absent from its closure")
        names = tuple(member.name for member in self.closure)
        if len(names) != len(set(names)):
            raise SkillContractError("effective invocation closure contains duplicate members")
        for member in self.closure:
            if member.execution_role is not self.execution_role:
                actual = (
                    member.execution_role.value if member.execution_role is not None else "invalid"
                )
                raise SkillContractError(
                    f"effective invocation role {self.execution_role.value!r} cannot contain "
                    f"{actual!r} skill {member.name!r}"
                )
            validate_skill_capability_roles(member.uses_capabilities, self.execution_role)
        expected_union = frozenset().union(*(member.uses_capabilities for member in self.closure))
        if self.capability_union != expected_union:
            raise SkillContractError(
                "effective invocation capability union does not match its closure"
            )

    @property
    def backend_requirements(self) -> frozenset[str]:
        """Derive backend constraints once from the invocation capability union."""
        return derive_backend_requirements(self.capability_union)


def _build_pack_index(skills: Iterable[SkillInfo]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for skill in skills:
        for category in skill.categories:
            index.setdefault(category, set()).add(skill.name)
    return index


def compute_skill_closure(skill_name: str, provider: Any) -> frozenset[str]:
    """Return the valid transitive dependency closure from one captured catalog."""
    skills = {skill.name: skill for skill in provider.list_skills()}
    return _compute_skill_closure(skill_name, skills)


def _compute_skill_closure(
    skill_name: str,
    skills: Mapping[str, SkillInfo],
) -> frozenset[str]:
    """Compute a closure without consulting a current metadata resolver."""
    if skill_name not in skills:
        return frozenset()
    pack_index: dict[str, set[str]] | None = None
    visited: set[str] = set()
    resolved: set[str] = set()
    queue: list[str] = [skill_name]
    while queue:
        name = queue.pop()
        if name in visited:
            continue
        visited.add(name)
        info = skills.get(name)
        if (
            info is None
            or info.invalid_reason is not None
            or info.execution_role is not SkillExecutionRole.SESSION
        ):
            continue
        resolved.add(name)
        for dependency in info.activate_deps:
            if dependency in PACK_REGISTRY:
                if pack_index is None:
                    pack_index = _build_pack_index(skills.values())
                queue.extend(
                    member for member in pack_index.get(dependency, ()) if member not in visited
                )
            elif dependency not in visited:
                queue.append(dependency)
    return frozenset(resolved)


_INTERNAL_SKILLS: frozenset[str] = frozenset({"sous-chef"})

_OVERRIDE_SEARCH_DIRS = ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS


class ProjectLocalOverride(NamedTuple):
    name: str
    search_dir: str
    skill_path: Path


def override_names(overrides: frozenset[ProjectLocalOverride]) -> frozenset[str]:
    return frozenset(o.name for o in overrides)


_LIST_ALL_CACHE: list[SkillInfo] | None = None
_LIST_ALL_CACHE_KEY: tuple[float, float] = (0.0, 0.0)


def _dir_mtime(path: Path) -> float:
    """Return directory mtime, or 0.0 if the path is inaccessible."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def detect_project_local_overrides(
    project_dir: Path,
    search_dirs: tuple[str, ...] | None = None,
) -> frozenset[ProjectLocalOverride]:
    """Return project-local skill overrides with path provenance.

    Scans all directories in `search_dirs` (or `_OVERRIDE_SEARCH_DIRS` when
    `search_dirs is None`) under `project_dir`. First-match-wins: if a skill
    name appears under multiple search dirs, only the first (by tuple order)
    is returned.
    """
    overrides: set[ProjectLocalOverride] = set()
    seen: set[str] = set()
    active = search_dirs if search_dirs is not None else _OVERRIDE_SEARCH_DIRS
    for subdir in active:
        search_root = project_dir / subdir
        if not search_root.is_dir():
            continue
        try:
            entries = list(search_root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir() and entry.name not in seen and (entry / "SKILL.md").is_file():
                seen.add(entry.name)
                overrides.add(
                    ProjectLocalOverride(
                        name=entry.name,
                        search_dir=subdir,
                        skill_path=entry / "SKILL.md",
                    )
                )
    return frozenset(overrides)


def _skill_info_from_frontmatter(
    name: str,
    source: SkillSource,
    skill_path: Path,
    *,
    source_ref: SkillSourceRef | None = None,
) -> SkillInfo:
    """Build a SkillInfo by reading all frontmatter fields in a single parse."""
    parsed = read_skill_frontmatter(skill_path)
    if not parsed.is_valid or parsed.data is None:
        return SkillInfo(
            name=name,
            source=source,
            path=skill_path,
            source_ref=source_ref,
            execution_role=None,
            canonical_content=parsed.content,
            canonical_digest=hashlib.sha256(parsed.content.encode()).hexdigest(),
            frontmatter=parsed,
            invalid_reason=f"invalid frontmatter: {parsed.error}",
        )

    data = parsed.data
    invalid_reasons: list[str] = []
    categories_raw = data.get("categories", [])
    if not isinstance(categories_raw, list):
        invalid_reasons.append("categories must be a list")
        categories_raw = []
    categories = frozenset(str(c) for c in categories_raw)

    caps_raw = data.get("uses_capabilities", [])
    if not isinstance(caps_raw, list):
        logger.warning(
            "uses_capabilities_not_a_list",
            value=caps_raw,
            skill=name,
            hint="use bracket syntax: uses_capabilities: [agent_subagent]",
        )
        invalid_reasons.append("uses_capabilities must be a list")
        caps_raw = []
    uses_capabilities = frozenset(str(c) for c in caps_raw)

    execution_role = parsed.execution_role

    activate_deps_raw = data.get("activate_deps", [])
    if not isinstance(activate_deps_raw, list):
        invalid_reasons.append("activate_deps must be a list")
        activate_deps_raw = []
    activate_deps = tuple(str(dep) for dep in activate_deps_raw)

    # These names are reserved machine-derived fields. Reading them here makes
    # attempts to inject source identity through YAML an explicit contract error.
    supplied_canonical_content = data.get("canonical_content")
    supplied_canonical_digest = data.get("canonical_digest")
    if supplied_canonical_content is not None or supplied_canonical_digest is not None:
        invalid_reasons.append("canonical content and digest are source-derived")

    unknown_caps = uses_capabilities - frozenset(SKILL_CAPABILITY_REGISTRY)
    if unknown_caps:
        logger.warning(
            "unrecognized_uses_capabilities",
            invalid=sorted(unknown_caps),
            skill=name,
            valid=sorted(SKILL_CAPABILITY_REGISTRY),
        )
    assert execution_role is not None
    try:
        validate_skill_capability_roles(uses_capabilities, execution_role)
    except SkillContractError as exc:
        invalid_reasons.append(str(exc))

    canonical_digest = hashlib.sha256(parsed.content.encode()).hexdigest()

    info = SkillInfo(
        name=name,
        source=source,
        path=skill_path,
        source_ref=source_ref,
        categories=categories,
        uses_capabilities=uses_capabilities,
        execution_role=execution_role,
        activate_deps=activate_deps,
        canonical_content=parsed.content,
        canonical_digest=canonical_digest,
        frontmatter=parsed,
        invalid_reason="; ".join(invalid_reasons) or None,
    )
    from autoskillit.workspace.skill_capabilities import (
        validate_skill_capability_authenticity,
    )

    authenticity_diagnostics = validate_skill_capability_authenticity(info)
    if authenticity_diagnostics:
        reasons = [
            reason
            for reason in (
                info.invalid_reason,
                *authenticity_diagnostics,
            )
            if reason
        ]
        info = replace(info, invalid_reason="; ".join(reasons))
    return info


def _effective_disabled_categories(
    *,
    explicit_disabled: Iterable[str],
    packs_enabled: Iterable[str],
    recipe_packs: frozenset[str] | None,
    disabled_feature_tags: frozenset[str],
) -> frozenset[str]:
    """Merge subset, pack, and feature visibility authority."""
    default_disabled = frozenset(
        tag for tag, pack_def in PACK_REGISTRY.items() if not pack_def.default_enabled
    )
    enabled_packs = frozenset(packs_enabled) | (recipe_packs or frozenset())
    return (
        frozenset(explicit_disabled) | (default_disabled - enabled_packs) | disabled_feature_tags
    )


def _skill_is_visible(
    skill: SkillInfo,
    *,
    disabled: frozenset[str],
    custom_tags: Mapping[str, Iterable[str]],
    features: dict[str, bool],
    experimental_enabled: bool,
    allow_only: frozenset[str] | None,
) -> bool:
    """Apply the established subset/pack/feature policy to one effective source."""
    if allow_only is not None and skill.name not in allow_only:
        return False
    allow_only_member = allow_only is not None and skill.name in allow_only
    feature_tool_tags = frozenset(
        tag
        for feature_name, feature_def in FEATURE_REGISTRY.items()
        for tag in feature_def.tool_tags
        if not is_feature_enabled(
            feature_name,
            features,
            experimental_enabled=experimental_enabled,
        )
    )
    for tag in disabled:
        if tag in custom_tags:
            if skill.name in custom_tags[tag]:
                return False
        elif tag in skill.categories:
            if allow_only_member and tag in feature_tool_tags:
                continue
            return False

    enabled_categories: set[str] = set()
    disabled_categories: set[str] = set()
    for feature_name, feature_def in FEATURE_REGISTRY.items():
        if is_feature_enabled(
            feature_name,
            features,
            experimental_enabled=experimental_enabled,
        ):
            enabled_categories.update(feature_def.skill_categories)
        else:
            disabled_categories.update(feature_def.skill_categories)
    gated_categories = disabled_categories - enabled_categories
    return allow_only_member or not bool(skill.categories & gated_categories)


def _visibility_policy(
    config: Any | None,
    *,
    cook_session: bool,
    recipe_packs: frozenset[str] | None,
    recipe_features: frozenset[str] | None,
) -> tuple[
    frozenset[str],
    Mapping[str, Iterable[str]],
    dict[str, bool],
    bool,
]:
    """Resolve configured visibility without importing the IL-1 config package."""
    if cook_session:
        explicit_disabled: Iterable[str] = ()
        custom_tags: Mapping[str, Iterable[str]] = {}
        features: dict[str, bool] = {
            name: True
            for name, definition in FEATURE_REGISTRY.items()
            if definition.lifecycle is not FeatureLifecycle.DISABLED
        }
        experimental_enabled = False
    elif config is None:
        explicit_disabled = ()
        custom_tags = {}
        features = {}
        experimental_enabled = False
    else:
        explicit_disabled = tuple(getattr(config.subsets, "disabled", ()))
        custom_tags = dict(getattr(config.subsets, "custom_tags", {}))
        features = dict(getattr(config, "features", {}))
        experimental_enabled = bool(getattr(config, "experimental_enabled", False))

    if recipe_features and not cook_session:
        for feature_name in recipe_features:
            if feature_name in FEATURE_REGISTRY and feature_name not in features:
                features[feature_name] = True

    disabled_feature_tags: frozenset[str] = frozenset()
    if not cook_session:
        enabled_tool_tags: set[str] = set()
        disabled_tool_tags: set[str] = set()
        for feature_name, feature_def in FEATURE_REGISTRY.items():
            if is_feature_enabled(
                feature_name,
                features,
                experimental_enabled=experimental_enabled,
            ):
                enabled_tool_tags.update(feature_def.tool_tags)
            else:
                disabled_tool_tags.update(feature_def.tool_tags)
        disabled_feature_tags = frozenset(disabled_tool_tags - enabled_tool_tags)

    packs_enabled = () if config is None else tuple(getattr(config.packs, "enabled", ()))
    disabled = _effective_disabled_categories(
        explicit_disabled=explicit_disabled,
        packs_enabled=packs_enabled,
        recipe_packs=recipe_packs,
        disabled_feature_tags=disabled_feature_tags,
    )
    return disabled, custom_tags, features, experimental_enabled


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
        """Resolve the current highest-precedence source without caching overrides."""
        normalized_root = project_root.resolve() if project_root is not None else None
        if normalized_root is not None:
            for precedence, search_dir in enumerate(_OVERRIDE_SEARCH_DIRS):
                skill_path = _contained_project_skill_path(
                    normalized_root / search_dir,
                    name,
                )
                if skill_path is None:
                    continue
                return _skill_info_from_frontmatter(
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
        return self.resolve(name)

    def _list_effective_unfiltered(self, project_root: Path | None) -> tuple[SkillInfo, ...]:
        normalized_root = project_root.resolve() if project_root is not None else None
        by_name = {skill.name: skill for skill in self.list_all()}
        if normalized_root is not None:
            selected: set[str] = set()
            for precedence, search_dir in enumerate(_OVERRIDE_SEARCH_DIRS):
                search_root = normalized_root / search_dir
                if not search_root.is_dir():
                    continue
                try:
                    entries = sorted(search_root.iterdir(), key=lambda entry: entry.name)
                except OSError:
                    continue
                for entry in entries:
                    if entry.name in selected:
                        continue
                    skill_path = _contained_project_skill_path(search_root, entry.name)
                    if skill_path is None:
                        continue
                    selected.add(entry.name)
                    by_name[entry.name] = _skill_info_from_frontmatter(
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
        return tuple(sorted(by_name.values(), key=lambda skill: skill.name))

    def list_effective(
        self,
        project_root: Path | None,
        execution_role: SkillExecutionRole,
        *,
        config: Any | None = None,
        cook_session: bool = False,
        recipe_packs: frozenset[str] | None = None,
        recipe_features: frozenset[str] | None = None,
        allow_only: frozenset[str] | None = None,
    ) -> EffectiveSkillCatalog:
        """Return a fresh, immutable catalog authorized by role and visibility."""
        normalized_root = project_root.resolve() if project_root is not None else None
        effective_skills = self._list_effective_unfiltered(normalized_root)
        invalid = tuple(skill for skill in effective_skills if skill.invalid_reason is not None)
        if invalid:
            details = "; ".join(f"{skill.name!r}: {skill.invalid_reason}" for skill in invalid)
            raise SkillContractError(
                f"effective skill catalog contains invalid contracts: {details}"
            )
        disabled, custom_tags, features, experimental_enabled = _visibility_policy(
            config,
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
                and skill.invalid_reason is None
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
        )

    def resolve_invocation(
        self,
        name: str,
        project_root: Path | None,
        execution_role: SkillExecutionRole,
        *,
        config: Any | None = None,
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
            if skill.invalid_reason is not None:
                raise SkillContractError(
                    f"invalid contract for {skill.name!r}: {skill.invalid_reason}"
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
                        pack_catalog = self._list_effective_unfiltered(normalized_root)
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
        if config is not None or recipe_packs is not None or recipe_features is not None:
            requested_members = frozenset(member.name for member in closure)
            disabled, custom_tags, features, experimental_enabled = _visibility_policy(
                config,
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
    config: Any,
    resolver: SkillResolver,
    project_root: Path | None,
) -> None:
    """Reject configured L1 tiers containing non-SESSION skill contracts.

    ``config`` is intentionally duck-typed so workspace remains independent of
    the IL-1 config package. Composition roots call this after loading config.
    """
    skills_config = getattr(config, "skills", None)
    if skills_config is None:
        return
    for tier_name in ("tier1", "tier2", "tier3"):
        configured = getattr(skills_config, tier_name, ())
        for skill_name in configured:
            effective = resolver.resolve_effective(str(skill_name), project_root)
            if effective is None:
                raise SkillContractError(
                    f"configured {tier_name} skill {skill_name!r} was not found"
                )
            if effective.invalid_reason is not None:
                raise SkillContractError(
                    f"configured {tier_name} skill {skill_name!r} is invalid: "
                    f"{effective.invalid_reason}"
                )
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
