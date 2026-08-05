"""Repair primitive for broken published hook artifacts in the plugin cache.

Placement is constrained by an enforced architectural guard
(``tests/arch/test_layer_enforcement.py``, REQ-ARCH-003b): no non-``tools_*``
module under ``server/`` may import ``autoskillit.cli`` — and
``server/_lifespan.py`` is this repair primitive's primary caller. It
therefore cannot live in ``cli/_plugin_artifact.py``; ``workspace/`` (IL-1)
is the same layer ``server/_lifespan.py`` already imports unconditionally
for ``verify_install_state()`` (see ``workspace/_install_state.py``), so the
edge is precedented and legal.

The manifest-refresh logic here is deliberately an INDEPENDENT
implementation from ``cli/_plugin_artifact.py``'s
``publish_installed_plugin_artifact`` rather than an import of it (an
upward ``workspace → cli`` import is illegal). Both are built from the
identical core-layer digest/manifest primitives
(``directory_tree_digest``, ``write_versioned_json``,
``installed_plugin_artifact_manifest_payload``), so they write the same
manifest schema and neither invalidates the other's tamper detection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (
    _AUTOSKILLIT_PLUGIN_KEY,
    INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    ArtifactLease,
    ArtifactLeaseContention,
    PluginArtifactIdentity,
    atomic_write,
    directory_tree_digest,
    get_logger,
    installed_plugin_artifact_lease_path,
    installed_plugin_artifact_manifest_path,
    installed_plugin_artifact_manifest_payload,
    installed_plugin_semantic_key,
    log_plugin_artifact_lifecycle,
    new_plugin_artifact_incarnation_id,
    write_versioned_json,
)
from autoskillit.hook_registry import find_broken_hook_scripts, generate_hooks_json

__all__ = ["RepairOutcome", "repair_broken_plugin_cache_hooks"]

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    """Per-incarnation repair result."""

    incarnation_dir: Path
    repaired: bool
    skipped_reason: str | None = None


def _refresh_manifest_locked(managed_path: Path, *, semantic_key: str) -> PluginArtifactIdentity:
    """Republish an incarnation's manifest while the caller already owns its lease."""
    manifest_path = installed_plugin_artifact_manifest_path(managed_path)
    identity = PluginArtifactIdentity(
        semantic_key=semantic_key,
        incarnation_id=new_plugin_artifact_incarnation_id(),
        manifest_schema_version=INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        artifact_digest=directory_tree_digest(managed_path),
        managed_path=managed_path,
        manifest_path=manifest_path,
    )
    write_versioned_json(
        manifest_path,
        installed_plugin_artifact_manifest_payload(identity),
        schema_version=INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        strict_durability=True,
    )
    log_plugin_artifact_lifecycle(
        logger,
        action="repair",
        outcome="succeeded",
        artifact_kind="installed_plugin",
        semantic_key=identity.semantic_key,
        incarnation=identity.incarnation_id,
    )
    return identity


def repair_broken_plugin_cache_hooks(cache_dir: Path) -> tuple[RepairOutcome, ...]:
    """Regenerate broken hooks.json for every incarnation under ``cache_dir``.

    For each ``<version>`` incarnation with broken hook commands (token-aware
    ``find_broken_hook_scripts``), regenerate ``hooks/hooks.json`` from
    ``HOOK_REGISTRY`` in relocatable form and refresh the incarnation's
    manifest so the digest/tamper detector stays consistent with the
    rewritten file — rewriting a cache hooks.json without refreshing its
    manifest would turn the tamper detector into a false alarm. Reuses
    ``generate_hooks_json()`` — no second implementation of hooks.json
    serialization.

    Lease contention or any other per-incarnation error is a skip with a
    structured diagnostic; this primitive never raises out. It repairs hook
    artifacts only — it does not clear a publication obligation (see
    ``workspace._update_obligation``), because it cannot perform the full
    publication that obligation may demand.
    """
    if not cache_dir.is_dir():
        return ()
    outcomes: list[RepairOutcome] = []
    for version_dir in sorted(
        p for p in cache_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
    ):
        hooks_json_path = version_dir / "hooks" / "hooks.json"
        if not hooks_json_path.is_file():
            continue
        broken = find_broken_hook_scripts(hooks_json_path, expansion_root=version_dir)
        if not broken:
            continue
        version = version_dir.name
        try:
            lease_path = installed_plugin_artifact_lease_path(version_dir)
            with ArtifactLease.acquire_exclusive(lease_path, blocking=False):
                fresh = generate_hooks_json()
                atomic_write(hooks_json_path, json.dumps(fresh, indent=2) + "\n")
                semantic_key = installed_plugin_semantic_key(_AUTOSKILLIT_PLUGIN_KEY, version)
                _refresh_manifest_locked(version_dir, semantic_key=semantic_key)
        except ArtifactLeaseContention:
            outcomes.append(
                RepairOutcome(
                    incarnation_dir=version_dir,
                    repaired=False,
                    skipped_reason="lease contended",
                )
            )
            logger.warning("plugin_cache_hooks_repair_skipped_contended", version=version)
            continue
        except Exception as exc:
            outcomes.append(
                RepairOutcome(
                    incarnation_dir=version_dir,
                    repaired=False,
                    skipped_reason=str(exc),
                )
            )
            logger.warning("plugin_cache_hooks_repair_failed", version=version, exc_info=True)
            continue
        outcomes.append(RepairOutcome(incarnation_dir=version_dir, repaired=True))
        logger.info("plugin_cache_hooks_repaired", version=version)
    return tuple(outcomes)
