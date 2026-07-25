"""What a plugin projection is made of, and when it goes stale.

Split out of ``skill_projection`` because staleness is its own concern: the
projection cache key used to cover only skill names and digests, so a release
that changed ``recipes/``, ``agents/``, or ``hooks/`` without touching a skill
produced an identical key and the previous release's assets were silently
reused. Keeping the asset inventory, the key record, and the orphan sweep in
one module is what stops those three from drifting apart again.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from autoskillit.core import append_retiring_entry, sweep_retiring_cache

__all__ = [
    "PROJECTION_CACHE_KEY_EXCLUSIONS",
    "ProjectionCacheKey",
    "is_projected_asset",
    "iter_public_plugin_asset_files",
    "prune_stale_projections",
    "public_plugin_asset_digest",
]

#: Grace window before an orphaned projection directory is actually deleted.
#: Deliberately generous: a long-running session may still be reading a
#: projection whose key has just been superseded by an upgrade.
_PROJECTION_GRACE_HOURS = 6

_CANONICAL_SKILL_DIRS = frozenset({"skills", "skills_extended"})
_PUBLIC_PLUGIN_ASSET_NAMES = frozenset(
    {
        ".claude-plugin",
        ".mcp.json",
        "agents",
        "assets",
        "commands",
        "hooks",
        "recipes",
        "scripts",
        "settings.json",
    }
)


def is_projected_asset(entry: Path, *, top_level: bool) -> bool:
    """Return True if *entry* is copied verbatim into a projection.

    The single predicate behind both the copier and the cache-key digest, so
    the two can never disagree about what a projection is made of.
    """
    if entry.name in _CANONICAL_SKILL_DIRS:
        return False
    return not (top_level and entry.name not in _PUBLIC_PLUGIN_ASSET_NAMES)


def iter_public_plugin_asset_files(source_root: Path, *, top_level: bool = True) -> Iterator[Path]:
    """Yield every regular file ``_copy_non_skill_plugin_assets`` would copy.

    Deliberately mirrors the copier's traversal via the shared
    ``_is_projected_asset`` predicate; ``test_asset_digest_covers_copied_files``
    asserts the two agree on a real projection.
    """
    if not source_root.is_dir():
        return
    for entry in sorted(source_root.iterdir(), key=lambda item: item.name):
        if not is_projected_asset(entry, top_level=top_level):
            continue
        if entry.is_symlink():
            continue
        if entry.is_dir():
            yield from iter_public_plugin_asset_files(entry, top_level=False)
        elif entry.is_file():
            yield entry


def public_plugin_asset_digest(source_root: Path) -> str:
    """Digest every byte a projection copies out of *source_root*.

    This is what makes the projection cache key honest. ``identity`` and
    ``namespace_identity`` cover only skill names and digests, so without this
    a release that changes ``recipes/``, ``agents/``, ``hooks/``, or
    ``plugin.json`` — but no skill — produces the *same* key and the stale
    projection is reused. That is silent mixed-version execution, and it is the
    defect this whole module's source policy exists to prevent.

    A bare ``__version__`` would not do: under an editable install the version
    is static while the files change continuously, pinning a stale projection
    for an entire development cycle.
    """
    digest = hashlib.sha256()
    for path in iter_public_plugin_asset_files(source_root):
        rel = path.relative_to(source_root).as_posix()
        digest.update(rel.encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            digest.update(hashlib.file_digest(handle, "sha256").digest())
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ProjectionCacheKey:
    """Every input that can change a projection's bytes, in one place.

    The key is derived from this record rather than a hand-concatenated string
    so a future input cannot be omitted by accident:
    ``test_cache_key_record_fields_are_keyed_or_excluded`` fails the build when
    a field appears here without being hashed, and when an entry in
    ``_PUBLIC_PLUGIN_ASSET_NAMES`` is neither digested nor excluded below.
    """

    source_root: str
    backend_name: str
    projection_version: int
    default_base_branch: str
    skill_identity: str
    namespace_identity: str
    asset_digest: str

    def digest(self) -> str:
        payload = "\0".join(
            (
                self.source_root,
                self.backend_name,
                str(self.projection_version),
                self.default_base_branch,
                self.skill_identity,
                self.namespace_identity,
                self.asset_digest,
            )
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:24]


#: Projection inputs deliberately left out of the cache key, each with the
#: reason it cannot affect projected bytes. An input is either keyed or listed
#: here with a written rationale — a guard test permits no third option.
PROJECTION_CACHE_KEY_EXCLUSIONS: Mapping[str, str] = MappingProxyType(
    {
        "cwd": (
            "Not byte-affecting. The only substitutions bound by "
            "_direct_install_projection_context are {{AUTOSKILLIT_TEMP}} (process-wide), "
            "{{AUTOSKILLIT_SCRIPTS}} (derived from `destination`, which is derived from "
            "this key) and {{DEFAULT_BASE_BRANCH}} (keyed). `cwd` reaches only "
            "EffectiveSkillDispatchContract, which is rebuilt per invocation and never "
            "cached, so two invocations differing only in cwd may safely share a projection."
        ),
        "project_root": (
            "Not byte-affecting, and constant: project_direct_install always passes "
            "project_root=None into the projection context."
        ),
        "skills": (
            "Covered by `skill_identity` (name + canonical digest, per skill) and "
            "`namespace_identity` (name -> source). The skills/ tree is regenerated from "
            "those contracts, so digesting the on-disk skill directories would be redundant."
        ),
        "skills_extended": (
            "Same as `skills`: canonical skill trees are never copied verbatim into a "
            "projection (_CANONICAL_SKILL_DIRS), only projected from their contracts."
        ),
    }
)


def prune_stale_projections(
    projections_root: Path,
    *,
    active_key: str,
    grace_hours: int = _PROJECTION_GRACE_HOURS,
) -> int:
    """Queue non-active projection directories for retirement; return how many.

    ``plugin-projections/`` had no cleanup at all, and both the source change
    and the key-composition change orphan every existing user's projection at
    once. Reuses the retiring-cache grace/lock machinery rather than inventing
    a second deletion mechanism, so a projection still in use by a running
    session survives the grace window.

    Caller must already hold ``_InstallLock``.
    """
    if not projections_root.is_dir():
        return 0
    retired = 0
    for entry in sorted(projections_root.iterdir(), key=lambda item: item.name):
        if entry.name == active_key or not entry.is_dir() or entry.is_symlink():
            continue
        append_retiring_entry(version=f"projection:{entry.name}", path=str(entry))
        manifest = projections_root / f".{entry.name}.autoskillit-projection.json"
        if manifest.is_file():
            manifest.unlink()
        retired += 1
    if retired:
        sweep_retiring_cache(grace_hours=grace_hours)
    return retired
