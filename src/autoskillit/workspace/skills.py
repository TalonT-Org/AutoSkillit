"""Skill resolution for bundled skills."""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

import regex as re

from autoskillit.core import (
    ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS,
    FEATURE_REGISTRY,
    PACK_REGISTRY,
    RETIRED_SKILL_NAMES,
    SKILL_CAPABILITY_REGISTRY,
    SKILL_CONTRACT_REMEDIATIONS,
    ExplorationTaskSpec,
    ExplorationVectorApplicabilityId,
    ExplorationVectorDef,
    ExplorationVectorDisposition,
    FeatureLifecycle,
    RelationshipKind,
    RepositoryProfileId,
    SkillContractError,
    SkillExecutionRole,
    SkillInvalidityAuthority,
    SkillInvalidityKind,
    SkillResolver,
    SkillSemanticPlan,
    SkillSource,
    SkillSourceIdentity,
    SkillSourceRef,
    SkillVisibilitySpec,
    get_logger,
    is_feature_enabled,
    pkg_root,
    validate_skill_capability_roles,
)
from autoskillit.workspace.skill_format import (
    SkillFrontmatterParseResult,
    _normalize_exploration_vector_body,
    parse_frontmatter_content,
    read_skill_frontmatter,
)

logger = get_logger(__name__)


def _project_skill_path(root: Path, search: Path, name: str) -> Path | None:
    """Return a non-symlinked SKILL.md contained by its project search root."""
    entry = search / name
    skill_path = entry / "SKILL.md"
    try:
        search_root_stat = search.lstat()
        entry_stat = entry.lstat()
        skill_stat = skill_path.lstat()
        resolved_project_root = root.resolve(strict=True)
        resolved_root = search.resolve(strict=True)
        resolved_skill = skill_path.resolve(strict=True)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SkillContractError(
            f"cannot validate project-local skill {name!r} under {search}: {exc}"
        ) from exc
    if any(stat.S_ISLNK(item.st_mode) for item in (search_root_stat, entry_stat, skill_stat)):
        return None
    if not stat.S_ISDIR(entry_stat.st_mode) or not stat.S_ISREG(skill_stat.st_mode):
        return None
    if not resolved_root.is_relative_to(
        resolved_project_root
    ) or not resolved_skill.is_relative_to(resolved_root):
        return None
    return skill_path


@dataclass(frozen=True, slots=True)
class SkillInvalidity:
    """One typed reason a skill's contract failed validation."""

    kind: SkillInvalidityKind
    detail: str
    capability: str | None = None


def invalidity_hints(invalidities: Iterable[SkillInvalidity]) -> tuple[str, ...]:
    """Return one deduplicated remediation hint per distinct invalidity kind."""
    return tuple(
        SKILL_CONTRACT_REMEDIATIONS[kind].hint
        for kind in dict.fromkeys(item.kind for item in invalidities)
    )


def render_skill_invalidities(invalidities: Iterable[SkillInvalidityAuthority]) -> str:
    """Render typed skill invalidities for operator-facing diagnostics."""
    return "; ".join(item.detail for item in invalidities)


