"""Repair broken published hook artifacts and refresh their manifests."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from autoskillit.core import (
    _AUTOSKILLIT_PLUGIN_KEY,
    ArtifactLease,
    ArtifactLeaseContention,
    atomic_write,
    get_logger,
    installed_plugin_artifact_lease_path,
    installed_plugin_artifact_manifest_path,
    installed_plugin_semantic_key,
    read_installed_plugin_artifact_identity,
)
from autoskillit.hook_registry import (
    PLUGIN_ROOT_TOKEN,
    find_broken_hook_scripts,
    render_relocatable_hook_command,
)
from autoskillit.workspace._installed_artifact import (
    write_installed_plugin_artifact_manifest_locked,
)
from autoskillit.workspace._projection_cache import (
    projected_artifact_lease_path,
    projected_artifact_manifest_path,
    projected_plugin_artifact_digest,
)

__all__ = [
    "PluginHookRepairOutcome",
    "PluginHookRepairStatus",
    "ProjectedArtifactHooksInvalid",
    "repair_broken_plugin_cache_hooks",
    "repair_broken_projection_hooks",
    "validate_staged_plugin_hooks",
]

logger = get_logger(__name__)


class ProjectedArtifactHooksInvalid(Exception):
    """A staged or published plugin artifact has broken or non-relocatable hook commands."""


def validate_staged_plugin_hooks(staging_root: Path) -> None:
    """Validate hook commands in a staged or published plugin artifact.

    Raises :class:`ProjectedArtifactHooksInvalid` when any command is absolute
    (non-relocatable) or when a token-form command's dispatcher target does not
    exist under *staging_root*.
    """
    hooks_json_path = staging_root / "hooks" / "hooks.json"
    if not hooks_json_path.is_file():
        return
    try:
        data = json.loads(hooks_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectedArtifactHooksInvalid(f"staged hooks.json is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectedArtifactHooksInvalid("staged hooks.json must contain a JSON object")
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        raise ProjectedArtifactHooksInvalid("staged hooks.json must contain a hooks object")
    for event_type, entries in hooks.items():
        if not isinstance(entries, list):
            raise ProjectedArtifactHooksInvalid(
                f"staged hooks.json event {event_type!r} must contain a list"
            )
        for entry in entries:
            if not isinstance(entry, dict):
                raise ProjectedArtifactHooksInvalid(
                    f"staged hooks.json event {event_type!r} contains a malformed entry"
                )
            entry_hooks = entry.get("hooks")
            if not isinstance(entry_hooks, list):
                raise ProjectedArtifactHooksInvalid(
                    f"staged hooks.json event {event_type!r} entry must contain a hooks list"
                )
            for hook in entry_hooks:
                if not isinstance(hook, dict):
                    raise ProjectedArtifactHooksInvalid(
                        f"staged hooks.json event {event_type!r} contains a malformed hook"
                    )
                cmd = hook.get("command", "")
                if not isinstance(cmd, str) or not cmd:
                    continue
                if PLUGIN_ROOT_TOKEN not in cmd:
                    raise ProjectedArtifactHooksInvalid(
                        f"staged hook command is not relocatable (no "
                        f"{PLUGIN_ROOT_TOKEN} token): {cmd}"
                    )
                resolved = cmd.replace(PLUGIN_ROOT_TOKEN, str(staging_root))
                try:
                    parts = shlex.split(resolved)
                except ValueError:
                    raise ProjectedArtifactHooksInvalid(
                        f"staged hook command cannot be parsed: {cmd}"
                    )
                if len(parts) >= 3 and parts[-2].endswith("_dispatch.py"):
                    dispatcher = Path(parts[-2])
                    if not dispatcher.is_file():
                        raise ProjectedArtifactHooksInvalid(
                            f"staged hook dispatcher does not exist: "
                            f"{dispatcher} (from command: {cmd})"
                        )


class PluginHookRepairStatus(StrEnum):
    """Closed outcomes for an incarnation considered by hook repair."""

    REPAIRED = "repaired"
    CONTENDED = "contended"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PluginHookRepairOutcome:
    """Per-incarnation repair result."""

    incarnation_dir: Path
    status: PluginHookRepairStatus
    detail: str | None = None


def _logical_hook_name(command: str) -> str:
    """Recover the version-owned logical hook name from an old command."""
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"cannot parse hook command: {command!r}") from exc
    has_dispatcher = any(part.endswith("_dispatch.py") for part in parts)
    if len(parts) >= 3 and parts[-2].endswith("_dispatch.py"):
        logical_name = parts[-1]
    elif has_dispatcher:
        logical_name = ""
    elif len(parts) >= 2:
        script_path = parts[-1].replace("\\", "/")
        marker = "/hooks/"
        logical_name = script_path.rpartition(marker)[2] if marker in script_path else ""
    else:
        logical_name = ""
    logical_name = logical_name.removesuffix(".py").strip("/")
    components = logical_name.split("/")
    if not logical_name or any(part in {"", ".", ".."} for part in components):
        raise ValueError(f"cannot recover logical hook name from command: {command!r}")
    return logical_name


def _relocate_existing_hooks(payload: Any) -> dict[str, Any]:
    """Relocate commands without consulting the running version's registry."""
    if not isinstance(payload, dict) or not isinstance(payload.get("hooks"), dict):
        raise ValueError("hooks.json does not contain a hooks object")
    for entries in payload["hooks"].values():
        if not isinstance(entries, list):
            raise ValueError("hooks.json event entries must be lists")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                raise ValueError("hooks.json entry does not contain a hooks list")
            for hook in entry["hooks"]:
                if not isinstance(hook, dict) or not isinstance(hook.get("command"), str):
                    raise ValueError("hooks.json command entry is malformed")
                logical_name = _logical_hook_name(hook["command"])
                hook["command"] = render_relocatable_hook_command(logical_name)
    return payload


