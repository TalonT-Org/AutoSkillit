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

import importlib.metadata
import json
import shutil
from pathlib import Path

from autoskillit.core import (  # IL-005: core only — never cli.InstalledPluginsFile
    DIRECT_INSTALL_CACHE_SUBDIR,
    RETIRED_INSTALL_ARTIFACT_SHAPES,
    PluginArtifactKind,
    PluginArtifactUnavailableError,
    RetiringArtifactRecord,
    RetiringCacheState,
    Severity,
    get_logger,
    read_retiring_cache,
    registered_install_paths,
)
from autoskillit.workspace._installed_artifact import (
    _INSTALLED_PLUGIN_ARTIFACT_UNREADABLE_CHECK,
    InstallStateFinding,
    InstallStateLeaseMode,
    InstallStateSpec,
    verify_installed_plugin_artifact,
)

__all__ = [
    "InstallStateFinding",
    "marketplace_plugin_root",
    "reconcile_install_artifacts",
    "verify_install_state",
]

logger = get_logger(__name__)


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


def _current_install_state_spec() -> InstallStateSpec:
    """Build the diagnostic spec from current metadata and registry evidence."""
    from autoskillit.core import _AUTOSKILLIT_PLUGIN_KEY

    home = _home()
    return InstallStateSpec(
        home=home,
        plugin_ref=_AUTOSKILLIT_PLUGIN_KEY,
        expected_version=importlib.metadata.version("autoskillit"),
        require_registered_plugin=bool(registered_install_paths(home)),
        lease_mode=InstallStateLeaseMode.SHARED,
    )


def verify_install_state() -> tuple[InstallStateFinding, ...]:
    """Return every violated install-state invariant, most actionable first."""
    findings: list[InstallStateFinding] = []

    # 1. Registry obligation and exact current-artifact identity. Registry paths
    #    are evidence only; the shared authority derives the sole managed root
    #    from the trusted home/plugin/version tuple.
    current_spec = _current_install_state_spec()
    exact = verify_installed_plugin_artifact(current_spec)
    findings.extend(exact.findings)
    if exact.lease is not None:
        exact.lease.close()

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
    registered = frozenset(registered_install_paths(_home()))
    retirement = read_retiring_cache()
    if retirement.state is RetiringCacheState.CORRUPT:
        detail = retirement.error or "unknown parse failure"
        findings.append(
            InstallStateFinding(
                Severity.ERROR,
                "retiring_cache_corrupt",
                "retiring_cache.json is corrupt and cannot be interpreted safely: "
                f"{detail}. Run `autoskillit install` to rebuild it.",
            )
        )
    elif retirement.state is RetiringCacheState.UNSUPPORTED_FUTURE:
        findings.append(
            InstallStateFinding(
                Severity.ERROR,
                "retiring_cache_unsupported_future",
                "retiring_cache.json uses unsupported schema "
                f"{retirement.schema_version}; this version cannot determine deletion "
                "authority safely. Run `autoskillit install` with a compatible version.",
            )
        )
    elif retirement.state is RetiringCacheState.EXACT_V2:
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
            if record.artifact_kind is not PluginArtifactKind.INSTALLED_PLUGIN:
                continue
            if record.managed_path not in registered:
                continue
            try:
                matches_current = _record_matches_current_installed_artifact(record)
            except PluginArtifactUnavailableError as exc:
                findings.append(
                    InstallStateFinding(
                        Severity.ERROR,
                        "retiring_artifact_unreadable",
                        f"{record.managed_path} is queued and still registered, but its exact "
                        f"identity is temporarily unreadable: {exc}. Restore filesystem "
                        "access, then rerun `autoskillit doctor` before installation.",
                    )
                )
                continue
            if matches_current:
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
    findings.extend(_derived_version_findings(current_spec.expected_version))

    return tuple(findings)


def _record_matches_current_installed_artifact(
    record: RetiringArtifactRecord,
) -> bool:
    from autoskillit.core import _AUTOSKILLIT_PLUGIN_KEY

    try:
        spec = InstallStateSpec.from_managed_root(
            record.managed_path,
            record.semantic_key,
            require_registered_plugin=False,
            lease_mode=InstallStateLeaseMode.SHARED,
        )
    except ValueError:
        return False
    if spec.plugin_ref != _AUTOSKILLIT_PLUGIN_KEY:
        return False
    verification = verify_installed_plugin_artifact(spec)
    try:
        unreadable = next(
            (
                finding
                for finding in verification.findings
                if finding.check == _INSTALLED_PLUGIN_ARTIFACT_UNREADABLE_CHECK
            ),
            None,
        )
        if unreadable is not None:
            raise PluginArtifactUnavailableError(unreadable.message)
        return not verification.findings and verification.identity == record.identity
    finally:
        if verification.lease is not None:
            verification.lease.close()


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
