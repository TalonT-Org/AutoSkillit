"""Production-shaped installed-plugin artifact states shared across test layers."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from autoskillit import __version__
from autoskillit.core import (
    INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    ArtifactLease,
    PluginArtifactIdentity,
    directory_tree_digest,
    installed_plugin_artifact_manifest_payload,
    new_plugin_artifact_incarnation_id,
    write_versioned_json,
)
from autoskillit.workspace import InstallStateLeaseMode, InstallStateSpec

DEFAULT_PLUGIN_REF = "autoskillit@autoskillit-local"


class PluginArtifactStateKind(StrEnum):
    """Named states in the installed-artifact integrity matrix."""

    NO_INSTALLATION = "no_installation"
    OLDER_ONLY = "older_only"
    VALID_CURRENT = "valid_current"
    DANGLING_REGISTRY = "dangling_registry"
    MISSING_IDENTITY = "missing_identity"
    MALFORMED_IDENTITY = "malformed_identity"
    WRONG_SEMANTIC_KEY = "wrong_semantic_key"
    WRONG_INCARNATION = "wrong_incarnation"
    WRONG_MANAGED_PATH = "wrong_managed_path"
    VERSION_MISMATCH = "version_mismatch"
    DIGEST_MISMATCH = "digest_mismatch"
    MISSING_LEASE = "missing_lease"
    DANGLING_MANAGED_ROOT = "dangling_managed_root"
    DANGLING_MANIFEST = "dangling_manifest"
    DANGLING_LEASE = "dangling_lease"
    BYTECODE_CONTAMINATED = "bytecode_contaminated"


PLUGIN_ARTIFACT_STATE_KINDS = tuple(PluginArtifactStateKind)
INVALID_PLUGIN_ARTIFACT_STATE_KINDS = tuple(
    kind
    for kind in PluginArtifactStateKind
    if kind
    not in {
        PluginArtifactStateKind.NO_INSTALLATION,
        PluginArtifactStateKind.VALID_CURRENT,
    }
)


@dataclass(frozen=True, slots=True)
class PluginArtifactStateExpectation:
    """Exact verification outcome for one production-shaped artifact state."""

    checks: frozenset[str]
    identity_present: bool
    lease_present: bool


_ARTIFACT_INVALID = frozenset({"installed_plugin_artifact_invalid"})
_LEASE_UNAVAILABLE = frozenset({"installed_plugin_lease_unavailable"})
_STALE_REGISTRY = frozenset(
    {
        "installed_plugins_install_path",
        "installed_plugin_registry_missing",
        "installed_plugin_lease_unavailable",
    }
)

PLUGIN_ARTIFACT_STATE_EXPECTATIONS = {
    PluginArtifactStateKind.NO_INSTALLATION: PluginArtifactStateExpectation(
        frozenset(), False, False
    ),
    PluginArtifactStateKind.OLDER_ONLY: PluginArtifactStateExpectation(
        _STALE_REGISTRY, False, False
    ),
    PluginArtifactStateKind.VALID_CURRENT: PluginArtifactStateExpectation(frozenset(), True, True),
    PluginArtifactStateKind.DANGLING_REGISTRY: PluginArtifactStateExpectation(
        _STALE_REGISTRY, False, False
    ),
    PluginArtifactStateKind.MISSING_IDENTITY: PluginArtifactStateExpectation(
        _ARTIFACT_INVALID, False, False
    ),
    PluginArtifactStateKind.MALFORMED_IDENTITY: PluginArtifactStateExpectation(
        _ARTIFACT_INVALID, False, False
    ),
    PluginArtifactStateKind.WRONG_SEMANTIC_KEY: PluginArtifactStateExpectation(
        _ARTIFACT_INVALID, False, False
    ),
    PluginArtifactStateKind.WRONG_INCARNATION: PluginArtifactStateExpectation(
        _ARTIFACT_INVALID, False, False
    ),
    PluginArtifactStateKind.WRONG_MANAGED_PATH: PluginArtifactStateExpectation(
        _ARTIFACT_INVALID, False, False
    ),
    PluginArtifactStateKind.VERSION_MISMATCH: PluginArtifactStateExpectation(
        _ARTIFACT_INVALID, False, False
    ),
    PluginArtifactStateKind.DIGEST_MISMATCH: PluginArtifactStateExpectation(
        _ARTIFACT_INVALID, False, False
    ),
    PluginArtifactStateKind.MISSING_LEASE: PluginArtifactStateExpectation(
        _LEASE_UNAVAILABLE, False, False
    ),
    PluginArtifactStateKind.DANGLING_MANAGED_ROOT: PluginArtifactStateExpectation(
        _ARTIFACT_INVALID, False, False
    ),
    PluginArtifactStateKind.DANGLING_MANIFEST: PluginArtifactStateExpectation(
        _ARTIFACT_INVALID, False, False
    ),
    PluginArtifactStateKind.DANGLING_LEASE: PluginArtifactStateExpectation(
        _LEASE_UNAVAILABLE, False, False
    ),
    PluginArtifactStateKind.BYTECODE_CONTAMINATED: PluginArtifactStateExpectation(
        _ARTIFACT_INVALID, False, False
    ),
}


@dataclass(frozen=True, slots=True)
class PluginArtifactState:
    """Materialized state plus every trusted path consumed by production."""

    kind: PluginArtifactStateKind
    home: Path
    plugin_ref: str
    expected_version: str
    spec: InstallStateSpec
    managed_root: Path
    manifest_path: Path
    lease_path: Path
    registry_path: Path
    marketplace_manifest_path: Path
    marketplace_plugin_root: Path
    identity: PluginArtifactIdentity | None
    older_root: Path | None = None


def _spec(
    home: Path,
    plugin_ref: str,
    version: str,
    *,
    require_registered_plugin: bool,
) -> InstallStateSpec:
    return InstallStateSpec(
        home=home,
        plugin_ref=plugin_ref,
        expected_version=version,
        require_registered_plugin=require_registered_plugin,
        lease_mode=InstallStateLeaseMode.SHARED,
    )


def _write_registry(spec: InstallStateSpec, install_path: Path) -> Path:
    registry = spec.home / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    spec.plugin_ref: {
                        "installPath": str(install_path),
                        "version": spec.expected_version,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return registry


def _write_marketplace_surfaces(spec: InstallStateSpec) -> tuple[Path, Path]:
    marketplace_manifest = (
        spec.home / ".autoskillit" / "marketplace" / ".claude-plugin" / "marketplace.json"
    )
    marketplace_manifest.parent.mkdir(parents=True, exist_ok=True)
    marketplace_manifest.write_text(
        json.dumps(
            {
                "name": "autoskillit-local",
                "plugins": [
                    {
                        "name": spec.plugin_ref.partition("@")[0],
                        "version": spec.expected_version,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    marketplace_plugin_root = (
        spec.home / ".autoskillit" / "marketplace" / "plugins" / "autoskillit"
    )
    plugin_json = marketplace_plugin_root / ".claude-plugin" / "plugin.json"
    plugin_json.parent.mkdir(parents=True, exist_ok=True)
    plugin_json.write_text(
        json.dumps(
            {
                "name": spec.plugin_ref.partition("@")[0],
                "version": spec.expected_version,
            }
        ),
        encoding="utf-8",
    )
    return marketplace_manifest, marketplace_plugin_root


def _publish_exact(
    spec: InstallStateSpec,
    *,
    plugin_version: str | None = None,
) -> PluginArtifactIdentity:
    metadata = spec.managed_root / ".claude-plugin" / "plugin.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(
        json.dumps(
            {
                "name": spec.plugin_ref.partition("@")[0],
                "version": plugin_version or spec.expected_version,
            }
        ),
        encoding="utf-8",
    )
    identity = PluginArtifactIdentity(
        semantic_key=spec.semantic_key,
        incarnation_id=new_plugin_artifact_incarnation_id(),
        manifest_schema_version=INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        artifact_digest=directory_tree_digest(spec.managed_root),
        managed_path=spec.managed_root,
        manifest_path=spec.manifest_path,
    )
    write_versioned_json(
        spec.manifest_path,
        installed_plugin_artifact_manifest_payload(identity),
        schema_version=INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        strict_durability=True,
    )
    with ArtifactLease.acquire_exclusive(spec.lease_path, blocking=True):
        pass
    return identity


def _rewrite_identity(spec: InstallStateSpec, field: str, value: object) -> None:
    raw = json.loads(spec.manifest_path.read_text(encoding="utf-8"))
    raw[field] = value
    spec.manifest_path.write_text(json.dumps(raw), encoding="utf-8")


def build_plugin_artifact_state(
    home: Path,
    kind: PluginArtifactStateKind | str,
    *,
    plugin_ref: str = DEFAULT_PLUGIN_REF,
    expected_version: str | None = None,
) -> PluginArtifactState:
    """Materialize one isolated production-shaped installed-plugin state."""
    selected = PluginArtifactStateKind(kind)
    version = expected_version or __version__
    required = selected not in {
        PluginArtifactStateKind.NO_INSTALLATION,
        PluginArtifactStateKind.DANGLING_MANAGED_ROOT,
        PluginArtifactStateKind.DANGLING_MANIFEST,
        PluginArtifactStateKind.DANGLING_LEASE,
    }
    spec = _spec(
        Path(home),
        plugin_ref,
        version,
        require_registered_plugin=required,
    )
    registry = spec.home / ".claude" / "plugins" / "installed_plugins.json"
    marketplace_manifest = (
        spec.home / ".autoskillit" / "marketplace" / ".claude-plugin" / "marketplace.json"
    )
    marketplace_plugin_root = (
        spec.home / ".autoskillit" / "marketplace" / "plugins" / "autoskillit"
    )
    identity: PluginArtifactIdentity | None = None
    older_root: Path | None = None

    if selected is not PluginArtifactStateKind.NO_INSTALLATION:
        marketplace_manifest, marketplace_plugin_root = _write_marketplace_surfaces(spec)

    if selected is PluginArtifactStateKind.OLDER_ONLY:
        older_version = f"{version}-older"
        older_spec = _spec(
            spec.home,
            plugin_ref,
            older_version,
            require_registered_plugin=True,
        )
        _publish_exact(older_spec)
        older_root = older_spec.managed_root
        _write_registry(older_spec, older_root)
    elif selected is PluginArtifactStateKind.DANGLING_REGISTRY:
        _write_registry(spec, spec.managed_root.parent / "missing")
    elif selected is PluginArtifactStateKind.DANGLING_MANAGED_ROOT:
        identity = _publish_exact(spec)
        shutil.rmtree(spec.managed_root)
        spec.managed_root.symlink_to(spec.managed_root.parent / "missing-root")
    elif selected is PluginArtifactStateKind.DANGLING_MANIFEST:
        metadata = spec.managed_root / ".claude-plugin" / "plugin.json"
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(
            json.dumps({"name": "autoskillit", "version": version}),
            encoding="utf-8",
        )
        spec.manifest_path.symlink_to(spec.manifest_path.parent / "missing-manifest")
        with ArtifactLease.acquire_exclusive(spec.lease_path, blocking=True):
            pass
    elif selected is PluginArtifactStateKind.DANGLING_LEASE:
        identity = _publish_exact(spec)
        spec.lease_path.unlink()
        spec.lease_path.symlink_to(spec.lease_path.parent / "missing-lease")
    elif selected is not PluginArtifactStateKind.NO_INSTALLATION:
        _write_registry(spec, spec.managed_root)
        if selected is PluginArtifactStateKind.MISSING_IDENTITY:
            metadata = spec.managed_root / ".claude-plugin" / "plugin.json"
            metadata.parent.mkdir(parents=True, exist_ok=True)
            metadata.write_text(
                json.dumps({"name": "autoskillit", "version": version}),
                encoding="utf-8",
            )
            with ArtifactLease.acquire_exclusive(spec.lease_path, blocking=True):
                pass
        elif selected is PluginArtifactStateKind.MALFORMED_IDENTITY:
            identity = _publish_exact(spec)
            spec.manifest_path.write_text("{not-json", encoding="utf-8")
        elif selected is PluginArtifactStateKind.VERSION_MISMATCH:
            identity = _publish_exact(spec, plugin_version=f"{version}-wrong")
        else:
            identity = _publish_exact(spec)
            if selected is PluginArtifactStateKind.WRONG_SEMANTIC_KEY:
                _rewrite_identity(spec, "semantic_key", f"{plugin_ref}:wrong")
            elif selected is PluginArtifactStateKind.WRONG_INCARNATION:
                _rewrite_identity(spec, "incarnation_id", "not-a-canonical-incarnation")
            elif selected is PluginArtifactStateKind.WRONG_MANAGED_PATH:
                _rewrite_identity(
                    spec,
                    "managed_path",
                    str(spec.managed_root.parent / "elsewhere"),
                )
            elif selected is PluginArtifactStateKind.DIGEST_MISMATCH:
                (spec.managed_root / "tampered-content.txt").write_text(
                    "content added after identity publication",
                    encoding="utf-8",
                )
            elif selected is PluginArtifactStateKind.BYTECODE_CONTAMINATED:
                import os
                import subprocess
                import sys

                hooks_dir = spec.managed_root / "hooks"
                if not hooks_dir.is_dir():
                    hooks_dir.mkdir(parents=True)
                # Write a sibling module and a script that imports it —
                # the import triggers __pycache__/*.pyc creation.
                (hooks_dir / "_sibling.py").write_text("VALUE = 1\n")
                runner = hooks_dir / "_contaminant.py"
                runner.write_text("import _sibling\n")
                env = dict(os.environ)
                env.pop("PYTHONDONTWRITEBYTECODE", None)
                env.pop("PYTHONPYCACHEPREFIX", None)
                subprocess.run(
                    [sys.executable, str(runner)],
                    env=env,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            elif selected is PluginArtifactStateKind.MISSING_LEASE:
                spec.lease_path.unlink()

    return PluginArtifactState(
        kind=selected,
        home=spec.home,
        plugin_ref=plugin_ref,
        expected_version=version,
        spec=spec,
        managed_root=spec.managed_root,
        manifest_path=spec.manifest_path,
        lease_path=spec.lease_path,
        registry_path=registry,
        marketplace_manifest_path=marketplace_manifest,
        marketplace_plugin_root=marketplace_plugin_root,
        identity=identity,
        older_root=older_root,
    )
