"""One authority for whether the on-disk install is internally consistent.

Before this module the same question was asked in nine unrelated ad-hoc repairs
(``upgrade()``, ``_ensure_workspace_ready()``, ``evict_direct_mcp_entry()``,
``_evict_stale_autoskillit_hooks()``, cache retirement, …), each with
its own trigger and idempotency story — and two doctor checks answered ``OK`` on
a machine that could not start. Nine one-offs is not a pattern; it is the
absence of one.

Two public entry points:

``verify_install_state()``
    Pure inspection. Returns one structured finding per violated invariant.
    Wired into ``doctor``, MCP server startup, and post-install verification, so
    it cannot rot into a function nobody calls.

``reconcile_install_artifacts()``
    Repair. Consumes ``RETIRED_INSTALL_ARTIFACT_SHAPES`` and removes artifacts
    left in a shape a previous release wrote. This is the *runtime-consuming*
    half of the retired-registry pattern (the model is
    ``RETIRED_SCRIPT_BASENAMES``, not the guard-test-only registries): a
    registry that only guards the repo would not repair a single user's machine.

Layer note (**IL-005**): ``workspace`` is IL-1 and may import only ``core``.
``InstalledPluginsFile`` lives at ``cli/_installed_plugins.py`` (IL-3), so
registry reads here go through ``core._plugin_ids.registered_install_paths``,
the stdlib reader that exists precisely so any layer can ask this question.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (  # IL-005: core only — never cli.InstalledPluginsFile
    DIRECT_INSTALL_CACHE_SUBDIR,
    RETIRED_INSTALL_ARTIFACT_SHAPES,
    PluginArtifactKind,
    RetiringArtifactRecord,
    RetiringCacheState,
    Severity,
    directory_tree_digest,
    get_logger,
    read_retiring_cache,
    read_versioned_json,
    registered_install_paths,
)

__all__ = [
    "InstallStateFinding",
    "marketplace_plugin_root",
    "reconcile_install_artifacts",
    "verify_install_state",
]

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class InstallStateFinding:
    """One violated install-state invariant, named precisely enough to act on."""

    severity: Severity
    check: str
    message: str


def _home() -> Path:
    return Path.home()


def marketplace_plugin_root() -> Path:
    """Return the public marketplace plugin root we materialize at install time."""
    return _home() / ".autoskillit" / "marketplace" / "plugins" / "autoskillit"


def _marketplace_manifest() -> Path:
    return _home() / ".autoskillit" / "marketplace" / ".claude-plugin" / "marketplace.json"


def _plugin_cache_dir() -> Path:
    return _home() / ".claude" / "plugins" / "cache" / DIRECT_INSTALL_CACHE_SUBDIR / "autoskillit"


def _read_json_version(path: Path, *, key: str) -> str | None:
    """Return a version string from a JSON file, or None when unavailable."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if key == "plugins":
        plugins = data.get("plugins") if isinstance(data, dict) else None
        if isinstance(plugins, list) and plugins and isinstance(plugins[0], dict):
            version = plugins[0].get("version")
            return version if isinstance(version, str) else None
        return None
    version = data.get(key) if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def verify_install_state() -> tuple[InstallStateFinding, ...]:
    """Return every violated install-state invariant, most actionable first."""
    from autoskillit import __version__

    findings: list[InstallStateFinding] = []

    # 1. Registry / filesystem agreement. A dangling installPath is what turned a
    #    background sweep two hours after a failed install into a hard crash.
    for install_path in registered_install_paths():
        if not install_path.is_dir():
            findings.append(
                InstallStateFinding(
                    Severity.ERROR,
                    "installed_plugins_install_path",
                    f"installed_plugins.json names a directory that does not exist: "
                    f"{install_path}. Run `autoskillit install` to reinstall the plugin.",
                )
            )

    # 2. Retired artifact shapes still present on disk.
    for key, retired in sorted(RETIRED_INSTALL_ARTIFACT_SHAPES.items()):
        artifact = _home() / key
        if _has_retired_shape(artifact, retired.shape):
            findings.append(
                InstallStateFinding(
                    Severity.ERROR,
                    "retired_install_artifact_shape",
                    f"{artifact} is a {retired.shape}, a shape retired in "
                    f"{retired.retired_in}. Run `autoskillit install` to rebuild it. "
                    f"({retired.reason})",
                )
            )

    # 3. Legacy evidence is visible but never deletion authority. Exact v2
    #    records are errors only when the registered path still validates as
    #    the same incarnation.
    registered = frozenset(registered_install_paths())
    retirement = read_retiring_cache()
    if retirement.state is RetiringCacheState.EXACT_V2:
        for evidence in retirement.legacy_evidence:
            findings.append(
                InstallStateFinding(
                    Severity.WARNING,
                    "retiring_cache_legacy_evidence",
                    f"{evidence.path} is path-only retirement evidence from schema v1. "
                    "It is retained for diagnosis and cannot authorize deletion.",
                )
            )
        for record in retirement.records:
            if (
                record.artifact_kind is PluginArtifactKind.INSTALLED_PLUGIN
                and record.managed_path in registered
                and _record_matches_current_installed_artifact(record)
            ):
                findings.append(
                    InstallStateFinding(
                        Severity.ERROR,
                        "retiring_exact_identity_still_registered",
                        f"{record.managed_path} is queued as exact incarnation "
                        f"{record.incarnation_id} while installed_plugins.json still "
                        "references it. Run `autoskillit install` to reconcile the registry.",
                    )
                )

    # 4. Version agreement, one finding per derived file (see module docstring).
    findings.extend(_derived_version_findings(__version__))

    return tuple(findings)


