"""Repair broken published hook artifacts and refresh their manifests."""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from autoskillit.core import (
    _AUTOSKILLIT_PLUGIN_KEY,
    ArtifactLease,
    ArtifactLeaseContention,
    PluginArtifactValidationError,
    atomic_write,
    installed_plugin_artifact_lease_path,
    installed_plugin_artifact_manifest_path,
    installed_plugin_semantic_key,
    read_installed_plugin_artifact_identity,
    read_versioned_json,
    write_versioned_json,
)
from autoskillit.hook_registry import (
    PLUGIN_ROOT_TOKEN,
    find_broken_hook_scripts,
    is_hook_payload_quarantined,
    quarantine_hook_payload,
    render_relocatable_hook_command,
)
from autoskillit.workspace._installed._artifact import (
    write_installed_plugin_artifact_manifest_locked,
)
from autoskillit.workspace._installed._projection_cache import (
    PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
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
    for command in _iter_staged_hook_commands(data):
        _validate_staged_hook_command(staging_root, command)


def _iter_staged_hook_commands(data: dict[str, object]) -> Iterator[str]:
    """Yield commands while preserving staged-hook structural diagnostics."""
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
                yield cmd


def _validate_staged_hook_command(staging_root: Path, command: str) -> None:
    """Validate one relocatable hook command against its staged dispatcher."""
    if PLUGIN_ROOT_TOKEN not in command:
        raise ProjectedArtifactHooksInvalid(
            f"staged hook command is not relocatable (no {PLUGIN_ROOT_TOKEN} token): {command}"
        )
    resolved = command.replace(PLUGIN_ROOT_TOKEN, str(staging_root))
    try:
        parts = shlex.split(resolved)
    except ValueError:
        raise ProjectedArtifactHooksInvalid(f"staged hook command cannot be parsed: {command}")
    if len(parts) < 3 or not parts[-2].endswith("_dispatch.py"):
        raise ProjectedArtifactHooksInvalid(
            f"staged hook command has invalid dispatcher shape: {command}"
        )
    dispatcher = Path(parts[-2])
    if not dispatcher.is_file():
        raise ProjectedArtifactHooksInvalid(
            f"staged hook dispatcher does not exist: {dispatcher} (from command: {command})"
        )


class PluginHookRepairStatus(StrEnum):
    """Closed outcomes for an incarnation considered by hook repair."""

    REPAIRED = "repaired"
    QUARANTINED = "quarantined"
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


def _relocate_raw_hooks(raw_hooks: bytes) -> dict[str, Any]:
    """Parse and validate raw hooks bytes before a repair transaction mutates them."""
    try:
        payload = json.loads(raw_hooks)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectedArtifactHooksInvalid(f"hooks.json is not valid JSON: {exc}") from exc
    try:
        return _relocate_existing_hooks(payload)
    except ValueError as exc:
        raise ProjectedArtifactHooksInvalid(f"hooks.json cannot be relocated: {exc}") from exc


def _load_projection_hook_manifest(manifest_path: Path) -> dict[str, Any] | None:
    """Admit the projection manifest before a hook-repair transaction mutates bytes."""
    if not manifest_path.is_file():
        return None
    manifest_data = read_versioned_json(
        manifest_path,
        PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    )
    if manifest_data is None:
        raise RuntimeError("projection manifest schema is missing, corrupt, or unsupported")
    return manifest_data


def _write_projection_hook_manifest(
    projection_dir: Path,
    manifest_path: Path,
    manifest_data: dict[str, Any] | None,
) -> None:
    """Refresh a projection manifest digest after its repaired hook bytes are durable."""
    if manifest_data is None:
        return
    manifest_data["artifact_digest"] = projected_plugin_artifact_digest(projection_dir)
    write_versioned_json(
        manifest_path,
        manifest_data,
        PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        strict_durability=True,
    )


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
    except (OSError, RuntimeError) as exc:
        failures.append(f"hooks rollback failed: {exc}")
    try:
        if original_manifest is None:
            manifest_path.unlink(missing_ok=True)
        else:
            atomic_write(manifest_path, original_manifest, strict_durability=True)
    except (OSError, RuntimeError) as exc:
        failures.append(f"manifest rollback failed: {exc}")
    return tuple(failures)


def _safe_incarnations(root: Path) -> Iterable[Path]:
    """Return the direct, non-hidden directories eligible for hook repair."""
    return (
        path
        for path in root.iterdir()
        if path.is_dir() and not path.is_symlink() and not path.name.startswith(".")
    )


def _hook_repair_needed(
    hooks_json_path: Path,
    *,
    manifest_path: Path,
    incarnation_dir: Path,
) -> bool:
    """Probe whether an unleased hook payload needs relocation or dispatcher repair."""
    if not hooks_json_path.is_file():
        return False
    raw_hooks = hooks_json_path.read_bytes()
    if is_hook_payload_quarantined(manifest_path, raw_hooks):
        return False
    try:
        _relocate_raw_hooks(raw_hooks)
    except ProjectedArtifactHooksInvalid:
        return True
    return bool(find_broken_hook_scripts(hooks_json_path, expansion_root=incarnation_dir))


def _repair_hook_payload_under_lease(
    incarnation_dir: Path,
    *,
    hooks_json_path: Path,
    manifest_path: Path,
    validate_identity: Callable[[], None] | None,
    load_manifest_refresh: Callable[[], object],
    write_manifest_refresh: Callable[[object], None],
    transaction_error_prefix: str,
) -> PluginHookRepairOutcome | None:
    """Reload, admit, write, validate, and roll back one payload under its held lease."""
    raw_hooks = hooks_json_path.read_bytes()
    if is_hook_payload_quarantined(manifest_path, raw_hooks):
        return None
    try:
        fresh = _relocate_raw_hooks(raw_hooks)
    except ProjectedArtifactHooksInvalid as exc:
        quarantine_hook_payload(manifest_path, raw_hooks)
        return PluginHookRepairOutcome(
            incarnation_dir=incarnation_dir,
            status=PluginHookRepairStatus.QUARANTINED,
            detail=str(exc),
        )
    if not find_broken_hook_scripts(hooks_json_path, expansion_root=incarnation_dir):
        return None
    if validate_identity is not None:
        try:
            validate_identity()
        except PluginArtifactValidationError as exc:
            quarantine_hook_payload(manifest_path, raw_hooks)
            return PluginHookRepairOutcome(
                incarnation_dir=incarnation_dir,
                status=PluginHookRepairStatus.QUARANTINED,
                detail=str(exc),
            )
    original_hooks = raw_hooks.decode("utf-8")
    original_manifest = (
        manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else None
    )
    manifest_refresh = load_manifest_refresh()
    try:
        atomic_write(
            hooks_json_path,
            json.dumps(fresh, indent=2) + "\n",
            strict_durability=True,
        )
        write_manifest_refresh(manifest_refresh)
        remaining = find_broken_hook_scripts(
            hooks_json_path,
            expansion_root=incarnation_dir,
        )
        if remaining:
            raise RuntimeError(f"{len(remaining)} broken hook command(s) remain after repair")
    except Exception as exc:
        rollback_failures = _rollback_repair(
            hooks_json_path=hooks_json_path,
            original_hooks=original_hooks,
            manifest_path=manifest_path,
            original_manifest=original_manifest,
        )
        detail = f"{transaction_error_prefix}: {exc}"
        if rollback_failures:
            detail = f"{detail}; {'; '.join(rollback_failures)}"
        raise RuntimeError(detail) from exc
    return PluginHookRepairOutcome(
        incarnation_dir=incarnation_dir,
        status=PluginHookRepairStatus.REPAIRED,
    )


def _repair_hook_incarnation(
    incarnation_dir: Path,
    *,
    manifest_path: Path,
    lease_path: Path,
    validate_identity: Callable[[], None] | None,
    load_manifest_refresh: Callable[[], object],
    write_manifest_refresh: Callable[[object], None],
    transaction_error_prefix: str,
) -> PluginHookRepairOutcome | None:
    """Probe and lease one artifact, converting operational outcomes for its caller."""
    hooks_json_path = incarnation_dir / "hooks" / "hooks.json"
    try:
        if not _hook_repair_needed(
            hooks_json_path,
            manifest_path=manifest_path,
            incarnation_dir=incarnation_dir,
        ):
            return None
        with ArtifactLease.acquire_exclusive(lease_path, timeout=0.0):
            return _repair_hook_payload_under_lease(
                incarnation_dir,
                hooks_json_path=hooks_json_path,
                manifest_path=manifest_path,
                validate_identity=validate_identity,
                load_manifest_refresh=load_manifest_refresh,
                write_manifest_refresh=write_manifest_refresh,
                transaction_error_prefix=transaction_error_prefix,
            )
    except ArtifactLeaseContention:
        return PluginHookRepairOutcome(
            incarnation_dir=incarnation_dir,
            status=PluginHookRepairStatus.CONTENDED,
            detail="lease contended",
        )
    except (OSError, RuntimeError, ValueError, UnicodeDecodeError) as exc:
        return PluginHookRepairOutcome(
            incarnation_dir=incarnation_dir,
            status=PluginHookRepairStatus.FAILED,
            detail=str(exc),
        )


def _repair_hook_incarnations(
    incarnations: Iterable[Path],
    *,
    manifest_path_for: Callable[[Path], Path],
    lease_path_for: Callable[[Path], Path],
    validate_identity_for: Callable[[Path], Callable[[], None] | None],
    load_manifest_refresh_for: Callable[[Path, Path], Callable[[], object]],
    write_manifest_refresh_for: Callable[[Path, Path], Callable[[object], None]],
    transaction_error_prefix: str,
) -> tuple[PluginHookRepairOutcome, ...]:
    """Repair every safely discoverable artifact in deterministic order."""
    outcomes: list[PluginHookRepairOutcome] = []
    for incarnation_dir in sorted(incarnations):
        manifest_path = manifest_path_for(incarnation_dir)
        outcome = _repair_hook_incarnation(
            incarnation_dir,
            manifest_path=manifest_path,
            lease_path=lease_path_for(incarnation_dir),
            validate_identity=validate_identity_for(incarnation_dir),
            load_manifest_refresh=load_manifest_refresh_for(incarnation_dir, manifest_path),
            write_manifest_refresh=write_manifest_refresh_for(incarnation_dir, manifest_path),
            transaction_error_prefix=transaction_error_prefix,
        )
        if outcome is not None:
            outcomes.append(outcome)
    return tuple(outcomes)


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

    def validate_identity(version_dir: Path) -> Callable[[], None]:
        semantic_key = installed_plugin_semantic_key(_AUTOSKILLIT_PLUGIN_KEY, version_dir.name)

        def check() -> None:
            read_installed_plugin_artifact_identity(
                version_dir,
                expected_semantic_key=semantic_key,
            )

        return check

    def load_manifest_refresh(
        version_dir: Path,
        _manifest_path: Path,
    ) -> Callable[[], object]:
        semantic_key = installed_plugin_semantic_key(_AUTOSKILLIT_PLUGIN_KEY, version_dir.name)
        return lambda: semantic_key

    def write_manifest_refresh(
        version_dir: Path,
        _manifest_path: Path,
    ) -> Callable[[object], None]:
        def write(semantic_key: object) -> None:
            write_installed_plugin_artifact_manifest_locked(
                version_dir,
                semantic_key=cast(str, semantic_key),
                action="repair",
            )

        return write

    return _repair_hook_incarnations(
        _safe_incarnations(cache_dir),
        manifest_path_for=installed_plugin_artifact_manifest_path,
        lease_path_for=installed_plugin_artifact_lease_path,
        validate_identity_for=validate_identity,
        load_manifest_refresh_for=load_manifest_refresh,
        write_manifest_refresh_for=write_manifest_refresh,
        transaction_error_prefix="hook repair transaction failed",
    )


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

    def load_manifest_refresh(
        projection_dir: Path,
        manifest_path: Path,
    ) -> Callable[[], object]:
        del projection_dir
        return lambda: _load_projection_hook_manifest(manifest_path)

    def write_manifest_refresh(
        projection_dir: Path,
        manifest_path: Path,
    ) -> Callable[[object], None]:
        def write(manifest_data: object) -> None:
            _write_projection_hook_manifest(
                projection_dir,
                manifest_path,
                cast(dict[str, Any] | None, manifest_data),
            )

        return write

    return _repair_hook_incarnations(
        _safe_incarnations(projections_root),
        manifest_path_for=projected_artifact_manifest_path,
        lease_path_for=projected_artifact_lease_path,
        validate_identity_for=lambda _projection_dir: None,
        load_manifest_refresh_for=load_manifest_refresh,
        write_manifest_refresh_for=write_manifest_refresh,
        transaction_error_prefix="projection hook repair transaction failed",
    )
