"""Per-session ephemeral skill directory management.

Provides three components:
  - resolve_ephemeral_root(): platform-aware writable dir discovery
  - SkillsDirectoryProvider: tier-aware skill content provider
  - DefaultSessionSkillManager: manages per-session ephemeral skill directories
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    ClaudeDirectoryConventions,
    SkillContractError,
    SkillExecutionRole,
    SkillResolver,
    SkillSource,
    SkillSourceRef,
    ValidatedAddDir,
    get_logger,
    pkg_root,
)
from autoskillit.workspace.skill_format import (
    SkillFrontmatterParseResult,
)
from autoskillit.workspace.skill_projection import (
    SkillProjectionContext,
    materialize_agent_skill_tree,
    project_agent_skill_document,
)
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    EffectiveSkillInvocation,
    SkillCatalogEntry,
    SkillInfo,
    _skill_info_from_frontmatter,
)
from autoskillit.workspace.skills import (
    compute_skill_closure as compute_skill_closure,
)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend

# Candidate ephemeral roots, tried in order.
# resolve_ephemeral_root() appends tempfile.gettempdir() as the final fallback.
_CANDIDATE_ROOTS: list[Path] = [
    Path("/dev/shm"),
    Path("/tmp"),
]

logger = get_logger(__name__)


def _codex_profile_skill_infos(
    backend: CodingAgentBackend,
) -> tuple[SkillInfo, ...]:
    profile_skills_root = Path.home() / ".codex" / "skills"
    if not profile_skills_root.is_dir():
        return ()
    result: list[SkillInfo] = []
    for entry in sorted(profile_skills_root.iterdir(), key=lambda item: item.name):
        skill_md = entry / "SKILL.md"
        if (
            entry.is_symlink()
            or skill_md.is_symlink()
            or not entry.is_dir()
            or not skill_md.is_file()
        ):
            continue
        info = _skill_info_from_frontmatter(
            entry.name,
            SkillSource.THIRD_PARTY,
            skill_md,
            source_ref=SkillSourceRef(
                origin=SkillSource.THIRD_PARTY,
                logical_name=entry.name,
                skill_path=skill_md,
                search_dir=str(profile_skills_root),
            ),
        )
        if (
            info.invalid_reason is not None
            or info.execution_role is not SkillExecutionRole.SESSION
        ):
            logger.warning(
                "codex_profile_skill_contract_rejected",
                skill=entry.name,
                reason=info.invalid_reason or "non-session execution role",
            )
            continue
        if info.backend_requirements and backend.name not in info.backend_requirements:
            logger.debug(
                "codex_profile_skill_backend_skip",
                skill=entry.name,
                backend=backend.name,
            )
            continue
        result.append(info)
    return tuple(result)


def _materialize_codex_profile_skill_infos(
    session_dir: Path,
    backend: CodingAgentBackend,
) -> tuple[SkillInfo, ...]:
    profile_skills_root = Path.home() / ".codex" / "skills"
    if not profile_skills_root.is_dir():
        return ()
    infos = _codex_profile_skill_infos(backend)
    catalog = EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(info) for info in infos),
        execution_role=SkillExecutionRole.SESSION,
    )
    materialize_agent_skill_tree(
        session_dir / backend.conventions.skills_subdir,
        catalog,
        SkillProjectionContext(
            execution_cwd=Path.cwd().resolve(),
            catalog=catalog,
            backend=backend,
            conventions=backend.conventions,
        ),
    )
    return infos


def materialize_codex_profile_skills(
    session_dir: Path,
    backend: CodingAgentBackend,
) -> int:
    """Project profile skills into a Codex session without exposing machine fields."""
    return len(_materialize_codex_profile_skill_infos(session_dir, backend))


def resolve_ephemeral_root() -> Path:
    """Return a writable ephemeral root directory for session skill dirs.

    Tries /dev/shm/autoskillit-sessions (Linux tmpfs), then
    /tmp/autoskillit-sessions, then tempfile.gettempdir().
    Creates the chosen directory if it does not exist.
    """
    candidates = _CANDIDATE_ROOTS + [Path(tempfile.gettempdir())]
    for base in candidates:
        target = base / "autoskillit-sessions"
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".write_probe"
            probe.touch()
            probe.unlink()
            return target
        except (OSError, PermissionError):
            continue
    raise RuntimeError("No writable ephemeral root found for session skill dirs")


def _parse_write_paths(parsed: SkillFrontmatterParseResult) -> list[str]:
    """Extract write paths from the contract's single frontmatter parse."""
    if not parsed.is_valid or parsed.data is None:
        return []
    raw = parsed.data.get("write_paths", [])
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if p and isinstance(p, str)]


