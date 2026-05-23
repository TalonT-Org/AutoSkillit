"""Tests for doctor quota cache schema, install classification, version consistency, and drift."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit import cli

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

# ---------------------------------------------------------------------------
# Check 14: Quota cache schema version (#711 Part B, Phase 4)
# ---------------------------------------------------------------------------


class TestCheckQuotaCacheSchema:
    """Tests for _check_quota_cache_schema doctor check."""

    def test_check_quota_cache_schema_ok_when_current(self, tmp_path):
        from autoskillit.cli.doctor import Severity, _check_quota_cache_schema
        from autoskillit.execution import QUOTA_CACHE_SCHEMA_VERSION

        cache = tmp_path / "cache.json"
        cache.write_text(
            json.dumps(
                {"schema_version": QUOTA_CACHE_SCHEMA_VERSION, "fetched_at": "2026-01-01T00:00:00"}
            )
        )
        result = _check_quota_cache_schema(cache_path=cache)
        assert result.severity == Severity.OK
        assert f"v{QUOTA_CACHE_SCHEMA_VERSION}" in result.message

    def test_check_quota_cache_schema_ok_when_missing(self, tmp_path):
        from autoskillit.cli.doctor import Severity, _check_quota_cache_schema

        cache = tmp_path / "nonexistent.json"
        result = _check_quota_cache_schema(cache_path=cache)
        assert result.severity == Severity.OK
        assert "No quota cache" in result.message

    def test_check_quota_cache_schema_warning_when_no_schema_version_key(self, tmp_path):
        from autoskillit.cli.doctor import Severity, _check_quota_cache_schema

        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"fetched_at": "2026-01-01T00:00:00"}))
        result = _check_quota_cache_schema(cache_path=cache)
        assert result.severity == Severity.WARNING
        assert "schema drift" in result.message.lower()

    def test_check_quota_cache_schema_warning_includes_cache_path_and_observed_value(
        self, tmp_path
    ):
        from autoskillit.cli.doctor import Severity, _check_quota_cache_schema

        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"schema_version": 1}))
        result = _check_quota_cache_schema(cache_path=cache)
        assert result.severity == Severity.WARNING
        assert str(cache) in result.message
        assert "observed=1" in result.message


def test_doctor_reports_drift_in_project_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_check_hook_registry_drift must report drift found in project scope."""
    from autoskillit.cli.doctor._doctor_hooks import _check_hook_registry_drift
    from autoskillit.core import Severity

    # Seed a stale pretty_output.py in project scope
    project_settings = tmp_path / ".claude" / "settings.json"
    project_settings.parent.mkdir(parents=True)
    project_settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "mcp__.*autoskillit.*",
                            "hooks": [
                                {"type": "command", "command": "python3 /stale/pretty_output.py"}
                            ],
                        }
                    ]
                }
            }
        )
    )

    result = _check_hook_registry_drift(project_settings, scope_label="project")
    assert result.severity == Severity.ERROR
    assert "[project]" in result.message
    assert "pretty_output.py" in result.message


class TestDoctorInstallClassification:
    """Tests for _check_install_classification doctor check."""

    @pytest.mark.parametrize(
        "revision,expected_fragment",
        [
            ("stable", "stable"),
            ("develop", "develop"),
        ],
    )
    def test_doctor_reports_install_classification_git_vcs(
        self, monkeypatch: pytest.MonkeyPatch, revision: str, expected_fragment: str
    ) -> None:
        from autoskillit.cli.doctor import Severity, _check_install_classification

        fake_direct_url = json.dumps(
            {
                "url": "https://github.com/TalonT-Org/AutoSkillit.git",
                "vcs_info": {
                    "vcs": "git",
                    "requested_revision": revision,
                    "commit_id": "abc123",
                },
            }
        )
        from unittest.mock import MagicMock

        fake_dist = MagicMock()
        fake_dist.read_text.return_value = fake_direct_url
        monkeypatch.setattr(
            "importlib.metadata.Distribution.from_name",
            lambda _name: fake_dist,
        )
        result = _check_install_classification()
        assert result.severity == Severity.OK
        assert expected_fragment in result.message

    def test_doctor_reports_install_classification_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock

        from autoskillit.cli.doctor import Severity, _check_install_classification

        fake_dist = MagicMock()
        fake_dist.read_text.return_value = None
        monkeypatch.setattr(
            "importlib.metadata.Distribution.from_name",
            lambda _name: fake_dist,
        )
        result = _check_install_classification()
        assert result.severity == Severity.WARNING
        assert "could not be detected" in result.message


