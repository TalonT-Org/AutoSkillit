"""Session-skill provider, ephemeral-root resolution, and closure write dirs.

Single owner of ``SkillsDirectoryProvider``, the ephemeral-root candidate
list, ``default_skill_resolver``, ``resolve_ephemeral_root``, and
``resolve_closure_write_dirs``. Catalog vs invocation projection-context
binding is preserved by constructing the stable
``skill_projection.SkillProjectionContext`` whose ``__post_init__`` enforces
exclusivity.

Closure write-dir resolution composes the closure's write scopes through the
shared ``autoskillit.hooks._write_scope`` fold (the same union the interactive
write guard applies), checks each declared directory against the session temp
root there, and preserves source order, ``existing`` exclusion, and
first-occurrence deduplication.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    ManagedCodexRoute,
    RepositoryProfileId,
    SemanticAdaptationContext,
    SessionHookRoot,
    SkillContractError,
    SkillExecutionRole,
    SkillResolver,
    destination_location,
    pkg_root,
)
from autoskillit.hooks._write_scope import fold_session_write_scopes, temp_root_escape
from autoskillit.workspace.session_skills._projection import (
    SkillProjectionContext,
    project_agent_skill_document,
)
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillInfo,
)

if TYPE_CHECKING:
    from autoskillit.core import (
        CodingAgentBackend,
        ResolvedSkillAuthority,
    )
    from autoskillit.hooks._write_scope import WriteScope

# Candidate ephemeral roots, tried in order.
# resolve_ephemeral_root() appends tempfile.gettempdir() as the final fallback.
_CANDIDATE_ROOTS: list[Path] = [
    Path("/dev/shm"),
    Path("/tmp"),
]


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


def resolve_closure_write_dirs(
    closure: tuple[ResolvedSkillAuthority, ...],
    cwd: str,
    existing: list[Path] | None = None,
) -> list[Path]:
    """Resolve an exact effective closure's BOUNDED write scopes into absolute Paths.

    UNRESTRICTED and INHERIT members contribute nothing. Raises
    ``SkillContractError`` when a member lacks a valid write scope or declares a
    directory that resolves outside ``cwd``'s temp root. Paths already present in
    ``existing`` are excluded from the result.
    """
    scopes: list[tuple[str, WriteScope]] = []
    for info in closure:
        if info.write_scope is None:
            raise SkillContractError(f"closure member {info.name} lacks a valid write scope")
        scopes.append((info.name, info.write_scope))
    seen: set[Path] = {destination_location(path) for path in existing or ()}
    result: list[Path] = []
    for name, prefixes in fold_session_write_scopes(scopes, cwd).contributors:
        for prefix in prefixes:
            escape = temp_root_escape(prefix, cwd)
            if escape is not None:
                raise SkillContractError(
                    f"declared write scope for closure member {name} {escape}"
                )
            resolved = destination_location(Path(prefix))
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

    def get_skill_content(
        self,
        skill_info: SkillInfo,
        *,
        cwd: Path,
        gated: bool = True,
    ) -> str:
        """Project already-resolved SKILL.md content with optional gating.

        - gated=True  → ensure disable-model-invocation: true is present
          (used only by the activate path — init_session omits gated skills entirely)
        - gated=False → return unmodified content (cook session or Tier 1 skills)

        Substitutes ``{{AUTOSKILLIT_TEMP}}`` with the configured temp dir relpath.
        Tier 1 skills (which contain no placeholder) are unaffected.
        """
        # No artifact binding — get_skill_content serves dev-checkout readers only.
        return self.project_skill_info(
            skill_info,
            cwd=cwd,
            gating=True if gated else None,
            durable_scripts_root=pkg_root(),
        )

    def projection_context(
        self,
        skill_info: SkillInfo,
        cwd: Path,
        *,
        gating: bool | None = None,
        backend: CodingAgentBackend | None = None,
        durable_scripts_root: Path,
    ) -> SkillProjectionContext:
        """Build the shared execution-local projection context.

        ``durable_scripts_root`` is required — every caller must supply the
        root whose lifetime exceeds the session's (no implicit ``pkg_root()``
        default, which would bake a venv-relative path into projected documents).
        """
        catalog = EffectiveSkillCatalog(
            skills=(SkillCatalogEntry.from_skill_info(skill_info),),
            execution_role=skill_info.execution_role or SkillExecutionRole.SESSION,
        )
        return self.catalog_projection_context(
            catalog,
            cwd,
            gating=gating,
            backend=backend,
            durable_scripts_root=durable_scripts_root,
            adaptation_context=None,
        )

    def catalog_projection_context(
        self,
        catalog: EffectiveSkillCatalog,
        cwd: Path,
        *,
        gating: bool | None = None,
        backend: CodingAgentBackend | None = None,
        durable_scripts_root: Path,
        resolved_exploration_profile: RepositoryProfileId | None = None,
        adaptation_context: SemanticAdaptationContext | None = None,
        managed_codex_route: ManagedCodexRoute | None = None,
        session_hook_root: SessionHookRoot | None = None,
    ) -> SkillProjectionContext:
        """Build one projection context bound to a resolved path-free catalog.

        ``durable_scripts_root`` is the root a projected document's
        ``{{AUTOSKILLIT_SCRIPTS}}`` placeholder resolves against — it must
        never have a shorter lifetime than the session consuming the
        projected document. Required — no implicit default.  Callers that hold
        a leased plugin launch binding must pass the binding's
        ``identity.managed_path``; callers operating from the dev checkout pass
        ``pkg_root()`` explicitly.

        ``session_hook_root`` is the leased tree a per-session backend home
        bakes its hook commands from; a backend that bakes per-session hooks
        refuses to materialize a home without one.
        """
        scripts_root = durable_scripts_root
        return SkillProjectionContext(
            cwd=cwd,
            catalog=catalog,
            backend=backend,
            conventions=backend.conventions if backend is not None else None,
            resolved_exploration_profile=resolved_exploration_profile,
            adaptation_context=adaptation_context,
            managed_codex_route=managed_codex_route,
            session_hook_root=session_hook_root,
            substitutions={
                "{{AUTOSKILLIT_TEMP}}": self._temp_dir_relpath,
                "{{AUTOSKILLIT_SCRIPTS}}": str(scripts_root / "recipes" / "scripts"),
                "{{DEFAULT_BASE_BRANCH}}": self._default_base_branch,
            },
            gating=gating,
        )

    def project_skill_info(
        self,
        skill_info: SkillInfo,
        *,
        cwd: Path,
        gating: bool | None = None,
        backend: CodingAgentBackend | None = None,
        durable_scripts_root: Path,
    ) -> str:
        """Project one already-resolved exact skill contract."""
        context = self.projection_context(
            skill_info,
            cwd,
            gating=gating,
            backend=backend,
            durable_scripts_root=durable_scripts_root,
        )
        return project_agent_skill_document(context.skills[0], context).content


def default_skill_resolver() -> DefaultSkillResolver:
    """Construct the standard resolver for non-injected session dispatch."""
    return DefaultSkillResolver()


__all__ = [
    "SkillsDirectoryProvider",
    "_CANDIDATE_ROOTS",
    "default_skill_resolver",
    "resolve_closure_write_dirs",
    "resolve_ephemeral_root",
]