@dataclass(frozen=True, slots=True)
class SkillInfo:
    """One exact, typed skill machine contract selected from a source."""

    name: str
    source: SkillSource
    path: Path
    source_ref: SkillSourceRef | None = None
    categories: frozenset[str] = frozenset()
    uses_capabilities: frozenset[str] = frozenset()
    semantic_plan: SkillSemanticPlan | None = None
    execution_role: SkillExecutionRole | None = SkillExecutionRole.SESSION
    activate_deps: tuple[str, ...] = ()
    exploration_vectors: tuple[ExplorationVectorDef, ...] = ()
    canonical_content: str = ""
    canonical_digest: str = ""
    frontmatter: SkillFrontmatterParseResult | None = None
    invalidities: tuple[SkillInvalidity, ...] = ()

    def __post_init__(self) -> None:
        if self.source_ref is None:
            object.__setattr__(
                self, "source_ref", SkillSourceRef(self.source, self.name, self.path)
            )
        else:
            self.source_ref.validate_identity(
                self.source,
                self.name,
                self.path,
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
        if (
            self.frontmatter is not None
            and not self.frontmatter.is_valid
            and not self.invalidities
        ):
            object.__setattr__(
                self,
                "invalidities",
                (
                    SkillInvalidity(
                        SkillInvalidityKind.FRONTMATTER_PARSE,
                        f"invalid frontmatter: {self.frontmatter.error}",
                    ),
                ),
            )

    @property
    def canonical_bytes(self) -> bytes:
        return self.canonical_content.encode("utf-8")

    @property
    def source_identity(self) -> SkillSourceIdentity:
        """Return the validated effective source identity."""
        assert self.source_ref is not None
        return self.source_ref.identity


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    """Path-free machine contract used by role-derived downstream catalogs."""

    name: str
    source: SkillSource
    source_identity: SkillSourceIdentity
    categories: frozenset[str]
    uses_capabilities: frozenset[str]
    semantic_plan: SkillSemanticPlan | None
    execution_role: SkillExecutionRole
    activate_deps: tuple[str, ...]
    exploration_vectors: tuple[ExplorationVectorDef, ...]
    canonical_content: str
    canonical_digest: str
    frontmatter: SkillFrontmatterParseResult
    invalidities: tuple[SkillInvalidity, ...] = ()

    @classmethod
    def from_skill_info(cls, skill: SkillInfo) -> SkillCatalogEntry:
        """Remove private source paths while preserving the parsed contract."""
        if skill.invalidities:
            raise SkillContractError(
                f"invalid contract for {skill.name!r}: "
                f"{render_skill_invalidities(skill.invalidities)}"
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
            semantic_plan=skill.semantic_plan,
            execution_role=skill.execution_role,
            activate_deps=skill.activate_deps,
            exploration_vectors=skill.exploration_vectors,
            canonical_content=skill.canonical_content,
            canonical_digest=skill.canonical_digest,
            frontmatter=skill.frontmatter,
        )


@dataclass(frozen=True, slots=True)
class SkillExclusion:
    """One project-local skill candidate excluded from the effective catalog.

    Recorded — never silently dropped — whenever the resolution boundary
    (``resolve_effective`` / ``_list_effective_unfiltered``) skips an invalid
    project-local candidate in favor of the next valid source or, if none
    exists, an operator-visible absence.
    """

    name: str
    path: Path
    search_dir: str
    invalidities: tuple[SkillInvalidity, ...]
    fallback: SkillSource | None
    hints: tuple[str, ...]

    @classmethod
    def from_skill_info(cls, info: SkillInfo, *, fallback: SkillSource | None) -> SkillExclusion:
        """Build a record from a rejected candidate's own invalidities."""
        assert info.source_ref is not None
        return cls(
            name=info.name,
            path=info.path,
            search_dir=info.source_ref.search_dir or "",
            invalidities=info.invalidities,
            fallback=fallback,
            hints=invalidity_hints(info.invalidities),
        )


@dataclass(frozen=True, slots=True)
class EffectiveSkillCatalog:
    """Immutable role-filtered view of every effective skill source."""

    skills: tuple[SkillCatalogEntry, ...]
    execution_role: SkillExecutionRole
    namespace_sources: Mapping[str, SkillSource] = field(default_factory=dict)
    exclusions: tuple[SkillExclusion, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "namespace_sources",
            MappingProxyType(dict(self.namespace_sources)),
        )
        for skill in self.skills:
            if skill.invalidities:
                raise SkillContractError(
                    f"invalid catalog contract for {skill.name!r}: "
                    f"{render_skill_invalidities(skill.invalidities)}"
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
            if (
                member.invalidities
                or member.frontmatter is None
                or not member.frontmatter.is_valid
            ):
                reason = (
                    render_skill_invalidities(member.invalidities)
                    if member.invalidities
                    else "missing parsed frontmatter"
                )
                raise SkillContractError(
                    f"invalid effective invocation contract for {member.name!r}: {reason}"
                )
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
    def semantic_plans(self) -> tuple[SkillSemanticPlan, ...]:
        """Portable semantic plans retained by the exact invocation closure."""
        return tuple(
            member.semantic_plan for member in self.closure if member.semantic_plan is not None
        )


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
            or info.invalidities
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
_EXPLORATION_VECTOR_KEYS = frozenset(
    {
        "id",
        "disposition",
        "rationale",
        "applicability",
        "role",
        "profile",
        "relationship_classes",
        "task_id",
        "frontier_item_id",
        "depends_on",
        "scope",
        "max_results",
        "max_report_bytes",
        "evidence_version",
        "native_dispatch",
    }
)
_EXPLORATION_VECTOR_MARKER_TOKEN = "autoskillit:exploration-vector"
_EXPLORATION_VECTOR_OPEN_RE = re.compile(
    r'^<!-- autoskillit:exploration-vector id="'
    r'(?P<id>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)" -->$'
)
_EXPLORATION_VECTOR_CLOSE = "<!-- /autoskillit:exploration-vector -->"


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SkillContractError(f"exploration vector {field_name} must be a list of strings")
    return tuple(value)


def _parse_exploration_vector_frontmatter(value: object) -> tuple[ExplorationVectorDef, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SkillContractError("exploration_vectors must be a list")
    vectors: list[ExplorationVectorDef] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != _EXPLORATION_VECTOR_KEYS:
            raise SkillContractError(
                f"exploration_vectors[{index}] must contain exactly the registered keys"
            )
        try:
            for field_name in (
                "id",
                "disposition",
                "rationale",
                "applicability",
                "profile",
                "task_id",
                "frontier_item_id",
            ):
                if not isinstance(item[field_name], str):
                    raise SkillContractError(f"exploration vector {field_name} must be text")
            role = item["role"]
            if role is not None and not isinstance(role, str):
                raise SkillContractError("exploration vector role must be text or null")
            native_dispatch = item["native_dispatch"]
            if not isinstance(native_dispatch, bool):
                raise SkillContractError("exploration vector native_dispatch must be boolean")
            max_results = item["max_results"]
            max_report_bytes = item["max_report_bytes"]
            evidence_version = item["evidence_version"]
            if any(
                type(number) is not int
                for number in (max_results, max_report_bytes, evidence_version)
            ):
                raise SkillContractError("exploration vector limits and versions must be integers")
            profile = RepositoryProfileId(item["profile"])
            vector = ExplorationVectorDef(
                id=item["id"],
                disposition=ExplorationVectorDisposition(item["disposition"]),
                rationale=item["rationale"],
                applicability=ExplorationVectorApplicabilityId(item["applicability"]),
                role=role,
                profile=profile,
                relationship_classes=tuple(
                    RelationshipKind(relationship)
                    for relationship in _string_tuple(
                        item["relationship_classes"],
                        "relationship_classes",
                    )
                ),
                task=ExplorationTaskSpec(
                    task_id=item["task_id"],
                    frontier_item_id=item["frontier_item_id"],
                    profile=profile,
                    depends_on=_string_tuple(item["depends_on"], "depends_on"),
                    scope=_string_tuple(item["scope"], "scope"),
                ),
                max_results=max_results,
                max_report_bytes=max_report_bytes,
                evidence_version=evidence_version,
                native_dispatch=native_dispatch,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SkillContractError(
                f"exploration_vectors[{index}] contains an invalid closed-schema value"
            ) from exc
        vectors.append(vector)
    ids = tuple(vector.id for vector in vectors)
    if len(ids) != len(set(ids)):
        raise SkillContractError("exploration vector ids must be unique")
    return tuple(vectors)


def _bind_exploration_vector_markers(
    content: str,
    vectors: tuple[ExplorationVectorDef, ...],
) -> tuple[ExplorationVectorDef, ...]:
    declared = {vector.id: vector for vector in vectors}
    if len(declared) != len(vectors):
        raise SkillContractError("exploration vector ids must be unique")
    bodies: dict[str, str] = {}
    active_id: str | None = None
    body_lines: list[str] = []
    for raw_line in content.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        if _EXPLORATION_VECTOR_MARKER_TOKEN not in line:
            if active_id is not None:
                body_lines.append(raw_line)
            continue
        opening = _EXPLORATION_VECTOR_OPEN_RE.fullmatch(line)
        if opening is not None:
            marker_id = opening.group("id")
            if active_id is not None:
                raise SkillContractError("exploration vector markers cannot be nested")
            if marker_id not in declared:
                raise SkillContractError(f"unknown exploration vector marker {marker_id!r}")
            if marker_id in bodies:
                raise SkillContractError(f"duplicate exploration vector marker {marker_id!r}")
            active_id = marker_id
            body_lines = []
            continue
        if line == _EXPLORATION_VECTOR_CLOSE:
            if active_id is None:
                raise SkillContractError("mismatched exploration vector closing marker")
            body = _normalize_exploration_vector_body("".join(body_lines))
            if not body.strip():
                raise SkillContractError(f"exploration vector {active_id!r} has an empty body")
            bodies[active_id] = body
            active_id = None
            body_lines = []
            continue
        raise SkillContractError("malformed or embedded exploration vector marker token")
    if active_id is not None:
        raise SkillContractError(f"exploration vector {active_id!r} is missing its closing marker")
    missing = set(declared).difference(bodies)
    if missing:
        raise SkillContractError(f"missing exploration vector markers: {sorted(missing)!r}")
    return tuple(replace(vector, body=bodies[vector.id]) for vector in vectors)


def replace_exploration_vector_bodies(
    content: str,
    vectors: tuple[ExplorationVectorDef, ...],
    replacements: Mapping[str, str],
) -> str:
    """Replace exactly every migrated marker body while retaining reviewed prose."""
    bound = _bind_exploration_vector_markers(content, vectors)
    supplied = {vector.id: vector for vector in vectors}
    if any(vector.body != supplied[vector.id].body for vector in bound):
        raise SkillContractError(
            "exploration vector body differs from its canonical parsed authority"
        )
    expected = {
        vector.id
        for vector in bound
        if vector.disposition is ExplorationVectorDisposition.MIGRATED
    }
    if set(replacements) != expected:
        raise SkillContractError(
            "exploration vector replacements must exactly match migrated marker ids"
        )
    normalized: dict[str, str] = {}
    for marker_id, replacement_body in replacements.items():
        if not isinstance(replacement_body, str) or not replacement_body.strip():
            raise SkillContractError(f"replacement for exploration vector {marker_id!r} is empty")
        replacement_body = _normalize_exploration_vector_body(replacement_body)
        if _EXPLORATION_VECTOR_MARKER_TOKEN in replacement_body:
            raise SkillContractError("exploration vector replacement contains a marker token")
        normalized[marker_id] = replacement_body

    output: list[str] = []
    active_id: str | None = None
    for raw_line in content.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        opening = _EXPLORATION_VECTOR_OPEN_RE.fullmatch(line)
        if opening is not None:
            active_id = opening.group("id")
            output.append(raw_line)
            if active_id in normalized:
                output.append(normalized[active_id] + "\n")
            continue
        if line == _EXPLORATION_VECTOR_CLOSE:
            output.append(raw_line)
            active_id = None
            continue
        if active_id not in normalized:
            output.append(raw_line)
    return "".join(output)


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
            invalidities=(
                SkillInvalidity(
                    SkillInvalidityKind.FRONTMATTER_PARSE,
                    f"invalid frontmatter: {parsed.error}",
                ),
            ),
        )

    data = parsed.data
    invalidities: list[SkillInvalidity] = []
    categories_raw = data.get("categories", [])
    if not isinstance(categories_raw, list):
        invalidities.append(
            SkillInvalidity(SkillInvalidityKind.FIELD_SHAPE, "categories must be a list")
        )
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
        invalidities.append(
            SkillInvalidity(SkillInvalidityKind.FIELD_SHAPE, "uses_capabilities must be a list")
        )
        caps_raw = []
    uses_capabilities = frozenset(str(c) for c in caps_raw)

    from autoskillit.workspace.skill_capabilities import parse_skill_semantic_plan

    semantic_plan, semantic_diagnostics = parse_skill_semantic_plan(
        data,
        path=skill_path,
        content=parsed.content,
        uses_capabilities=uses_capabilities,
    )
    invalidities.extend(SkillInvalidity(kind, detail) for kind, detail in semantic_diagnostics)

    execution_role = parsed.execution_role

    activate_deps_raw = data.get("activate_deps", [])
    if not isinstance(activate_deps_raw, list):
        invalidities.append(
            SkillInvalidity(SkillInvalidityKind.FIELD_SHAPE, "activate_deps must be a list")
        )
        activate_deps_raw = []
    activate_deps = tuple(str(dep) for dep in activate_deps_raw)

    exploration_vectors: tuple[ExplorationVectorDef, ...] = ()
    try:
        exploration_vectors = _bind_exploration_vector_markers(
            parsed.content,
            _parse_exploration_vector_frontmatter(data.get("exploration_vectors")),
        )
    except SkillContractError as exc:
        invalid_reasons.append(str(exc))

    # These names are reserved machine-derived fields. Reading them here makes
    # attempts to inject source identity through YAML an explicit contract error.
    supplied_canonical_content = data.get("canonical_content")
    supplied_canonical_digest = data.get("canonical_digest")
    if supplied_canonical_content is not None or supplied_canonical_digest is not None:
        invalidities.append(
            SkillInvalidity(
                SkillInvalidityKind.RESERVED_FIELD,
                "canonical content and digest are source-derived",
            )
        )

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
        invalidities.append(SkillInvalidity(SkillInvalidityKind.UNKNOWN_CAPABILITY, str(exc)))

    canonical_digest = hashlib.sha256(parsed.content.encode()).hexdigest()

    info = SkillInfo(
        name=name,
        source=source,
        path=skill_path,
        source_ref=source_ref,
        categories=categories,
        uses_capabilities=uses_capabilities,
        semantic_plan=semantic_plan,
        execution_role=execution_role,
        activate_deps=activate_deps,
        exploration_vectors=exploration_vectors,
        canonical_content=parsed.content,
        canonical_digest=canonical_digest,
        frontmatter=parsed,
        invalidities=tuple(invalidities),
    )
    from autoskillit.workspace.skill_capabilities import (
        validate_skill_capability_authenticity,
    )

    authenticity_diagnostics = validate_skill_capability_authenticity(info)
    if authenticity_diagnostics:
        info = replace(
            info,
            invalidities=info.invalidities
            + tuple(
                SkillInvalidity(
                    diagnostic.kind,
                    diagnostic.detail,
                    capability=diagnostic.capability,
                )
                for diagnostic in authenticity_diagnostics
            ),
        )
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
    visibility: SkillVisibilitySpec | None,
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
    """Resolve effective visibility from the core-owned policy contract."""
    if cook_session:
        explicit_disabled: Iterable[str] = ()
        custom_tags: Mapping[str, Iterable[str]] = {}
        features: dict[str, bool] = {
            name: True
            for name, definition in FEATURE_REGISTRY.items()
            if definition.lifecycle is not FeatureLifecycle.DISABLED
        }
        experimental_enabled = False
    elif visibility is None:
        explicit_disabled = ()
        custom_tags = {}
        features = {}
        experimental_enabled = False
    else:
        explicit_disabled = visibility.disabled_categories
        custom_tags = visibility.custom_tags
        features = dict(visibility.features)
        experimental_enabled = visibility.experimental_enabled

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

    packs_enabled = () if visibility is None else visibility.enabled_packs
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
