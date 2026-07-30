"""Production-shaped tests for exact installed-plugin verification authority."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from autoskillit.core import (
    INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    ArtifactLease,
    PluginArtifactIdentity,
    directory_tree_digest,
    installed_plugin_artifact_manifest_payload,
    new_plugin_artifact_incarnation_id,
    write_versioned_json,
)
from autoskillit.workspace import (
    InstallStateSpec,
    verify_installed_plugin_artifact,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]

_PLUGIN_REF = "autoskillit@autoskillit-local"
_VERSION = "1.2.3"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def _spec(
    home: Path,
    *,
    require_registered_plugin: bool = True,
    require_shared_lease: bool = True,
    supplied_lease: ArtifactLease | None = None,
) -> InstallStateSpec:
    return InstallStateSpec(
        home=home,
        plugin_ref=_PLUGIN_REF,
        expected_version=_VERSION,
        require_registered_plugin=require_registered_plugin,
        require_shared_lease=require_shared_lease,
        supplied_lease=supplied_lease,
    )


def _write_registry(home: Path, root: Path) -> None:
    registry = home / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {_PLUGIN_REF: {"installPath": str(root)}},
            }
        ),
        encoding="utf-8",
    )


def _publish_exact(spec: InstallStateSpec) -> PluginArtifactIdentity:
    root = spec.managed_root
    metadata = root / ".claude-plugin" / "plugin.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        json.dumps({"name": "autoskillit", "version": spec.expected_version}),
        encoding="utf-8",
    )
    identity = PluginArtifactIdentity(
        semantic_key=spec.semantic_key,
        incarnation_id=new_plugin_artifact_incarnation_id(),
        manifest_schema_version=INSTALLED_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        artifact_digest=directory_tree_digest(root),
        managed_path=root,
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


def test_spec_is_immutable_and_derives_every_authority_path(home: Path) -> None:
    spec = _spec(home)

    assert spec.managed_root == (
        home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit" / _VERSION
    )
    assert spec.semantic_key == f"{_PLUGIN_REF}:{_VERSION}"
    assert spec.manifest_path == (
        spec.managed_root.parent / f".{_VERSION}.autoskillit-artifact.json"
    )
    assert spec.lease_path == Path(f"{spec.manifest_path}.lock")
    assert "__dict__" not in InstallStateSpec.__slots__
    with pytest.raises(FrozenInstanceError):
        spec.expected_version = "other"  # type: ignore[misc]


def test_uninstalled_optional_state_is_clean_and_does_not_create_a_sidecar(
    home: Path,
) -> None:
    spec = _spec(home, require_registered_plugin=False)

    result = verify_installed_plugin_artifact(spec)

    assert result.identity is None
    assert result.findings == ()
    assert result.lease is None
    assert not spec.lease_path.exists()


def test_registry_path_is_obligation_evidence_never_path_authority(home: Path) -> None:
    spec = _spec(home)
    stale = home / ".claude" / "plugins" / "cache" / "elsewhere" / "stale"
    stale.mkdir(parents=True)
    _write_registry(home, stale)

    result = verify_installed_plugin_artifact(spec)

    checks = {finding.check for finding in result.findings}
    assert "installed_plugins_install_path" in checks
    assert "installed_plugin_registry_missing" in checks
    assert "installed_plugin_lease_unavailable" in checks
    assert not spec.lease_path.exists()
    assert not any(stale == identity.managed_path for identity in [result.identity] if identity)


def test_acquires_existing_shared_lease_and_returns_exact_identity(home: Path) -> None:
    spec = _spec(home)
    expected = _publish_exact(spec)
    _write_registry(home, spec.managed_root)

    result = verify_installed_plugin_artifact(spec)

    assert result.findings == ()
    assert result.identity == expected
    assert result.lease is not None
    assert result.lease.shared
    assert result.lease.path == spec.lease_path
    result.lease.close()


def test_explicit_trusted_home_does_not_fall_back_to_ambient_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_home = tmp_path / "trusted"
    ambient_home = tmp_path / "ambient"
    spec = _spec(trusted_home)
    expected = _publish_exact(spec)
    _write_registry(trusted_home, spec.managed_root)
    monkeypatch.setattr(Path, "home", lambda: ambient_home)

    result = verify_installed_plugin_artifact(spec)

    assert result.findings == ()
    assert result.identity == expected
    assert result.lease is not None
    result.lease.close()


def test_reuses_caller_owned_exclusive_lease_without_closing_it(home: Path) -> None:
    initial = _spec(home)
    expected = _publish_exact(initial)
    _write_registry(home, initial.managed_root)

    with ArtifactLease.acquire_exclusive(initial.lease_path, blocking=True) as lease:
        result = verify_installed_plugin_artifact(
            _spec(
                home,
                require_shared_lease=False,
                supplied_lease=lease,
            )
        )
        assert result.identity == expected
        assert result.findings == ()
        assert result.lease is lease
        assert not lease.closed


def test_install_precommit_can_verify_under_exclusive_lease_before_registration(
    home: Path,
) -> None:
    initial = _spec(home)
    expected = _publish_exact(initial)

    with ArtifactLease.acquire_exclusive(initial.lease_path, blocking=True) as lease:
        result = verify_installed_plugin_artifact(
            _spec(
                home,
                require_registered_plugin=False,
                require_shared_lease=False,
                supplied_lease=lease,
            )
        )
        assert result.identity == expected
        assert result.findings == ()
        assert result.lease is lease
        assert not lease.closed


def test_exclusive_mode_requires_a_supplied_publication_lease(home: Path) -> None:
    initial = _spec(home)
    _publish_exact(initial)
    _write_registry(home, initial.managed_root)

    result = verify_installed_plugin_artifact(_spec(home, require_shared_lease=False))

    assert result.identity is None
    assert {finding.check for finding in result.findings} == {"installed_plugin_lease_required"}
    assert result.lease is None


def test_rejects_wrong_supplied_mode_without_closing_caller_lease(home: Path) -> None:
    spec = _spec(home)
    _publish_exact(spec)
    _write_registry(home, spec.managed_root)

    with ArtifactLease.acquire_exclusive(spec.lease_path, blocking=True) as lease:
        result = verify_installed_plugin_artifact(
            _spec(home, require_shared_lease=True, supplied_lease=lease)
        )
        assert {finding.check for finding in result.findings} == {"installed_plugin_lease_invalid"}
        assert not lease.closed


def test_digest_failure_does_not_close_caller_owned_shared_lease(home: Path) -> None:
    spec = _spec(home)
    _publish_exact(spec)
    _write_registry(home, spec.managed_root)
    shared = ArtifactLease.acquire_existing_shared(spec.lease_path)
    (spec.managed_root / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"autoskillit","version":"corrupt"}',
        encoding="utf-8",
    )
    try:
        result = verify_installed_plugin_artifact(_spec(home, supplied_lease=shared))
        assert result.identity is None
        assert {finding.check for finding in result.findings} == {
            "installed_plugin_artifact_invalid"
        }
        assert result.lease is shared
        assert not shared.closed
    finally:
        shared.close()


def test_closed_or_wrong_path_supplied_lease_is_rejected(home: Path) -> None:
    spec = _spec(home)
    _publish_exact(spec)
    _write_registry(home, spec.managed_root)
    closed = ArtifactLease.acquire_existing_shared(spec.lease_path)
    closed.close()

    closed_result = verify_installed_plugin_artifact(_spec(home, supplied_lease=closed))
    assert {finding.check for finding in closed_result.findings} == {
        "installed_plugin_lease_invalid"
    }

    wrong_path = spec.lease_path.parent / "wrong.lock"
    with ArtifactLease.acquire_exclusive(wrong_path, blocking=True) as wrong:
        wrong_result = verify_installed_plugin_artifact(
            _spec(
                home,
                require_shared_lease=False,
                supplied_lease=wrong,
            )
        )
        assert {finding.check for finding in wrong_result.findings} == {
            "installed_plugin_lease_invalid"
        }
        assert not wrong.closed
