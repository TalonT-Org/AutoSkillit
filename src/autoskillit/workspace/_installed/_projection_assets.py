"""What gets copied into a plugin projection, and the key that covers it.

Split out of ``_projection_cache.py``: the asset inventory and the cache-key
record are a self-contained "what defines a projection's identity" concern
that needs none of that module's retirement/reconciliation machinery.
Reached only by direct submodule import (never through ``_installed/__init__.py``'s
facade), matching how ``_shared_asset_store.py`` already reaches this same file's
``per_file_asset_digest`` today.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from autoskillit.core import is_python_bytecode_path

_CANONICAL_SKILL_DIRS = frozenset({"skills", "skills_extended"})
_PUBLIC_PLUGIN_ASSET_NAMES = frozenset(
    {
        ".claude-plugin",
        ".mcp.json",
        "_recipe_delivery_framing.py",
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

    Bytecode artifacts (``__pycache__`` directories and ``*.pyc``/``*.pyo``
    files) are excluded at every depth — they are interpreter-generated
    ephemera that must never enter a published file set whose identity is
    bound by a content digest.
    """
    name = entry.name
    if is_python_bytecode_path(entry):
        return False
    if name in _CANONICAL_SKILL_DIRS:
        return False
    return not (top_level and name not in _PUBLIC_PLUGIN_ASSET_NAMES)


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


#: Renderer-owned manifest excluded from the source-asset digest.
_RENDERED_HOOKS_MANIFEST_RELPATH = "hooks/hooks.json"


def per_file_asset_digest(path: Path) -> str:
    """Content-only SHA-256 of one file, independent of its relpath or projection.

    Extracted from public_plugin_asset_digest's per-file loop (S3-1) so a
    content-addressed shared asset store can key on file bytes alone: 91 separate
    copies of the identical mermaid.min.js each recompute the SAME digest here and
    therefore hash to the same store entry, regardless of which projection or
    relative path they arrived at. Distinct from public_plugin_asset_digest
    (whole-set, path-qualified), authority.py's asset_digest/semantic_key
    (asset-set + skill/adaptation/namespace identities), and artifact_digest (over
    the staged *output* tree) -- none of the other three is per-file or
    path-independent.
    """
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


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

    ``hooks/hooks.json`` is excluded because its projected bytes come from the
    renderer (``render_hooks_json_text``), not from the source tree.  Its
    coverage is provided by ``rendered_hooks_digest`` in the cache key.
    """
    digest = hashlib.sha256()
    for path in iter_public_plugin_asset_files(source_root):
        rel = path.relative_to(source_root).as_posix()
        if rel == _RENDERED_HOOKS_MANIFEST_RELPATH:
            continue
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(per_file_asset_digest(path)))
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
    adaptation_identity: str
    namespace_identity: str
    asset_digest: str
    rendered_hooks_digest: str

    def digest(self) -> str:
        payload = "\0".join(
            (
                self.source_root,
                self.backend_name,
                str(self.projection_version),
                self.default_base_branch,
                self.skill_identity,
                self.adaptation_identity,
                self.namespace_identity,
                self.asset_digest,
                self.rendered_hooks_digest,
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
            "SkillProjectionBinding, which is rebuilt per invocation and never "
            "cached, so two invocations differing only in cwd may safely share a projection."
        ),
        "project_root": (
            "Not byte-affecting, and constant: projection authority always passes "
            "project_root=None into the projection context."
        ),
        "skills": (
            "Covered by `skill_identity` (name + canonical digest + sidecar digest, "
            "per skill) and `namespace_identity` (name -> source). The skills/ tree "
            "is regenerated from those contracts, and sidecar bytes are covered via "
            "skill_identity's sidecar-digest component, so digesting the on-disk skill "
            "directories would be redundant."
        ),
        "skills_extended": (
            "Same as `skills`: canonical skill trees are never copied verbatim into a "
            "projection (_CANONICAL_SKILL_DIRS), only projected from their contracts."
        ),
    }
)