def collect_closure_write_paths(
    closure: frozenset[str],
    resolver: SkillResolver,
) -> tuple[str, ...]:
    """Collect write_paths from all skills in a pre-computed closure.

    Returns a deduplicated tuple of raw template paths (may contain
    ``{{AUTOSKILLIT_TEMP}}``). Unresolvable or unreadable skills are
    silently skipped.
    """
    paths: list[str] = []
    seen: set[str] = set()
    for name in sorted(closure):
        info = resolver.resolve(name)
        if info is None:
            continue
        if (
            info.invalid_reason is not None
            or info.execution_role is not SkillExecutionRole.SESSION
        ):
            continue
        if info.frontmatter is None:
            continue
        for wp in _parse_write_paths(info.frontmatter):
            if wp not in seen:
                seen.add(wp)
                paths.append(wp)
    return tuple(paths)


def resolve_closure_write_dirs(
    closure: tuple[SkillInfo, ...],
    cwd: str,
    existing: list[Path] | None = None,
) -> list[Path]:
    """Resolve write_paths from an exact effective closure into absolute Paths.

    Substitutes ``{{AUTOSKILLIT_TEMP}}`` with ``cwd/.autoskillit/temp`` and
    returns deduplicated resolved Paths ready to extend ``write_watch_dirs``.
    Paths already present in ``existing`` are excluded from the result.
    """
    raw_paths = tuple(
        write_path
        for info in closure
        if info.frontmatter is not None
        for write_path in _parse_write_paths(info.frontmatter)
    )
    if not raw_paths:
        return []
    temp_prefix = os.path.join(cwd, ".autoskillit", "temp")
    seen: set[Path] = set(existing) if existing else set()
    result: list[Path] = []
    for rwp in raw_paths:
        resolved = Path(rwp.replace("{{AUTOSKILLIT_TEMP}}", temp_prefix))
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


class SkillsDirectoryProvider:
    """Provides bundled skill content with tier-aware frontmatter injection."""

    def __init__(
        self,
        temp_dir_relpath: str = ".autoskillit/temp",
        default_base_branch: str = "main",
    ) -> None:
        if "\n" in temp_dir_relpath or ": " in temp_dir_relpath:
            raise ValueError(f"temp_dir_relpath is YAML-unsafe: {temp_dir_relpath!r}")
        if "\n" in default_base_branch or ": " in default_base_branch:
            raise ValueError(f"default_base_branch is YAML-unsafe: {default_base_branch!r}")
        self._resolver = DefaultSkillResolver()
        self._temp_dir_relpath = temp_dir_relpath
        self._default_base_branch = default_base_branch

    @property
    def resolver(self) -> SkillResolver:
        """Expose the underlying SkillResolver for target skill resolution."""
        return self._resolver

    def list_skills(self) -> list[SkillInfo]:
        """List all public bundled skills."""
        return self._resolver.list_all()

    def get_skill_content(self, name: str, *, gated: bool = True) -> str:
        """Return SKILL.md content with gating frontmatter injected when required.

        - gated=True  → ensure disable-model-invocation: true is present
          (used only by the activate path — init_session omits gated skills entirely)
        - gated=False → return unmodified content (cook session or Tier 1 skills)

        Substitutes ``{{AUTOSKILLIT_TEMP}}`` with the configured temp dir relpath.
        Tier 1 skills (which contain no placeholder) are unaffected.
        """
        skill_info = self._resolver.resolve(name)
        if skill_info is None:
            raise FileNotFoundError(f"Skill not found: {name}")
        return self.project_skill_info(
            skill_info,
            execution_cwd=Path.cwd(),
            gating=True if gated else None,
        )

    def projection_context(
        self,
        skill_info: SkillInfo,
        execution_cwd: Path,
        *,
        gating: bool | None = None,
        backend: CodingAgentBackend | None = None,
    ) -> SkillProjectionContext:
        """Build the shared execution-local projection context."""
        catalog = EffectiveSkillCatalog(
            skills=(SkillCatalogEntry.from_skill_info(skill_info),),
            execution_role=skill_info.execution_role or SkillExecutionRole.SESSION,
        )
        return self.catalog_projection_context(
            catalog,
            execution_cwd,
            gating=gating,
            backend=backend,
        )

    def catalog_projection_context(
        self,
        catalog: EffectiveSkillCatalog,
        execution_cwd: Path,
        *,
        gating: bool | None = None,
        backend: CodingAgentBackend | None = None,
    ) -> SkillProjectionContext:
        """Build one projection context bound to a resolved path-free catalog."""
        return SkillProjectionContext(
            execution_cwd=execution_cwd,
            catalog=catalog,
            backend=backend,
            conventions=backend.conventions if backend is not None else None,
            substitutions={
                "{{AUTOSKILLIT_TEMP}}": self._temp_dir_relpath,
                "{{AUTOSKILLIT_SCRIPTS}}": str(pkg_root() / "recipes" / "scripts"),
                "{{DEFAULT_BASE_BRANCH}}": self._default_base_branch,
            },
            gating=gating,
        )

    def project_skill_info(
        self,
        skill_info: SkillInfo,
        *,
        execution_cwd: Path,
        gating: bool | None = None,
        backend: CodingAgentBackend | None = None,
    ) -> str:
        """Project one already-resolved exact skill contract."""
        context = self.projection_context(
            skill_info,
            execution_cwd,
            gating=gating,
            backend=backend,
        )
        return project_agent_skill_document(context.skills[0], context).content


