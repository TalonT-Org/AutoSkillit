"""One authority reports every install-state invariant, and doctor believes it.

Two doctor checks returned `OK` on the machine that could not start:
`_check_installed_plugins_entry` never dereferenced `installPath`, and
`_check_plugin_cache_integrity` treated "the cache directory is absent" as
"nothing is broken". So the diagnostic layer affirmatively reassured the user
while `cook` crashed on every launch. There was no failing signal to codify.

`verify_install_state()` is the one place those invariants live, wired into
`doctor`, MCP server startup, and post-install verification so it cannot rot.
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


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def _checks(home: Path) -> set[str]:
    from autoskillit.workspace import verify_install_state

    return {f.check for f in verify_install_state()}


def _queue_registered_retirement(home: Path) -> PluginArtifactIdentity:
    from datetime import UTC, datetime, timedelta

    from autoskillit.cli._plugin_artifact import (
        InstalledPluginArtifactRetirementOwner,
        publish_installed_plugin_artifact,
    )

    live = home / "cache" / "1.0.0"
    live.mkdir(parents=True)
    (live / "plugin.json").write_text('{"name":"autoskillit"}')
    _write_registry(home, live)
    identity = publish_installed_plugin_artifact(
        live,
        semantic_key="autoskillit@autoskillit-local:1.0.0",
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

    def test_dangling_install_path(self, home: Path) -> None:
        _write_registry(home, home / "does" / "not" / "exist")
        assert "installed_plugins_install_path" in _checks(home)

    def test_resolvable_install_path_is_silent(self, home: Path) -> None:
        real = home / "cache" / "1.0.0"
        real.mkdir(parents=True)
        _write_registry(home, real)
        assert "installed_plugins_install_path" not in _checks(home)

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

    def test_installed_plugins_entry_errors_on_a_dangling_path(self, home: Path) -> None:
        from autoskillit.cli.doctor._doctor_mcp import _check_installed_plugins_entry

        _write_registry(home, home / "does" / "not" / "exist")
        result = _check_installed_plugins_entry()
        assert result.severity is Severity.ERROR, (
            "doctor reported OK for a registry entry whose installPath does not exist"
        )

    def test_installed_plugins_entry_is_ok_when_the_path_resolves(self, home: Path) -> None:
        from autoskillit.cli.doctor._doctor_mcp import _check_installed_plugins_entry

        real = home / "cache" / "1.0.0"
        real.mkdir(parents=True)
        _write_registry(home, real)
        assert _check_installed_plugins_entry().severity is Severity.OK

    def test_cache_integrity_is_not_ok_when_the_cache_is_absent(self, home: Path) -> None:
        """`[]` from the validator means "nothing checked", not "nothing broken"."""
        from autoskillit.cli.doctor._doctor_mcp import _check_plugin_cache_integrity

        assert _check_plugin_cache_integrity().severity is not Severity.OK

    def test_install_state_check_surfaces_every_finding(self, home: Path) -> None:
        from autoskillit.cli.doctor._doctor_mcp import _check_install_state_consistency

        _write_registry(home, home / "gone")
        results = _check_install_state_consistency()
        assert results
        assert all(r.severity is Severity.ERROR for r in results)

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