class TestDoctorUpdateDismissalState:
    """Tests for _check_update_dismissal_state doctor check."""

    def test_doctor_reports_dismissal_state_empty(self, tmp_path: Path) -> None:
        from autoskillit.cli.doctor import Severity, _check_update_dismissal_state

        result = _check_update_dismissal_state(home=tmp_path)
        assert result.severity == Severity.OK
        assert "No active dismissal" in result.message

    def test_doctor_reports_dismissal_state_populated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import UTC, datetime
        from unittest.mock import MagicMock

        from autoskillit.cli.doctor import Severity, _check_update_dismissal_state
        from autoskillit.cli.update._update_checks import _write_dismiss_state

        # Seed state
        dismissed_at = datetime.now(UTC).isoformat()
        _write_dismiss_state(
            tmp_path,
            {
                "update_prompt": {
                    "dismissed_at": dismissed_at,
                    "dismissed_version": "0.7.77",
                    "conditions": ["binary"],
                }
            },
        )

        # Patch detect_install to return stable GIT_VCS
        fake_direct_url = json.dumps(
            {
                "url": "https://github.com/TalonT-Org/AutoSkillit.git",
                "vcs_info": {
                    "vcs": "git",
                    "requested_revision": "stable",
                    "commit_id": "abc123",
                },
            }
        )
        fake_dist = MagicMock()
        fake_dist.read_text.return_value = fake_direct_url
        monkeypatch.setattr(
            "importlib.metadata.Distribution.from_name",
            lambda _name: fake_dist,
        )

        result = _check_update_dismissal_state(home=tmp_path)
        assert result.severity == Severity.OK
        assert "dismissed until" in result.message
        assert "binary" in result.message


class TestDoctorSourceVersionDriftUsesNetwork:
    """Test that source_version_drift now uses network=True."""

    def test_doctor_source_version_drift_uses_network_true(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """_check_source_version_drift must call resolve_reference_sha with network=True."""
        from unittest.mock import MagicMock

        from autoskillit.cli.doctor import _check_source_version_drift

        fake_direct_url = json.dumps(
            {
                "url": "https://github.com/TalonT-Org/AutoSkillit.git",
                "vcs_info": {
                    "vcs": "git",
                    "requested_revision": "stable",
                    "commit_id": "abc123",
                },
            }
        )
        fake_dist = MagicMock()
        fake_dist.read_text.return_value = fake_direct_url
        monkeypatch.setattr(
            "importlib.metadata.Distribution.from_name",
            lambda _name: fake_dist,
        )

        network_args: list[bool] = []
        monkeypatch.setattr(
            "autoskillit.cli.update._update_checks.resolve_reference_sha",
            lambda info, home, **kw: network_args.append(kw.get("network", True)) or None,
        )

        _check_source_version_drift(home=tmp_path)
        assert any(n is True for n in network_args), (
            "_check_source_version_drift must call resolve_reference_sha with network=True"
        )

    def test_check_source_version_drift_returns_ok_when_network_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Network error (resolve_reference_sha returns None) → OK, not hard failure."""
        from unittest.mock import MagicMock

        from autoskillit.cli.doctor import _check_source_version_drift

        fake_direct_url = json.dumps(
            {
                "url": "https://github.com/TalonT-Org/AutoSkillit.git",
                "vcs_info": {
                    "vcs": "git",
                    "requested_revision": "stable",
                    "commit_id": "abc123",
                },
            }
        )
        fake_dist = MagicMock()
        fake_dist.read_text.return_value = fake_direct_url
        monkeypatch.setattr(
            "importlib.metadata.Distribution.from_name",
            lambda _name: fake_dist,
        )
        monkeypatch.setattr(
            "autoskillit.cli.update._update_checks.resolve_reference_sha",
            lambda info, home, **kw: None,
        )

        from autoskillit.cli.doctor import Severity

        result = _check_source_version_drift(home=tmp_path)
        assert result.severity == Severity.OK, (
            f"Expected OK (fail-open) when network unavailable, "
            f"got {result.severity}: {result.message}"
        )
        assert "unavailable" in result.message.lower() or "network" in result.message.lower()


def test_doctor_dual_mcp_registration_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_check_dual_mcp_registration() warns when both direct and marketplace entries exist."""
    from autoskillit.cli.doctor import _check_dual_mcp_registration
    from autoskillit.core import Severity

    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({"mcpServers": {"autoskillit": {"type": "stdio"}}}))
    plugins_dir = tmp_path / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"autoskillit@autoskillit-local": {"name": "autoskillit"}}})
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = _check_dual_mcp_registration()
    assert result.severity == Severity.WARNING
    assert "autoskillit install" in result.message


