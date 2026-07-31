"""Exact, lease-protected authority for the installed Claude plugin artifact.

The Claude registry is obligation evidence only.  It can tell us that an
installed artifact must exist, but its ``installPath`` is never trusted as path
authority.  The managed root, semantic identity, manifest, and lease sidecar
are all derived from caller-trusted install inputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from autoskillit.core import (
    ArtifactLease,
    PluginArtifactIdentity,
    PluginArtifactUnavailableError,
    PluginArtifactValidationError,
    Severity,
    installed_plugin_artifact_lease_path,
    installed_plugin_artifact_manifest_path,
    installed_plugin_artifact_root,
    installed_plugin_semantic_key,
    parse_installed_plugin_semantic_key,
    read_installed_plugin_artifact_identity,
    registered_install_paths,
)

__all__ = [
    "InstalledArtifactVerification",
    "InstallStateLeaseMode",
    "InstallStateFinding",
    "InstallStateSpec",
    "verify_installed_plugin_artifact",
]

_INSTALLED_PLUGIN_ARTIFACT_UNREADABLE_CHECK = "installed_plugin_artifact_unreadable"


class InstallStateLeaseMode(StrEnum):
    """Lease ownership required while verifying installed artifact identity."""

    SHARED = "shared"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True, slots=True)
class InstallStateFinding:
    """One violated install-state invariant, named precisely enough to act on."""

    severity: Severity
    check: str
    message: str


@dataclass(frozen=True, slots=True)
class InstallStateSpec:
    """Trusted inputs for exact installed-artifact verification.

    ``lease_mode`` is explicit because verification is also used by the install
    transaction while it owns the exclusive publication lease. A missing
    supplied lease may only be satisfied for shared-reader mode: the verifier
    never creates or independently acquires an exclusive sidecar.
    """

    home: Path
    plugin_ref: str
    expected_version: str
    require_registered_plugin: bool
    lease_mode: InstallStateLeaseMode
    supplied_lease: ArtifactLease | None = None

    def __post_init__(self) -> None:
        home = Path(self.home)
        if not home.is_absolute():
            raise ValueError(f"install-state home must be absolute: {home}")
        if not self.plugin_ref:
            raise ValueError("installed plugin reference must not be empty")
        plugin_name = self.plugin_ref.partition("@")[0]
        if not plugin_name or Path(plugin_name).name != plugin_name or plugin_name in {".", ".."}:
            raise ValueError(f"installed plugin name must be one path component: {plugin_name!r}")
        if (
            not self.expected_version
            or Path(self.expected_version).name != self.expected_version
            or self.expected_version in {".", ".."}
        ):
            raise ValueError(
                f"installed plugin version must be one path component: {self.expected_version!r}"
            )
        if type(self.require_registered_plugin) is not bool:
            raise ValueError("require_registered_plugin must be a boolean")
        if not isinstance(self.lease_mode, InstallStateLeaseMode):
            raise ValueError("lease_mode must be an InstallStateLeaseMode")
        object.__setattr__(self, "home", home)

    @classmethod
    def from_managed_root(
        cls,
        managed_root: Path,
        semantic_key: str,
        *,
        require_registered_plugin: bool,
        lease_mode: InstallStateLeaseMode,
        supplied_lease: ArtifactLease | None = None,
    ) -> InstallStateSpec:
        """Reconstruct trusted inputs from a managed root and exact identity."""
        root = Path(managed_root)
        plugin_ref, expected_version = parse_installed_plugin_semantic_key(semantic_key)
        try:
            home = root.parents[5]
        except IndexError as exc:
            raise ValueError(f"installed plugin root is invalid: {root}") from exc
        spec = cls(
            home=home,
            plugin_ref=plugin_ref,
            expected_version=expected_version,
            require_registered_plugin=require_registered_plugin,
            lease_mode=lease_mode,
            supplied_lease=supplied_lease,
        )
        if spec.managed_root != root:
            raise ValueError(f"installed plugin root is invalid: {root}")
        return spec

    @property
    def managed_root(self) -> Path:
        """Return the sole managed root authorized by the trusted inputs."""
        return installed_plugin_artifact_root(
            self.home,
            self.plugin_ref,
            self.expected_version,
        )

    @property
    def semantic_key(self) -> str:
        """Return the exact plugin/version transaction identity."""
        return installed_plugin_semantic_key(self.plugin_ref, self.expected_version)

    @property
    def manifest_path(self) -> Path:
        return installed_plugin_artifact_manifest_path(self.managed_root)

    @property
    def lease_path(self) -> Path:
        return installed_plugin_artifact_lease_path(self.managed_root)


@dataclass(frozen=True, slots=True)
class InstalledArtifactVerification:
    """Exact identity, findings, and any verifier-acquired shared lease."""

    identity: PluginArtifactIdentity | None
    findings: tuple[InstallStateFinding, ...]
    lease: ArtifactLease | None


def _finding(check: str, message: str) -> InstallStateFinding:
    return InstallStateFinding(Severity.ERROR, check, message)


def _registry_findings(
    spec: InstallStateSpec,
    *,
    registered: tuple[Path, ...] | None = None,
) -> tuple[tuple[Path, ...], tuple[InstallStateFinding, ...]]:
    """Report registry disagreement without deriving authority from registry paths."""
    expected = spec.managed_root
    if registered is None:
        registered = registered_install_paths(spec.home)
    findings: list[InstallStateFinding] = []
    for observed in registered:
        if observed == expected:
            continue
        state = "does not exist" if not observed.is_dir() else "is not the current managed root"
        findings.append(
            _finding(
                "installed_plugins_install_path",
                "installed_plugins.json names a directory that "
                f"{state}: {observed}. The expected installed plugin is {expected}. "
                "Run `autoskillit install` to reinstall the plugin.",
            )
        )
    if spec.require_registered_plugin and expected not in registered:
        findings.append(
            _finding(
                "installed_plugin_registry_missing",
                "installed_plugins.json does not register the exact current plugin "
                f"artifact {expected}. Run `autoskillit install` to publish it.",
            )
        )
    return registered, tuple(findings)


def _has_lexical_evidence(path: Path) -> bool:
    """Return whether the exact path names an entry, including a dangling symlink."""
    return path.exists() or path.is_symlink()


def _validate_plugin_metadata_version(spec: InstallStateSpec) -> None:
    metadata_path = spec.managed_root / ".claude-plugin" / "plugin.json"
    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PluginArtifactUnavailableError(
            f"installed plugin metadata cannot be read: {metadata_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise PluginArtifactValidationError(
            f"installed plugin metadata is malformed: {metadata_path}"
        ) from exc
    observed = raw.get("version") if isinstance(raw, dict) else None
    if observed != spec.expected_version:
        raise PluginArtifactValidationError(
            "installed plugin metadata version does not match the trusted "
            f"current version {spec.expected_version!r}: {metadata_path} "
            f"contains {observed!r}"
        )


def _validate_supplied_lease(
    spec: InstallStateSpec,
) -> tuple[ArtifactLease | None, InstallStateFinding | None]:
    lease = spec.supplied_lease
    if lease is None:
        if spec.lease_mode is InstallStateLeaseMode.EXCLUSIVE:
            return None, _finding(
                "installed_plugin_lease_required",
                "exact installed-plugin verification requires the caller's open "
                f"exclusive publication lease for {spec.lease_path}. "
                "Run `autoskillit install` to retry publication.",
            )
        assert spec.lease_mode is InstallStateLeaseMode.SHARED
        try:
            return ArtifactLease.acquire_existing_shared(spec.lease_path), None
        except (OSError, RuntimeError, ValueError) as exc:
            return None, _finding(
                "installed_plugin_lease_unavailable",
                "the installed plugin's existing shared lease sidecar cannot be "
                f"acquired at {spec.lease_path} for the exact artifact "
                f"{spec.managed_root}: {exc}. "
                "Run `autoskillit install` to republish the artifact.",
            )

    if lease.closed:
        problem = "is closed"
    elif lease.path != spec.lease_path:
        problem = f"owns {lease.path} instead of {spec.lease_path}"
    elif spec.lease_mode is InstallStateLeaseMode.SHARED and not lease.shared:
        problem = "does not own the required shared mode"
    elif spec.lease_mode is InstallStateLeaseMode.EXCLUSIVE and lease.shared:
        problem = "does not own the required exclusive mode"
    else:
        return lease, None
    return None, _finding(
        "installed_plugin_lease_invalid",
        f"the supplied installed-plugin lease {problem}. "
        "Run `autoskillit install` to retry with the exact publication lease.",
    )


def verify_installed_plugin_artifact(
    spec: InstallStateSpec,
) -> InstalledArtifactVerification:
    """Verify the exact installed incarnation while holding the required lease.

    A successfully acquired shared lease is returned to the caller so launch
    code can extend it through the child-process lifetime.  A supplied lease is
    borrowed: this function never closes it, including on validation failure.
    """
    findings: list[InstallStateFinding] = []
    preflight_registered: tuple[Path, ...] | None = None
    if spec.supplied_lease is None:
        # This first read is obligation evidence for the no-create fast path,
        # never the registry truth used by a successful verification.
        preflight_registered = registered_install_paths(spec.home)
        artifact_evidence_exists = any(
            _has_lexical_evidence(path)
            for path in (spec.managed_root, spec.manifest_path, spec.lease_path)
        )
        if (
            not preflight_registered
            and not spec.require_registered_plugin
            and not artifact_evidence_exists
        ):
            return InstalledArtifactVerification(None, (), None)

    lease, lease_finding = _validate_supplied_lease(spec)
    if lease_finding is not None:
        if preflight_registered is not None:
            _, registry_findings = _registry_findings(
                spec,
                registered=preflight_registered,
            )
            findings.extend(registry_findings)
        findings.append(lease_finding)
        return InstalledArtifactVerification(None, tuple(findings), None)

    assert lease is not None
    owns_lease = spec.supplied_lease is None
    try:
        # Registry state is mutable publication evidence. Discard the preflight
        # snapshot and inspect it afresh only while the exact lease is held.
        _, registry_findings = _registry_findings(spec)
        findings.extend(registry_findings)
        identity = read_installed_plugin_artifact_identity(
            spec.managed_root,
            expected_semantic_key=spec.semantic_key,
            manifest_path=spec.manifest_path,
        )
        _validate_plugin_metadata_version(spec)
    except PluginArtifactUnavailableError as exc:
        findings.append(
            _finding(
                _INSTALLED_PLUGIN_ARTIFACT_UNREADABLE_CHECK,
                f"the exact installed plugin artifact at {spec.managed_root} cannot "
                f"be read under lease: {exc}. Restore filesystem access, then run "
                "`autoskillit install`.",
            )
        )
        if owns_lease:
            lease.close_preserving(exc)
        return InstalledArtifactVerification(
            None,
            tuple(findings),
            None if owns_lease else lease,
        )
    except PluginArtifactValidationError as exc:
        findings.append(
            _finding(
                "installed_plugin_artifact_invalid",
                f"the exact installed plugin artifact at {spec.managed_root} failed "
                f"identity validation: {exc}. Run `autoskillit install` to republish it.",
            )
        )
        if owns_lease:
            lease.close_preserving(exc)
        return InstalledArtifactVerification(
            None,
            tuple(findings),
            None if owns_lease else lease,
        )
    except BaseException as primary_error:
        if owns_lease:
            lease.close_preserving(primary_error)
        raise

    return InstalledArtifactVerification(identity, tuple(findings), lease)