def _rollback_repair(
    *,
    hooks_json_path: Path,
    original_hooks: str,
    manifest_path: Path,
    original_manifest: str | None,
) -> tuple[str, ...]:
    """Restore both repair outputs while the caller still holds the lease."""
    failures: list[str] = []
    try:
        atomic_write(hooks_json_path, original_hooks, strict_durability=True)
    except Exception as exc:
        logger.warning(
            "plugin_cache_hooks_rollback_hooks_failed",
            path=str(hooks_json_path),
            exc_info=True,
        )
        failures.append(f"hooks rollback failed: {exc}")
    try:
        if original_manifest is None:
            manifest_path.unlink(missing_ok=True)
        else:
            atomic_write(manifest_path, original_manifest, strict_durability=True)
    except Exception as exc:
        logger.warning(
            "plugin_cache_hooks_rollback_manifest_failed",
            path=str(manifest_path),
            exc_info=True,
        )
        failures.append(f"manifest rollback failed: {exc}")
    return tuple(failures)


def repair_broken_plugin_cache_hooks(
    cache_dir: Path,
) -> tuple[PluginHookRepairOutcome, ...]:
    """Regenerate broken hooks.json for every incarnation under ``cache_dir``.

    For each ``<version>`` incarnation with broken hook commands (token-aware
    ``find_broken_hook_scripts``), preserve that incarnation's logical hook
    structure while relocating each command through its own dispatcher.
    Hooks and manifest are updated as one rollback-protected operation and
    the repaired artifact is revalidated before success is reported.

    Per-incarnation errors are returned as closed outcomes. This primitive
    repairs hook artifacts only and never clears publication obligations.
    """
    if not cache_dir.is_dir():
        return ()
    outcomes: list[PluginHookRepairOutcome] = []
    for version_dir in sorted(
        p
        for p in cache_dir.iterdir()
        if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
    ):
        version = version_dir.name
        hooks_json_path = version_dir / "hooks" / "hooks.json"
        try:
            if not hooks_json_path.is_file():
                continue
            broken = find_broken_hook_scripts(hooks_json_path, expansion_root=version_dir)
            if not broken:
                continue
            lease_path = installed_plugin_artifact_lease_path(version_dir)
            with ArtifactLease.acquire_exclusive(lease_path, blocking=False):
                original_hooks = hooks_json_path.read_text(encoding="utf-8")
                if not find_broken_hook_scripts(hooks_json_path, expansion_root=version_dir):
                    continue
                semantic_key = installed_plugin_semantic_key(_AUTOSKILLIT_PLUGIN_KEY, version)
                read_installed_plugin_artifact_identity(
                    version_dir,
                    expected_semantic_key=semantic_key,
                )
                manifest_path = installed_plugin_artifact_manifest_path(version_dir)
                original_manifest = (
                    manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else None
                )
                fresh = _relocate_existing_hooks(json.loads(original_hooks))
                try:
                    atomic_write(
                        hooks_json_path,
                        json.dumps(fresh, indent=2) + "\n",
                        strict_durability=True,
                    )
                    write_installed_plugin_artifact_manifest_locked(
                        version_dir,
                        semantic_key=semantic_key,
                        action="repair",
                    )
                    remaining = find_broken_hook_scripts(
                        hooks_json_path,
                        expansion_root=version_dir,
                    )
                    if remaining:
                        raise RuntimeError(
                            f"{len(remaining)} broken hook command(s) remain after repair"
                        )
                except Exception as exc:
                    rollback_failures = _rollback_repair(
                        hooks_json_path=hooks_json_path,
                        original_hooks=original_hooks,
                        manifest_path=manifest_path,
                        original_manifest=original_manifest,
                    )
                    detail = f"hook repair transaction failed: {exc}"
                    if rollback_failures:
                        detail = f"{detail}; {'; '.join(rollback_failures)}"
                    raise RuntimeError(detail) from exc
        except ArtifactLeaseContention:
            outcomes.append(
                PluginHookRepairOutcome(
                    incarnation_dir=version_dir,
                    status=PluginHookRepairStatus.CONTENDED,
                    detail="lease contended",
                )
            )
            logger.warning("plugin_cache_hooks_repair_skipped_contended", version=version)
            continue
        except Exception as exc:
            outcomes.append(
                PluginHookRepairOutcome(
                    incarnation_dir=version_dir,
                    status=PluginHookRepairStatus.FAILED,
                    detail=str(exc),
                )
            )
            logger.warning("plugin_cache_hooks_repair_failed", version=version, exc_info=True)
            continue
        outcomes.append(
            PluginHookRepairOutcome(
                incarnation_dir=version_dir,
                status=PluginHookRepairStatus.REPAIRED,
            )
        )
        logger.info("plugin_cache_hooks_repaired", version=version)
    return tuple(outcomes)