def test_doctor_no_dual_when_only_direct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_check_dual_mcp_registration() returns OK when only the direct entry exists."""
    from autoskillit.cli.doctor import _check_dual_mcp_registration
    from autoskillit.core import Severity

    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({"mcpServers": {"autoskillit": {"type": "stdio"}}}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = _check_dual_mcp_registration()
    assert result.severity == Severity.OK


def test_check_installed_plugins_entry_real_structure_is_ok(tmp_path: Path) -> None:
    """With the real nested format, the check must report OK."""
    from autoskillit.cli.doctor import _check_installed_plugins_entry
    from autoskillit.core import Severity

    p = tmp_path / "installed_plugins.json"
    p.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {"autoskillit@autoskillit-local": {"name": "autoskillit"}},
            }
        )
    )
    result = _check_installed_plugins_entry(plugins_json_path=p)
    assert result.severity == Severity.OK


def test_check_installed_plugins_entry_flat_structure_is_warning(tmp_path: Path) -> None:
    """A flat structure (wrong format) must not be silently treated as OK."""
    from autoskillit.cli.doctor import _check_installed_plugins_entry
    from autoskillit.core import Severity

    p = tmp_path / "installed_plugins.json"
    p.write_text(json.dumps({"autoskillit@autoskillit-local": {}}))
    result = _check_installed_plugins_entry(plugins_json_path=p)
    assert result.severity == Severity.WARNING


# ---------------------------------------------------------------------------
# T3 — version_consistency reads cache dir, not source tree
# ---------------------------------------------------------------------------


def test_doctor_version_consistency_detects_stale_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    request: pytest.FixtureRequest,
) -> None:
    """Check 5 warns when the CACHED plugin.json version is behind the package."""
    import importlib.metadata

    from autoskillit.version import version_info as _vi

    cache_dir = tmp_path / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
    plugin_json = cache_dir / ".claude-plugin" / "plugin.json"
    plugin_json.parent.mkdir(parents=True)
    plugin_json.write_text('{"version": "0.8.0"}')

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(importlib.metadata, "version", lambda _: "0.9.0")
    _vi.cache_clear()
    request.addfinalizer(_vi.cache_clear)
    cli.doctor_cmd(output_json=True)
    data = json.loads(capsys.readouterr().out)
    vc = next((r for r in data["results"] if r["check"] == "version_consistency"), None)
    assert vc is not None, "version_consistency check not found in doctor results"
    assert vc["severity"] == "warning"
    assert "autoskillit install" in vc["message"]


def test_doctor_version_consistency_ok_when_cache_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    request: pytest.FixtureRequest,
) -> None:
    """Check 5 reports OK when cached plugin.json version matches the package."""
    import importlib.metadata

    from autoskillit.version import version_info as _vi

    cache_dir = tmp_path / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
    plugin_json = cache_dir / ".claude-plugin" / "plugin.json"
    plugin_json.parent.mkdir(parents=True)
    plugin_json.write_text('{"version": "0.9.0"}')

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(importlib.metadata, "version", lambda _: "0.9.0")
    _vi.cache_clear()
    request.addfinalizer(_vi.cache_clear)
    cli.doctor_cmd(output_json=True)
    data = json.loads(capsys.readouterr().out)
    vc = next((r for r in data["results"] if r["check"] == "version_consistency"), None)
    assert vc is not None, "version_consistency check not found in doctor results"
    assert vc["severity"] == "ok"


# ---------------------------------------------------------------------------
# T4 — _check_source_version_drift remediation uses specific upgrade command
# ---------------------------------------------------------------------------


def test_source_version_drift_remediation_contains_upgrade_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_check_source_version_drift WARNING message contains the install-type-specific command."""
    from autoskillit.cli._install_info import InstallInfo, InstallType
    from autoskillit.cli.doctor import _check_source_version_drift
    from autoskillit.core import Severity

    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="aaaa1111bbbb",
        requested_revision="stable",
        url="https://github.com/TalonT-Org/AutoSkillit.git",
        editable_source=None,
    )
    monkeypatch.setattr("autoskillit.cli._install_info.detect_install", lambda: info)
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.resolve_reference_sha",
        lambda *a, **kw: "bbbb2222cccc",
    )
    result = _check_source_version_drift(home=tmp_path)
    assert result.severity == Severity.WARNING
    assert "uv tool upgrade autoskillit" in result.message
    assert "appropriate" not in result.message