def default_skill_resolver() -> DefaultSkillResolver:
    """Construct the standard resolver for non-injected session dispatch."""
    return DefaultSkillResolver()


class DefaultSessionSkillManager:
    """Manages per-session ephemeral skill directories."""

    def __init__(
        self,
        provider: SkillsDirectoryProvider,
        ephemeral_root: Path,
        *,
        codex_root: Path | None = None,
    ) -> None:
        self._provider = provider
        self._root = ephemeral_root
        self._codex_root = codex_root
        self._session_roots: dict[str, Path] = {}
        self._session_skill_infos: dict[str, dict[str, SkillInfo | SkillCatalogEntry]] = {}
        self._available_skill_infos = {skill.name: skill for skill in provider.list_skills()}
        self._skills_subdir = ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR

    def materialize_invocation(
        self,
        session_id: str,
        invocation: EffectiveSkillInvocation,
        projection_context: SkillProjectionContext,
    ) -> ValidatedAddDir:
        """Write only a prevalidated closure from its captured canonical content."""
        self._validate_session_id(session_id)
        if not invocation.closure or invocation.root not in invocation.closure:
            raise ValueError("Effective invocation closure must contain its root")
        if invocation.execution_role is not SkillExecutionRole.SESSION:
            raise SkillContractError("L1 materialization requires an exact SESSION invocation")
        for member in invocation.closure:
            if member.invalid_reason is not None:
                raise SkillContractError(
                    f"invalid materialization contract for {member.name!r}: "
                    f"{member.invalid_reason}"
                )
            if member.execution_role is not SkillExecutionRole.SESSION:
                actual = (
                    member.execution_role.value if member.execution_role is not None else "invalid"
                )
                raise SkillContractError(
                    f"L1 materialization requires SESSION members; {member.name!r} is {actual}"
                )
        if projection_context.invocation != invocation:
            raise SkillContractError(
                "materialization projection must bind the exact effective invocation"
            )
        return self._materialize_bound_records(
            session_id,
            invocation.closure,
            projection_context,
        )

    def init_session(
        self,
        session_id: str,
        catalog: EffectiveSkillCatalog,
        projection_context: SkillProjectionContext,
    ) -> ValidatedAddDir:
        """Initialize a session from one prevalidated, path-free SESSION catalog."""
        self._validate_session_id(session_id)
        if catalog.execution_role is not SkillExecutionRole.SESSION:
            raise SkillContractError("L1 catalog materialization requires SESSION contracts")
        for member in catalog.skills:
            if member.invalid_reason is not None:
                raise SkillContractError(
                    f"invalid materialization contract for {member.name!r}: "
                    f"{member.invalid_reason}"
                )
            if member.execution_role is not SkillExecutionRole.SESSION:
                raise SkillContractError(
                    f"L1 catalog materialization requires SESSION members; "
                    f"{member.name!r} is {member.execution_role.value}"
                )
        if projection_context.catalog != catalog:
            raise SkillContractError(
                "materialization projection must bind the exact effective catalog"
            )
        return self._materialize_bound_records(
            session_id,
            catalog.skills,
            projection_context,
        )

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if (
            not session_id
            or "\x00" in session_id
            or "/" in session_id
            or "\\" in session_id
            or session_id in (".", "..")
        ):
            raise ValueError(f"Invalid session_id: {session_id!r}")

    def _materialize_bound_records(
        self,
        session_id: str,
        records: tuple[SkillInfo | SkillCatalogEntry, ...],
        projection_context: SkillProjectionContext,
    ) -> ValidatedAddDir:
        conventions = projection_context.conventions
        skills_subdir = (
            conventions.skills_subdir
            if conventions is not None
            else ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
        )
        effective_root = self._root
        backend = projection_context.backend
        if (
            backend is not None
            and backend.capabilities.session_dir_persistent
            and self._codex_root is not None
        ):
            effective_root = self._codex_root
        if backend is not None:
            if (
                any(record.source is SkillSource.PROJECT_LOCAL for record in records)
                and not backend.capabilities.project_local_skills_capable
            ):
                raise SkillContractError(
                    f"backend {backend.name!r} does not accept project-local skill contracts"
                )
            if (
                projection_context.gating is True
                and not backend.capabilities.supports_model_invocation_gating
            ):
                raise SkillContractError(
                    f"backend {backend.name!r} does not support model invocation gating"
                )

        self._session_roots[session_id] = effective_root
        self._skills_subdir = skills_subdir
        session_dir = effective_root / session_id
        skills_base = session_dir / skills_subdir

        if backend is not None and backend.capabilities.mcp_config_capable:
            pre_launch_errors = backend.ensure_pre_launch()
            if pre_launch_errors:
                raise RuntimeError(f"Pre-launch check failed: {'; '.join(pre_launch_errors)}")
        if backend is not None:
            session_dir.mkdir(parents=True, exist_ok=True)
            backend.setup_session_dir(session_dir)
        ungated_context = replace(projection_context, gating=False)
        materialize_agent_skill_tree(skills_base, records, ungated_context)
        self._session_skill_infos[session_id] = {member.name: member for member in records}

        return ValidatedAddDir(path=str(session_dir))

    def cleanup_session(self, session_id: str) -> bool:
        """Remove the session skill directory for a completed session.

        Returns True if the directory was found and removed, False otherwise.
        """
        effective_root = self._session_roots.pop(session_id, None)
        self._session_skill_infos.pop(session_id, None)
        if effective_root is not None:
            session_dir = effective_root / session_id
            if session_dir.is_dir():
                shutil.rmtree(session_dir, ignore_errors=True)
                return True
            return False
        for root in (self._root, self._codex_root):
            if root is None:
                continue
            candidate = root / session_id
            if candidate.is_dir():
                logger.debug("cleanup_session_fallback", session_id=session_id, root=str(root))
                shutil.rmtree(candidate, ignore_errors=True)
                return True
        return False

    def validate_session_exists(self, session_id: str) -> bool:
        """Return True if session directory exists and is non-empty."""
        for root in (self._root, self._codex_root):
            if root is None:
                continue
            candidate = root / session_id
            if candidate.is_dir():
                try:
                    return any(candidate.iterdir())
                except OSError:
                    return False
        return False

    def cleanup_stale(self, max_age_seconds: int = 86400) -> int:
        """Remove session dirs not accessed within max_age_seconds.

        Returns count of removed directories.
        """
        now = time.time()
        removed = 0
        for root in (self._root, self._codex_root):
            if root is None or not root.exists():
                continue
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                last_access = entry.stat().st_atime
                if now - last_access > max_age_seconds:
                    logger.warning(
                        "cleanup_stale_removed",
                        path=str(entry),
                        age_seconds=round(now - last_access),
                    )
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
        return removed