def repair_broken_projection_hooks(
    projections_root: Path | None = None,
) -> tuple[PluginHookRepairOutcome, ...]:
    """Repair broken hooks in ``~/.autoskillit/plugin-projections/*``.

    Contended projections are skipped. Hooks and the sidecar digest are updated
    as one rollback-protected transaction and revalidated before success.
    """
    if projections_root is None:
        projections_root = Path.home() / ".autoskillit" / "plugin-projections"
    if not projections_root.is_dir():
        return ()
    outcomes: list[PluginHookRepairOutcome] = []
    for projection_dir in sorted(
        p
        for p in projections_root.iterdir()
        if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
    ):
        hooks_json_path = projection_dir / "hooks" / "hooks.json"
        try:
            if not hooks_json_path.is_file():
                continue
            broken = find_broken_hook_scripts(hooks_json_path, expansion_root=projection_dir)
            if not broken:
                continue
            lease_path = projected_artifact_lease_path(projection_dir)
            with ArtifactLease.acquire_exclusive(lease_path, blocking=False):
                original_hooks = hooks_json_path.read_text(encoding="utf-8")
                if not find_broken_hook_scripts(hooks_json_path, expansion_root=projection_dir):
                    continue
                manifest_path = projected_artifact_manifest_path(projection_dir)
                original_manifest = (
                    manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else None
                )
                fresh = _relocate_existing_hooks(json.loads(original_hooks))
                try:
                    atomic_write(
                        hooks_json_path,
                        json.dumps(fresh, indent=2) + "\n",
                        strict_durability=True,
                    )
                    if manifest_path.is_file():
                        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                        new_digest = projected_plugin_artifact_digest(projection_dir)
                        manifest_data["artifact_digest"] = new_digest
                        atomic_write(
                            manifest_path,
                            json.dumps(manifest_data, indent=2) + "\n",
                            strict_durability=True,
                        )
                    remaining = find_broken_hook_scripts(
                        hooks_json_path,
                        expansion_root=projection_dir,
                    )
                    if remaining:
                        raise RuntimeError(
                            f"{len(remaining)} broken hook command(s) remain after repair"
                        )
                except Exception as exc:
                    rollback_failures = _rollback_repair(
                        hooks_json_path=hooks_json_path,
                        original_hooks=original_hooks,
                        manifest_path=manifest_path,
                        original_manifest=original_manifest,
                    )
                    detail = f"projection hook repair transaction failed: {exc}"
                    if rollback_failures:
                        detail = f"{detail}; {'; '.join(rollback_failures)}"
                    raise RuntimeError(detail) from exc
        except ArtifactLeaseContention:
            outcomes.append(
                PluginHookRepairOutcome(
                    incarnation_dir=projection_dir,
                    status=PluginHookRepairStatus.CONTENDED,
                    detail="lease contended",
                )
            )
            logger.warning(
                "projection_hooks_repair_skipped_contended",
                projection=projection_dir.name,
            )
            continue
        except Exception as exc:
            outcomes.append(
                PluginHookRepairOutcome(
                    incarnation_dir=projection_dir,
                    status=PluginHookRepairStatus.FAILED,
                    detail=str(exc),
                )
            )
            logger.warning(
                "projection_hooks_repair_failed",
                projection=projection_dir.name,
                exc_info=True,
            )
            continue
        outcomes.append(
            PluginHookRepairOutcome(
                incarnation_dir=projection_dir,
                status=PluginHookRepairStatus.REPAIRED,
            )
        )
        logger.info("projection_hooks_repaired", projection=projection_dir.name)
    return tuple(outcomes)