def _record_matches_current_installed_artifact(
    record: RetiringArtifactRecord,
) -> bool:
    try:
        raw = read_versioned_json(
            record.manifest_path,
            record.manifest_schema_version,
        )
        if raw is None:
            return False
        if (
            raw.get("artifact_kind") != PluginArtifactKind.INSTALLED_PLUGIN.value
            or raw.get("semantic_key") != record.semantic_key
            or raw.get("incarnation_id") != record.incarnation_id
            or raw.get("artifact_digest") != record.artifact_digest
            or raw.get("managed_path") != str(record.managed_path)
            or raw.get("manifest_path") != str(record.manifest_path)
        ):
            return False
        return directory_tree_digest(record.managed_path) == record.artifact_digest
    except (OSError, ValueError):
        return False


def _has_retired_shape(artifact: Path, shape: str) -> bool:
    match shape:
        case "symlink":
            return artifact.is_symlink()
        case "file":
            return artifact.is_file() and not artifact.is_symlink()
        case "directory":
            return artifact.is_dir() and not artifact.is_symlink()
        case _:
            raise ValueError(
                f"unknown retired artifact shape {shape!r} for {artifact} — "
                "RETIRED_INSTALL_ARTIFACT_SHAPES and this reconciler must stay in step"
            )


def _derived_version_findings(package_version: str) -> list[InstallStateFinding]:
    """Report each derived version file that disagrees with the package version."""
    derived: tuple[tuple[str, Path, str], ...] = (
        ("marketplace_manifest_version", _marketplace_manifest(), "plugins"),
        (
            "marketplace_plugin_version",
            marketplace_plugin_root() / ".claude-plugin" / "plugin.json",
            "version",
        ),
        (
            "plugin_cache_version",
            _plugin_cache_dir() / package_version / ".claude-plugin" / "plugin.json",
            "version",
        ),
    )
    findings: list[InstallStateFinding] = []
    for check, path, key in derived:
        if not path.is_file():
            continue
        observed = _read_json_version(path, key=key)
        if observed is not None and observed != package_version:
            findings.append(
                InstallStateFinding(
                    Severity.ERROR,
                    check,
                    f"{path} reports version {observed} but the running package is "
                    f"{package_version}. Run `autoskillit install` to refresh it.",
                )
            )
    return findings


def reconcile_install_artifacts() -> tuple[str, ...]:
    """Remove install artifacts left in a retired shape; return what was repaired.

    Idempotent and safe to call on any machine state, including a clean one.
    Invoked from ``install()`` (before anything reads the artifact) so an
    upgrade from a pre-0.10.892 install repairs itself without the user having
    to ``rm`` anything by hand.
    """
    repaired: list[str] = []
    for key, retired in sorted(RETIRED_INSTALL_ARTIFACT_SHAPES.items()):
        artifact = _home() / key
        if not _has_retired_shape(artifact, retired.shape):
            continue
        try:
            if artifact.is_symlink() or artifact.is_file():
                artifact.unlink()
            else:
                shutil.rmtree(artifact)
        except OSError as exc:
            logger.warning(
                "reconcile_install_artifacts: could not remove %s (%s): %s",
                artifact,
                retired.shape,
                exc,
            )
            continue
        logger.info(
            "reconcile_install_artifacts: removed %s retired in %s",
            artifact,
            retired.retired_in,
        )
        repaired.append(key)
    return tuple(repaired)
