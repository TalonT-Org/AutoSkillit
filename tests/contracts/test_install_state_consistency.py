"""One authority reports every install-state invariant, and doctor believes it.

The old doctor registry check and cache-integrity check could each report `OK`
for a machine that could not start. ``verify_install_state()`` now owns the
registry obligation and exact current-artifact decision, wired into doctor,
MCP server startup, and post-install verification so it cannot rot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core import (
    RETIRED_INSTALL_ARTIFACT_SHAPES,
    PluginArtifactIdentity,
    Severity,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_PLUGIN_KEY = "autoskillit@autoskillit-local"


def _write_registry(home: Path, install_path: Path) -> None:
    registry = home / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps({"version": 2, "plugins": {_PLUGIN_KEY: {"installPath": str(install_path)}}})
    )


def _write_marketplace_manifest(home: Path, version: str) -> None:
    manifest = home / ".autoskillit" / "marketplace" / ".claude-plugin" / "marketplace.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"name": "autoskillit-local", "plugins": [{"version": version}]})
    )


def _publish_generation(
    home: Path,
    version: str,
    *,
    plugin_ref: str = _PLUGIN_KEY,
) -> PluginArtifactIdentity:
    """Publish a valid current generation directly at the primitive layer.

    Builds the generation-store shape with the same primitives production
    uses (``generation_artifact_root`` / ``generation_selector_path`` /
    ``write_installed_plugin_artifact_manifest_locked``) rather than calling
    ``workspace.publish_generation()``, which currently raises unconditionally
    (its ``action="publish_generation"`` is not a member of
    ``log_plugin_artifact_lifecycle``'s allowed action set) — a pre-existing
    defect outside this test file's scope.
    """
    from autoskillit.core import (
        generation_artifact_root,
        generation_selector_path,
        new_plugin_artifact_incarnation_id,
    )
    from autoskillit.workspace._installed_artifact import (
        write_installed_plugin_artifact_manifest_locked,
    )

    incarnation_id = new_plugin_artifact_incarnation_id()
    managed_path = generation_artifact_root(home, plugin_ref, version, incarnation_id)
    managed_path.mkdir(parents=True)
    (managed_path / "marker.txt").write_text("content", encoding="utf-8")
    identity = write_installed_plugin_artifact_manifest_locked(
        managed_path,
        semantic_key=f"{plugin_ref}:{version}",
        action="publish",
        incarnation_id=incarnation_id,
    )
    generation_selector_path(home, plugin_ref, version).symlink_to(managed_path)
    return identity


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def _checks(home: Path) -> set[str]:
    from autoskillit.workspace import verify_install_state

    return {f.check for f in verify_install_state()}


def _queue_registered_retirement(home: Path) -> PluginArtifactIdentity:
    from datetime import UTC, datetime, timedelta

    from autoskillit import __version__
    from autoskillit.cli._plugin_artifact import (
        InstalledPluginArtifactRetirementOwner,
        publish_installed_plugin_artifact,
    )

    live = (
        home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit" / __version__
    )
    metadata = live / ".claude-plugin" / "plugin.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"name": "autoskillit", "version": __version__}))
    _write_registry(home, live)
    identity = publish_installed_plugin_artifact(
        live,
        semantic_key=f"autoskillit@autoskillit-local:{__version__}",
    )
    InstalledPluginArtifactRetirementOwner(live.parent).enqueue_retirement(
        identity,
        datetime.now(UTC) + timedelta(hours=6),
    )
    return identity


class TestVerifyInstallState:
    def test_clean_state_reports_nothing(self, home: Path) -> None:
        from autoskillit.workspace import verify_install_state

        assert verify_install_state() == ()

    def test_acquired_lease_closes_when_findings_consumption_fails(
        self,
        home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The exact-artifact lease still closes when consumption raises.

        Post-4.4 ``verify_installed_plugin_artifact`` is no longer called from
        ``verify_install_state()``'s primary path — its sole remaining caller
        inside this module is ``_record_matches_current_installed_artifact``,
        reached while cross-referencing a still-registered retiring record.
        """
        from autoskillit.workspace import _install_state

        _queue_registered_retirement(home)

        class RaisingFindings:
            def __iter__(self):
                raise RuntimeError("findings consumption failed")

        class TrackingLease:
            closed = False

            def close(self) -> None:
                self.closed = True

        lease = TrackingLease()

        class Verification:
            findings = RaisingFindings()

            def __init__(self) -> None:
                self.lease = lease

        monkeypatch.setattr(
            _install_state,
            "verify_installed_plugin_artifact",
            lambda _spec: Verification(),
        )

        with pytest.raises(RuntimeError, match="findings consumption failed"):
            _install_state.verify_install_state()

        assert lease.closed is True

    def test_acquired_lease_closes_after_successful_findings_consumption(
        self,
        home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autoskillit.workspace import _install_state

        _queue_registered_retirement(home)

        class TrackingLease:
            closed = False

            def close(self) -> None:
                self.closed = True

        lease = TrackingLease()

        class Verification:
            findings = ()
            identity = None

            def __init__(self) -> None:
                self.lease = lease

        monkeypatch.setattr(
            _install_state,
            "verify_installed_plugin_artifact",
            lambda _spec: Verification(),
        )

        _install_state.verify_install_state()

        assert lease.closed is True

    def test_generation_store_missing_when_registry_obligates_but_store_absent(
        self, home: Path
    ) -> None:
        """A registered plugin with no published generation is an explicit error.

        The generation store is the primary authority post-4.4; a registry
        obligation with nothing published is exactly the "cannot start" case
        this module exists to catch.
        """
        _write_registry(home, home / "cache" / "wherever")
        assert "generation_store_missing" in _checks(home)

    def test_valid_current_generation_reports_nothing(self, home: Path) -> None:
        from autoskillit import __version__
        from autoskillit.workspace import verify_install_state

        _publish_generation(home, __version__)
        assert verify_install_state() == ()

    def test_generation_artifact_unreadable_is_reported(
        self,
        home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import autoskillit.core._plugin_artifact_identity as plugin_artifact_identity
        from autoskillit import __version__

        _publish_generation(home, __version__)

        def fail_digest(_path: Path) -> str:
            raise PermissionError("injected diagnostic read failure")

        monkeypatch.setattr(plugin_artifact_identity, "directory_tree_digest", fail_digest)

        assert "generation_artifact_unreadable" in _checks(home)

    def test_generation_artifact_invalid_when_content_digest_mismatches(self, home: Path) -> None:
        from autoskillit import __version__

        identity = _publish_generation(home, __version__)
        (identity.managed_path / "tampered.txt").write_text(
            "content added after publication", encoding="utf-8"
        )

        assert "generation_artifact_invalid" in _checks(home)

    def test_generation_incarnation_mismatch_when_manifest_disagrees_with_directory(
        self, home: Path
    ) -> None:
        from autoskillit import __version__
        from autoskillit.core import new_plugin_artifact_incarnation_id

        identity = _publish_generation(home, __version__)
        raw = json.loads(identity.manifest_path.read_text(encoding="utf-8"))
        raw["incarnation_id"] = new_plugin_artifact_incarnation_id()
        identity.manifest_path.write_text(json.dumps(raw), encoding="utf-8")

        # The cross-check in read_installed_plugin_artifact_identity raises
        # PluginArtifactValidationError, which _generation_store_findings
        # reports as generation_artifact_invalid.
        assert "generation_artifact_invalid" in _checks(home)

    def test_dangling_install_path(self, home: Path) -> None:
        _write_registry(home, home / "does" / "not" / "exist")
        assert "generation_store_missing" in _checks(home)

    def test_resolvable_stale_install_path_is_not_path_authority(self, home: Path) -> None:
        """A registered path that resolves to a real directory is still not authority.

        The registry's claimed install path is obligation evidence only — even
        when it names a directory that genuinely exists, the generation store,
        never the registry, determines whether a current incarnation is
        published.
        """
        real = home / "cache" / "1.0.0"
        real.mkdir(parents=True)
        _write_registry(home, real)
        assert "generation_store_missing" in _checks(home)

    def test_current_spec_uses_fresh_metadata_and_live_obligation(
        self,
        home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import autoskillit.workspace._install_state as install_state

        fresh_version = "9.8.7-fresh"
        real_version = install_state.importlib.metadata.version

        def version_for_test(package: str) -> str:
            if package == "autoskillit":
                return fresh_version
            return real_version(package)

        monkeypatch.setattr(
            install_state.importlib.metadata,
            "version",
            version_for_test,
        )

        clean_spec = install_state._current_install_state_spec()
        assert clean_spec.expected_version == fresh_version
        assert clean_spec.require_registered_plugin is False

        _write_registry(home, home / "cache" / "older")
        obligated_spec = install_state._current_install_state_spec()
        assert obligated_spec.expected_version == fresh_version
        assert obligated_spec.require_registered_plugin is True
        assert obligated_spec.managed_root.name == fresh_version

    def test_retired_artifact_shape_still_on_disk(self, home: Path) -> None:
        from tests.cli._upgrade_fixtures import seed_legacy_home

        seed_legacy_home("legacy_symlink", home)
        assert "retired_install_artifact_shape" in _checks(home)

    def test_retiring_entry_the_registry_still_references(self, home: Path) -> None:
        _queue_registered_retirement(home)
        assert "retiring_exact_identity_still_registered" in _checks(home)

    def test_launch_invalid_retiring_manifest_is_not_recognized(self, home: Path) -> None:
        identity = _queue_registered_retirement(home)
        raw = json.loads(identity.manifest_path.read_text(encoding="utf-8"))
        raw["unexpected"] = True
        identity.manifest_path.write_text(json.dumps(raw), encoding="utf-8")

        assert "retiring_exact_identity_still_registered" not in _checks(home)

    def test_unreadable_registered_retirement_is_actionable(
        self,
        home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import autoskillit.core._plugin_artifact_identity as plugin_artifact_identity
        from autoskillit.workspace import verify_install_state

        identity = _queue_registered_retirement(home)

        def fail_digest(_path: Path) -> str:
            raise PermissionError("injected diagnostic read failure")

        monkeypatch.setattr(
            plugin_artifact_identity,
            "directory_tree_digest",
            fail_digest,
        )

        finding = next(
            item for item in verify_install_state() if item.check == "retiring_artifact_unreadable"
        )

        assert finding.severity is Severity.ERROR
        assert str(identity.managed_path) in finding.message
        assert "Restore filesystem access" in finding.message
        assert "autoskillit doctor" in finding.message

    def test_migrated_legacy_evidence_is_a_warning_not_deletion_authority(
        self,
        home: Path,
    ) -> None:
        from autoskillit.core import (
            PluginArtifactKind,
            Severity,
            migrate_retiring_cache_v1,
            write_versioned_json,
        )
        from autoskillit.workspace import verify_install_state

        legacy_path = home / "cache" / "legacy"
        write_versioned_json(
            home / ".autoskillit" / "retiring_cache.json",
            {
                "retiring": [
                    {
                        "version": "legacy",
                        "path": str(legacy_path),
                        "retired_at": "2025-01-01T00:00:00+00:00",
                    }
                ]
            },
            schema_version=1,
        )
        migrate_retiring_cache_v1({PluginArtifactKind.INSTALLED_PLUGIN: home / "cache"})

        finding = next(
            item
            for item in verify_install_state()
            if item.check == "retiring_cache_legacy_evidence"
        )
        assert finding.severity is Severity.WARNING

    def test_corrupt_retirement_cache_is_an_explicit_error(self, home: Path) -> None:
        from autoskillit.workspace import verify_install_state

        cache = home / ".autoskillit" / "retiring_cache.json"
        cache.parent.mkdir(parents=True)
        cache.write_text("{not-json")

        finding = next(
            item for item in verify_install_state() if item.check == "retiring_cache_corrupt"
        )

        assert finding.severity is Severity.ERROR
        assert "retiring_cache.json" in finding.message
        assert "autoskillit install" in finding.message

    def test_future_retirement_cache_schema_is_an_explicit_error(self, home: Path) -> None:
        from autoskillit.workspace import verify_install_state

        cache = home / ".autoskillit" / "retiring_cache.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps({"schema_version": 99, "records": []}))

        finding = next(
            item
            for item in verify_install_state()
            if item.check == "retiring_cache_unsupported_future"
        )

        assert finding.severity is Severity.ERROR
        assert "schema 99" in finding.message
        assert "autoskillit install" in finding.message

    def test_version_drift_names_each_derived_file(self, home: Path) -> None:
        """Three files carry a version and all three are derived.

        The observed machine had package 0.10.894, marketplace.json 0.10.884, and
        the registry pointing at 0.10.883 — and the only version check compared
        the package to the *cached snapshot*, never to the manifest. A single
        ambiguous "version mismatch" is not actionable; each file is named.
        """
        from autoskillit import __version__

        _write_marketplace_manifest(home, "0.0.1-stale")
        plugin_root = home / ".autoskillit" / "marketplace" / "plugins" / "autoskillit"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.2-stale"})
        )

        checks = _checks(home)
        assert "marketplace_manifest_version" in checks
        assert "marketplace_plugin_version" in checks

        _write_marketplace_manifest(home, __version__)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": __version__})
        )
        assert not (_checks(home) & {"marketplace_manifest_version", "marketplace_plugin_version"})

    def test_all_findings_are_actionable_errors(self, home: Path) -> None:
        from autoskillit.workspace import verify_install_state

        _write_registry(home, home / "gone")
        findings = verify_install_state()
        assert findings
        for finding in findings:
            assert finding.severity is Severity.ERROR
            assert "autoskillit install" in finding.message, (
                f"{finding.check} does not tell the operator what to do"
            )


class TestDoctorReportsTheBrokenState:
    """The diagnostic inversion: OK on a machine that cannot start."""

    def test_cache_integrity_is_not_ok_when_the_cache_is_absent(self, home: Path) -> None:
        """`[]` from the validator means "nothing checked", not "nothing broken"."""
        from autoskillit.cli.doctor._doctor_mcp import _check_plugin_cache_integrity

        assert _check_plugin_cache_integrity().severity is not Severity.OK

    def test_install_state_check_surfaces_every_finding(self, home: Path) -> None:
        """Doctor surfaces findings from more than one check, not just the first.

        Combines a registry obligation with nothing published (generation
        store) and a leftover legacy Claude-cache directory (retired
        artifact shape) — two independent invariants — to prove neither
        check silently suppresses the other.
        """
        from autoskillit.cli.doctor._doctor_mcp import _check_install_state_consistency

        _write_registry(home, home / "gone")
        legacy_cache = home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
        legacy_cache.mkdir(parents=True)
        (legacy_cache / "marker.txt").write_text("leftover", encoding="utf-8")

        results = _check_install_state_consistency()
        assert results
        assert all(r.severity is Severity.ERROR for r in results)
        assert {result.check for result in results} >= {
            "install_state:generation_store_missing",
            "install_state:retired_install_artifact_shape",
        }

    def test_install_state_check_uses_fresh_version_not_stale_cache(
        self,
        home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The obligation check names the freshly-read package version.

        The observed machine had package metadata drift from cached snapshots
        of the version string; the check must compare against a live
        ``importlib.metadata.version()`` read, never a stale cached one.
        """
        import autoskillit.workspace._install_state as install_state
        from autoskillit.cli.doctor._doctor_mcp import _check_install_state_consistency

        fresh_version = "9.8.7-fresh"
        monkeypatch.setattr(
            install_state.importlib.metadata,
            "version",
            lambda package: fresh_version if package == "autoskillit" else "",
        )
        _write_registry(home, home / "cache" / "older")

        results = _check_install_state_consistency()
        assert any(fresh_version in result.message for result in results)

    def test_install_state_check_is_ok_on_a_clean_machine(self, home: Path) -> None:
        from autoskillit.cli.doctor._doctor_mcp import _check_install_state_consistency

        results = _check_install_state_consistency()
        assert [r.severity for r in results] == [Severity.OK]


class TestRetiredArtifactShapeRegistry:
    """The double bind: an unregistered shape change must be unmergeable.

    The retired-name pattern works only because *two* tests act as a pincer —
    one fails if the new artifact is undeclared, the other if the old one is
    left live. Either alone is escapable.

    `RETIRED_SCRIPT_BASENAMES` is the model here rather than the skill/agent
    registries, because this one is consumed at **runtime** by the reconciler.
    A registry that only guards the repo would not repair a single user's machine.
    """

    def test_no_key_is_absolute(self) -> None:
        """Keys must be Path.home()-relative.

        The whole suite patches `Path.home` to `tmp_path`; an absolute key would
        need a different value in every test, which is how a registry becomes
        untestable and then wrong.
        """
        absolute = sorted(k for k in RETIRED_INSTALL_ARTIFACT_SHAPES if k.startswith("/"))
        assert not absolute, (
            f"RETIRED_INSTALL_ARTIFACT_SHAPES keys must be home-relative: {absolute}"
        )

    def test_every_entry_is_fully_described(self) -> None:
        for key, retired in RETIRED_INSTALL_ARTIFACT_SHAPES.items():
            assert retired.shape, f"{key}: no shape recorded"
            assert retired.retired_in, f"{key}: no retiring version recorded"
            assert len(retired.reason) > 40, f"{key}: rationale too thin to act on"

    def test_reconciler_handles_every_retired_shape(self, home: Path) -> None:
        """Coverage half: an entry the reconciler cannot handle fails the build."""
        from autoskillit.workspace._install_state import _has_retired_shape

        for key, retired in RETIRED_INSTALL_ARTIFACT_SHAPES.items():
            # Raises ValueError on an unknown shape rather than silently skipping.
            _has_retired_shape(home / key, retired.shape)

    def test_reconciler_rejects_a_shape_it_does_not_know(self, home: Path) -> None:
        """Meta-test: the coverage half actually has teeth."""
        from autoskillit.workspace._install_state import _has_retired_shape

        with pytest.raises(ValueError, match="unknown retired artifact shape"):
            _has_retired_shape(home / "whatever", "hardlink")

    def test_no_retired_shape_survives_reconciliation(self, home: Path) -> None:
        """Retired half: after reconciling, no artifact is left in a retired shape."""
        from autoskillit.workspace import reconcile_install_artifacts, verify_install_state
        from tests.cli._upgrade_fixtures import seed_legacy_home

        seed_legacy_home("legacy_symlink", home)
        repaired = reconcile_install_artifacts()

        assert ".autoskillit/marketplace/plugins/autoskillit" in repaired
        assert "retired_install_artifact_shape" not in {f.check for f in verify_install_state()}

    def test_reconciliation_is_idempotent_on_a_clean_machine(self, home: Path) -> None:
        from autoskillit.workspace import reconcile_install_artifacts

        assert reconcile_install_artifacts() == ()
        assert reconcile_install_artifacts() == ()
