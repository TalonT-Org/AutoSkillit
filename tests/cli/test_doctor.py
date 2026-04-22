"""Tests for CLI doctor command and related utilities."""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit import cli

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

_MINIMAL_SCRIPT_YAML = """\
name: my-script
description: A test script
steps:
  do_it:
    tool: run_cmd
    with:
      cmd: echo hello
    on_success: done
  done:
    action: stop
    message: Done
kitchen_rules:
  - Only use AutoSkillit MCP tools during pipeline execution
"""


class TestCLIDoctor:
    # --- T7: doctor ---

    def test_doctor_warns_dead_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor warns about MCP servers with nonexistent command binaries."""
        fake_claude_json = tmp_path / ".claude.json"
        fake_claude_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "old-server": {
                            "type": "stdio",
                            "command": "/nonexistent/path/to/old-server",
                        },
                        "autoskillit": {"type": "stdio", "command": "autoskillit"},
                    }
                }
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor()
        captured = capsys.readouterr()
        assert "old-server" in captured.out
        assert "ERROR" in captured.out

    def test_doctor_ignores_healthy_coregistered_servers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor ignores legitimate co-registered MCP servers (no standalone autoskillit)."""
        fake_claude_json = tmp_path / ".claude.json"
        fake_bin = tmp_path / "legit-server"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        fake_claude_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "other-server": {"type": "stdio", "command": str(fake_bin)},
                    }
                }
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".autoskillit").mkdir(exist_ok=True)
        (tmp_path / ".autoskillit" / "config.yaml").write_text(
            "test_check:\n  command: ['pytest']\n"
        )
        from autoskillit.core import _AUTOSKILLIT_GITIGNORE_ENTRIES

        (tmp_path / ".autoskillit" / ".gitignore").write_text(
            "\n".join(_AUTOSKILLIT_GITIGNORE_ENTRIES) + "\n"
        )
        (tmp_path / ".pre-commit-config.yaml").write_text(
            "repos:\n  - repo: dummy\n    hooks:\n      - id: gitleaks\n"
        )
        # Create plugin cache directory for Check 2c
        (tmp_path / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit").mkdir(
            parents=True, exist_ok=True
        )
        # Create installed_plugins.json for Check 2d
        (tmp_path / ".claude" / "plugins" / "installed_plugins.json").write_text(
            json.dumps({"version": 2, "plugins": {"autoskillit@autoskillit-local": {}}})
        )
        # Register hooks so hook_registration check passes
        # Use explicit path (tmp_path already monkeypatched as Path.home())
        from autoskillit.cli._hooks import (
            _evict_stale_autoskillit_hooks,
            sync_hooks_to_settings,
        )

        settings_path = tmp_path / ".claude" / "settings.json"
        _evict_stale_autoskillit_hooks(settings_path)
        sync_hooks_to_settings(settings_path)
        # Franchise checks: set SESSION_TYPE to a non-triggering value so ambient
        # checks 18-20 all return OK, and stub check 23 directly so it returns OK
        # without touching canonical_script_basenames (shared with hook-registration check 4).
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "worker")
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        from autoskillit.cli._doctor import DoctorResult
        from autoskillit.core import Severity

        monkeypatch.setattr(
            "autoskillit.cli._doctor._check_franchise_dispatch_guard_registered",
            lambda: DoctorResult(Severity.OK, "franchise_dispatch_guard_registered", "stubbed"),
        )
        local_bin = str(tmp_path / ".local" / "bin" / "autoskillit")
        with (
            patch(
                "autoskillit.cli.shutil.which",
                side_effect=lambda cmd: local_bin if cmd == "autoskillit" else shutil.which(cmd),
            ),
            patch(
                "subprocess.run",
                return_value=type("R", (), {"returncode": 0, "stdout": local_bin})(),
            ),
        ):
            cli.doctor()
        captured = capsys.readouterr()
        assert "WARNING" not in captured.out
        assert "ERROR" not in captured.out

    def test_doctor_warns_missing_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor warns when no project config exists."""
        fake_claude_json = tmp_path / ".claude.json"
        fake_claude_json.write_text(
            json.dumps(
                {"mcpServers": {"autoskillit": {"type": "stdio", "command": "autoskillit"}}}
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor()
        captured = capsys.readouterr()
        assert "No project config" in captured.out

    def test_doctor_json_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor --json outputs structured results JSON."""
        fake_claude_json = tmp_path / ".claude.json"
        fake_claude_json.write_text(
            json.dumps(
                {"mcpServers": {"autoskillit": {"type": "stdio", "command": "autoskillit"}}}
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "results" in data
        assert isinstance(data["results"], list)
        for entry in data["results"]:
            assert "severity" in entry
            assert "check" in entry
            assert "message" in entry

    def test_doctor_result_has_severity_tiers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor JSON output uses ok/warning/error severity tiers."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        severities = {r["severity"] for r in data["results"]}
        assert severities <= {"ok", "warning", "error", "info"}

    def test_doctor_info_severity_not_treated_as_problem(self) -> None:
        """INFO findings must not appear in the problems section."""
        from autoskillit.cli._doctor import _NON_PROBLEM
        from autoskillit.core import Severity

        assert Severity.INFO in _NON_PROBLEM, "INFO must be in _NON_PROBLEM"
        assert Severity.OK in _NON_PROBLEM, "OK must be in _NON_PROBLEM"
        assert Severity.ERROR not in _NON_PROBLEM, "ERROR must not be in _NON_PROBLEM"
        assert Severity.WARNING not in _NON_PROBLEM, "WARNING must not be in _NON_PROBLEM"

    def test_doctor_passes_when_versions_match(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports ok when plugin.json version matches package."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        version_checks = [r for r in data["results"] if r["check"] == "version_consistency"]
        assert len(version_checks) == 1
        assert version_checks[0]["severity"] == "ok"

    def test_doctor_json_output_includes_all_checks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor JSON includes entries for all core check names."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        check_names = {r["check"] for r in data["results"]}
        expected = {
            "stale_mcp_servers",
            "mcp_server_registered",
            "autoskillit_on_path",
            "project_config",
            "version_consistency",
            "hook_health",
            "hook_registration",
            "hook_registry_drift",
            "script_version_health",
            "gitignore_completeness",
            "secret_scanning_hook",
            "editable_install_source_exists",  # ★ new
            "stale_entry_points",  # ★ new
            "dual_mcp_registration",  # ★ new
            "plugin_cache_exists",
            "installed_plugins_entry",
            "ambient_session_type_leaf",
            "ambient_session_type_orchestrator",
            "ambient_session_type_franchise",
            "ambient_campaign_id",
            "feature_dependencies",
            "feature_registry_consistency",
            "sous_chef_bundled",
            "franchise_dispatch_guard_registered",
            "stale_franchise_state",
            "campaign_onboarding_hint",
            "campaign_manifest_clone_dests",
        }
        assert expected <= check_names

    def test_doctor_human_output_shows_severity(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """doctor human output includes severity prefixes for problems."""
        # Trigger an error via a dead binary MCP server
        fake_claude_json = tmp_path / ".claude.json"
        fake_claude_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "dead-server": {
                            "type": "stdio",
                            "command": "/nonexistent/dead-binary",
                        },
                        "autoskillit": {"type": "stdio", "command": "autoskillit"},
                    }
                }
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor()
        captured = capsys.readouterr()
        assert "ERROR:" in captured.out

    # DOC-REG-1
    def test_doctor_includes_mcp_server_registered_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor run_doctor() results include mcp_server_registered check."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        check_names = {r["check"] for r in data["results"]}
        assert "mcp_server_registered" in check_names

    # DOC-REG-2
    def test_doctor_includes_hook_registration_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor run_doctor() results include hook_registration check."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        check_names = {r["check"] for r in data["results"]}
        assert "hook_registration" in check_names

    # DOC-REG-3
    def test_doctor_marketplace_freshness_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """marketplace_freshness does NOT appear in doctor results."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        check_names = {r["check"] for r in data["results"]}
        assert "marketplace_freshness" not in check_names

    # DOC-REG-4
    def test_doctor_plugin_metadata_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """plugin_metadata does NOT appear in doctor results."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        check_names = {r["check"] for r in data["results"]}
        assert "plugin_metadata" not in check_names

    # DOC-REG-5
    def test_doctor_duplicate_mcp_server_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """duplicate_mcp_server does NOT appear in doctor results."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        check_names = {r["check"] for r in data["results"]}
        assert "duplicate_mcp_server" not in check_names

    # DOC-REG-6
    def test_doctor_mcp_server_registered_warns_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """mcp_server_registered returns warning when autoskillit absent from ~/.claude.json."""
        import subprocess

        # ~/.claude.json does not exist in tmp_path (no file created)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        # Simulate `claude plugin list` returning non-zero so check falls through to WARNING
        class _NoPlugin:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _NoPlugin())
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        mcp_checks = [r for r in data["results"] if r["check"] == "mcp_server_registered"]
        assert len(mcp_checks) == 1
        assert mcp_checks[0]["severity"] == "warning"

    # DOC-REG-7
    def test_doctor_hook_registration_warns_when_scripts_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """hook_registration returns warning when a HOOK_REGISTRY script is absent."""
        # settings.json does not exist — all hooks missing
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        hook_checks = [r for r in data["results"] if r["check"] == "hook_registration"]
        assert len(hook_checks) == 1
        assert hook_checks[0]["severity"] == "warning"

    # DOC-REG-8
    def test_doctor_json_output_includes_new_checks_not_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Doctor JSON output includes new checks but excludes the three removed checks."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        check_names = {r["check"] for r in data["results"]}
        assert "mcp_server_registered" in check_names
        assert "hook_registration" in check_names
        assert "marketplace_freshness" not in check_names
        assert "plugin_metadata" not in check_names
        assert "duplicate_mcp_server" not in check_names


class TestDoctorScriptHealth:
    """Doctor check for script version staleness."""

    # DOC1: No .autoskillit/recipes/ -> OK result
    def test_no_scripts_dir_reports_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports OK for script_version_health when no scripts directory exists."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        # No .autoskillit/recipes/ directory created
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "ok"

    # DOC2: All scripts at current version -> OK result
    def test_all_scripts_at_current_version_reports_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports OK when all scripts carry the current installed version."""
        import autoskillit

        current_version = autoskillit.__version__
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "up-to-date.yaml").write_text(
            f"name: up-to-date\ndescription: Current version\n"
            f'autoskillit_version: "{current_version}"\n'
            + _MINIMAL_SCRIPT_YAML.split("\n", 2)[2]  # reuse steps/constraints block
        )
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "ok"

    # DOC3: Scripts below current version -> WARNING result with recipe names
    def test_outdated_scripts_reports_warning_with_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports WARNING with recipe names when scripts have an older version."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "old-script.yaml").write_text(
            'name: old-script\ndescription: Old\nautoskillit_version: "0.1.0"\n'
        )
        (scripts_dir / "also-old.yaml").write_text(
            'name: also-old\ndescription: Also old\nautoskillit_version: "0.1.0"\n'
        )
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "warning"
        assert "old-script" in script_checks[0]["message"]

    # DOC4: Scripts with no version field -> WARNING result
    def test_scripts_without_version_reports_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports WARNING when script YAML has no autoskillit_version field."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "no-version.yaml").write_text(
            "name: no-version\ndescription: No version field\n"
        )
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "warning"

    def _setup_recipe(self, scripts_dir: Path, name: str, version: str = "0.1.0") -> None:
        (scripts_dir / f"{name}.yaml").write_text(
            f'name: {name}\ndescription: Test\nautoskillit_version: "{version}"\n'
        )

    def _write_failures_json(self, tmp_path: Path, name: str, retries: int = 3) -> None:
        import json as _json

        failures_path = tmp_path / ".autoskillit" / "temp" / "migrations" / "failures.json"
        failures_path.parent.mkdir(parents=True, exist_ok=True)
        failures_path.write_text(
            _json.dumps(
                {
                    name: {
                        "name": name,
                        "file_path": f"/fake/{name}.yaml",
                        "file_type": "recipe",
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "error": "validation failed after retries",
                        "retries_attempted": retries,
                    }
                }
            )
        )

    # DR1: failures.json has an entry for a recipe -> error severity
    def test_doctor_error_on_failed_migration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports error severity when failures.json has an entry for a recipe."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        self._setup_recipe(scripts_dir, "broken-pipeline")
        self._write_failures_json(tmp_path, "broken-pipeline", retries=3)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "error"

    # DR2: Error message includes retries_attempted value from failure record
    def test_doctor_error_message_includes_retry_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor error message includes retries_attempted value from the failure record."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        self._setup_recipe(scripts_dir, "my-pipeline")
        self._write_failures_json(tmp_path, "my-pipeline", retries=3)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert "3" in script_checks[0]["message"]

    # DR3: Outdated recipe with no failure record -> warning severity
    def test_doctor_warning_on_simply_outdated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports warning when recipe is outdated but has no failure record."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        self._setup_recipe(scripts_dir, "outdated-pipeline")
        # No failures.json written
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "warning"

    # DR4: All recipes current, no failures.json -> ok
    def test_doctor_ok_when_all_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports ok when all recipes are at current version and no failures.json."""
        import autoskillit

        current_version = autoskillit.__version__
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        self._setup_recipe(scripts_dir, "current-pipeline", version=current_version)
        # No failures.json written
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "ok"

    # DR5: Warning message says "Will be auto-migrated on next load"
    def test_doctor_outdated_message_updated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor warning message says 'Will be auto-migrated on next load'."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        self._setup_recipe(scripts_dir, "stale-pipeline")
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "warning"
        assert "Will be auto-migrated on next load" in script_checks[0]["message"]


class TestSyncRemovalCLI:
    def test_doctor_has_no_recipe_sync_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ):
        """REQ-APP-006: doctor output does not include recipe_sync_status."""
        monkeypatch.chdir(tmp_path)
        cli.doctor()
        captured = capsys.readouterr()
        assert "recipe_sync_status" not in captured.out


class TestGroupFDoctor:
    """P8-2, P3-2: CLI refactoring — doctor delegation tests from TestGroupFRefactoring."""

    def test_doctor_delegates_to_doctor_module(self, monkeypatch, capsys):
        """cli.doctor() must delegate to cli._doctor.run_doctor(), not contain the logic itself."""
        from autoskillit.cli import _doctor

        called_with: dict = {}

        def mock_run_doctor(*, output_json: bool = False) -> None:
            called_with["output_json"] = output_json

        monkeypatch.setattr(_doctor, "run_doctor", mock_run_doctor)
        cli.doctor(output_json=True)
        assert called_with == {"output_json": True}

    def test_severity_and_doctorresult_in_doctor_module(self):
        """Severity and DoctorResult must be importable from autoskillit.cli._doctor."""
        from autoskillit.cli._doctor import DoctorResult, Severity

        r = DoctorResult(severity=Severity.OK, check="test", message="ok")
        assert r.severity == Severity.OK
        assert r.check == "test"


def test_doctor_fix_parameter_does_not_exist():
    """The doctor --fix no-op flag must be removed from the CLI."""
    import inspect

    from autoskillit import cli

    sig = inspect.signature(cli.doctor)
    assert "fix" not in sig.parameters, "doctor --fix is a silent no-op and must be removed"


def test_doctor_does_not_modify_plugin_state(tmp_path, monkeypatch, capsys):
    """Doctor must not delete the plugin cache or modify installed_plugins.json."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    cache_dir = tmp_path / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
    cache_dir.mkdir(parents=True)
    (cache_dir / "0.3.0" / "hooks").mkdir(parents=True)
    (cache_dir / "0.3.0" / "hooks" / "pretty_output_hook.py").write_text("# cached")

    plugins_json = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    plugins_json.write_text(
        json.dumps(
            {"version": 2, "plugins": {"autoskillit@autoskillit-local": {"version": "0.8.25"}}}
        )
    )

    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    retiring_json.parent.mkdir(parents=True, exist_ok=True)
    retiring_content = json.dumps(
        {
            "retiring": [
                {
                    "version": "0.3.0",
                    "path": str(cache_dir / "0.3.0"),
                    "retired_at": "2026-01-01T00:00:00+00:00",
                }
            ],
            "schema_version": 1,
        }
    )
    retiring_json.write_text(retiring_content)

    cli.doctor()

    assert cache_dir.exists(), "Doctor must not delete the plugin cache directory"
    data = json.loads(plugins_json.read_text())
    assert "autoskillit@autoskillit-local" in data.get("plugins", {}), (
        "Doctor must not remove installed_plugins.json entries"
    )
    assert retiring_json.read_text() == retiring_content, (
        "Doctor must not modify retiring_cache.json"
    )


def test_doctor_checks_plugin_cache_exists(tmp_path, monkeypatch, capsys):
    """Doctor must report when the plugin cache directory is missing."""
    from autoskillit.cli._install_info import InstallInfo, InstallType

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    # Force non-editable install so the cache check actually runs
    monkeypatch.setattr(
        "autoskillit.cli._install_info.detect_install",
        lambda: InstallInfo(
            install_type=InstallType.GIT_VCS,
            commit_id=None,
            requested_revision=None,
            url=None,
            editable_source=None,
        ),
    )
    cli.doctor(output_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    checks = [r for r in data["results"] if r["check"] == "plugin_cache_exists"]
    assert len(checks) == 1, "Expected a plugin_cache_exists check"
    assert checks[0]["severity"] in ("warning", "error")


def test_doctor_checks_installed_plugins_entry(tmp_path, monkeypatch, capsys):
    """Doctor must report when installed_plugins.json is missing the autoskillit entry."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    # Create installed_plugins.json without the autoskillit entry
    plugins_dir = tmp_path / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "installed_plugins.json").write_text("{}")
    cli.doctor(output_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    checks = [r for r in data["results"] if r["check"] == "installed_plugins_entry"]
    assert len(checks) == 1, "Expected an installed_plugins_entry check"
    assert checks[0]["severity"] in ("warning", "error")


def test_stale_gate_check_absent_from_doctor_output(tmp_path, monkeypatch, capsys):
    """Doctor must not report a stale_gate_file check."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    from autoskillit import cli

    cli.doctor(output_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    check_names = {r["check"] for r in data["results"]}
    assert "stale_gate_file" not in check_names


def test_doctor_detects_plugin_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    """doctor must not report MCP unregistered when autoskillit is installed as a plugin."""
    import json as _json
    import subprocess
    import tempfile

    from autoskillit.cli._doctor import _check_mcp_server_registered
    from autoskillit.core import Severity

    fake_claude_json_content = _json.dumps({"mcpServers": {}})  # No mcpServers entry

    class FakeResult:
        stdout = "autoskillit  0.4.0  active\n"
        returncode = 0

    def fake_plugin_list(*args: object, **kwargs: object) -> FakeResult:
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_plugin_list)

    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        f.write(fake_claude_json_content)
        tmpf = Path(f.name)

    try:
        result = _check_mcp_server_registered(claude_json_path=tmpf)
        assert result.severity == Severity.OK, (
            "doctor must recognize plugin-based registration; "
            "not just mcpServers presence (REQ-ONB-002)"
        )
    finally:
        tmpf.unlink(missing_ok=True)


def test_doctor_warns_on_missing_gitignore_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doctor must WARN when .autoskillit/.gitignore is missing entries."""
    autoskillit_dir = tmp_path / ".autoskillit"
    autoskillit_dir.mkdir()
    (autoskillit_dir / ".gitignore").write_text("temp/\n")
    (autoskillit_dir / ".secrets.yaml").write_text("github:\n  token: ''\n")

    monkeypatch.chdir(tmp_path)
    from autoskillit.cli._doctor import _check_gitignore_completeness
    from autoskillit.core import Severity

    result = _check_gitignore_completeness(tmp_path)
    assert result.severity == Severity.WARNING
    assert ".secrets.yaml" in result.message


def test_doctor_gitignore_ok_when_all_covered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doctor must report OK when all .autoskillit/ files are covered.

    Root ``.gitignore`` is no longer mutated by autoskillit (self-gitignoring
    temp dir pattern), so the doctor only checks ``.autoskillit/.gitignore``.
    """
    from autoskillit.core.io import _AUTOSKILLIT_GITIGNORE_ENTRIES

    autoskillit_dir = tmp_path / ".autoskillit"
    autoskillit_dir.mkdir()
    (autoskillit_dir / ".gitignore").write_text("\n".join(_AUTOSKILLIT_GITIGNORE_ENTRIES) + "\n")
    (autoskillit_dir / ".secrets.yaml").write_text("github:\n  token: ''\n")

    monkeypatch.chdir(tmp_path)
    from autoskillit.cli._doctor import _check_gitignore_completeness
    from autoskillit.core import Severity

    result = _check_gitignore_completeness(tmp_path)
    assert result.severity == Severity.OK


# SS-DOC-1
def test_doctor_includes_secret_scanning_hook_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """doctor output includes the secret_scanning_hook check."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    cli.doctor(output_json=True)
    data = json.loads(capsys.readouterr().out)
    check_names = {r["check"] for r in data["results"]}
    assert "secret_scanning_hook" in check_names


# SS-DOC-2
def test_doctor_error_when_no_scanner_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """doctor reports ERROR severity for secret_scanning_hook when no scanner found."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    # No .pre-commit-config.yaml
    cli.doctor(output_json=True)
    data = json.loads(capsys.readouterr().out)
    checks = [r for r in data["results"] if r["check"] == "secret_scanning_hook"]
    assert len(checks) == 1
    assert checks[0]["severity"] == "error"


# SS-DOC-3
def test_doctor_ok_when_scanner_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """doctor reports OK for secret_scanning_hook when a known scanner is configured."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: https://github.com/gitleaks/gitleaks\n"
        "    hooks:\n      - id: gitleaks\n"
    )
    cli.doctor(output_json=True)
    data = json.loads(capsys.readouterr().out)
    checks = [r for r in data["results"] if r["check"] == "secret_scanning_hook"]
    assert len(checks) == 1
    assert checks[0]["severity"] == "ok"


# SS-DOC-4 (unit test for check function directly)
def test_check_secret_scanning_hook_ok_with_gitleaks(tmp_path: Path) -> None:
    """_check_secret_scanning_hook returns OK when gitleaks hook is present."""
    from autoskillit.cli._doctor import _check_secret_scanning_hook
    from autoskillit.core import Severity

    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: dummy\n    hooks:\n      - id: gitleaks\n"
    )
    result = _check_secret_scanning_hook(tmp_path)
    assert result.severity == Severity.OK


# SS-DOC-5 (unit test for check function directly)
def test_check_secret_scanning_hook_error_without_scanner(tmp_path: Path) -> None:
    """_check_secret_scanning_hook returns ERROR when no .pre-commit-config.yaml."""
    from autoskillit.cli._doctor import _check_secret_scanning_hook
    from autoskillit.core import Severity

    result = _check_secret_scanning_hook(tmp_path)
    assert result.severity == Severity.ERROR


# DR-SECRETS-1
def test_doctor_detects_misplaced_token_in_project_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DR-SECRETS-1: Doctor reports ERROR when github.token is in project config.yaml.

    home has no config so the function must detect the violation via the project path.
    """
    from autoskillit.cli._doctor import _check_config_layers_for_secrets
    from autoskillit.core import Severity

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    config_dir = project_dir / ".autoskillit"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("github:\n  token: ghp_leaked\n")

    result = _check_config_layers_for_secrets(project_dir=project_dir)
    assert result.severity == Severity.ERROR
    assert "github.token" in result.message
    assert ".secrets.yaml" in result.message


# DR-SECRETS-2
def test_doctor_reports_ok_when_no_misplaced_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DR-SECRETS-2: Doctor reports OK when config.yaml has no secrets-only keys.

    home has no config; only the project config exists with a clean (non-secret) key.
    This exercises the project path independently of the home path.
    """
    from autoskillit.cli._doctor import _check_config_layers_for_secrets
    from autoskillit.core import Severity

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    config_dir = project_dir / ".autoskillit"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("github:\n  default_repo: owner/repo\n")

    result = _check_config_layers_for_secrets(project_dir=project_dir)
    assert result.severity == Severity.OK


# DC-11: _check_hook_registry_drift — deployed matches canonical → OK
def test_check_hook_registry_drift_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autoskillit.cli._doctor import _check_hook_registry_drift
    from autoskillit.cli._hooks import _evict_stale_autoskillit_hooks, sync_hooks_to_settings
    from autoskillit.core import Severity

    settings = tmp_path / "settings.json"
    _evict_stale_autoskillit_hooks(settings)
    sync_hooks_to_settings(settings)
    result = _check_hook_registry_drift(settings)
    assert result.severity == Severity.OK
    assert result.check == "hook_registry_drift"


# DC-12: _check_hook_registry_drift — missing hooks → WARNING with count
def test_check_hook_registry_drift_warning(tmp_path: Path) -> None:
    import json

    from autoskillit.cli._doctor import _check_hook_registry_drift
    from autoskillit.core import Severity

    settings = tmp_path / "settings.json"
    # Write settings.json with no hooks (simulating stale install)
    settings.write_text(json.dumps({"hooks": {}}))
    result = _check_hook_registry_drift(settings)
    assert result.severity == Severity.WARNING
    assert result.check == "hook_registry_drift"
    assert "autoskillit install" in result.message
    assert "new/changed" in result.message


# DC-13: doctor JSON output includes hook_registry_drift check with correct severity
def test_doctor_json_output_includes_hook_registry_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from autoskillit.core import Severity

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    cli.doctor(output_json=True)
    data = json.loads(capsys.readouterr().out)
    drift = next(r for r in data["results"] if r["check"] == "hook_registry_drift")
    # No settings.json in tmp_path → all canonical hooks missing → WARNING
    assert drift["severity"] == Severity.WARNING


class TestEditableInstallSourceExistsCheck:
    """Tests for the editable_install_source_exists doctor check."""

    def test_check_ok_when_not_editable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-editable install → OK."""
        import importlib.metadata as meta

        from autoskillit.cli._doctor import _check_editable_install_source_exists

        class FakeDist:
            def read_text(self, filename: str) -> str | None:
                if filename == "direct_url.json":
                    return '{"url": "https://pypi.org/...", "dir_info": {"editable": false}}'
                return None

        monkeypatch.setattr(meta.Distribution, "from_name", lambda name: FakeDist())
        result = _check_editable_install_source_exists()
        assert result.check == "editable_install_source_exists"
        assert result.severity.value == "ok"

    def test_check_error_when_editable_source_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Editable install pointing to a deleted directory → ERROR."""
        import importlib.metadata as meta

        from autoskillit.cli._doctor import _check_editable_install_source_exists

        deleted_path = tmp_path / "deleted-worktree" / "src"
        # Do NOT create deleted_path — it does not exist

        class FakeDist:
            def read_text(self, filename: str) -> str | None:
                if filename == "direct_url.json":
                    return json.dumps(
                        {
                            "url": f"file://{deleted_path}",
                            "dir_info": {"editable": True},
                        }
                    )
                return None

        monkeypatch.setattr(meta.Distribution, "from_name", lambda name: FakeDist())
        result = _check_editable_install_source_exists()
        assert result.check == "editable_install_source_exists"
        assert result.severity.value == "error"
        assert str(deleted_path) in result.message

    def test_check_ok_when_editable_source_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Editable install pointing to an existing directory → OK."""
        import importlib.metadata as meta

        from autoskillit.cli._doctor import _check_editable_install_source_exists

        existing_path = tmp_path / "src"
        existing_path.mkdir()

        class FakeDist:
            def read_text(self, filename: str) -> str | None:
                if filename == "direct_url.json":
                    return json.dumps(
                        {
                            "url": f"file://{existing_path}",
                            "dir_info": {"editable": True},
                        }
                    )
                return None

        monkeypatch.setattr(meta.Distribution, "from_name", lambda name: FakeDist())
        result = _check_editable_install_source_exists()
        assert result.check == "editable_install_source_exists"
        assert result.severity.value == "ok"

    def test_check_ok_when_not_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PackageNotFoundError → check returns OK (not installed in this env)."""
        import importlib.metadata as meta

        from autoskillit.cli._doctor import _check_editable_install_source_exists

        monkeypatch.setattr(
            meta.Distribution,
            "from_name",
            lambda name: (_ for _ in ()).throw(meta.PackageNotFoundError(name)),
        )
        result = _check_editable_install_source_exists()
        assert result.check == "editable_install_source_exists"
        assert result.severity.value == "ok"


class TestStaleEntryPointsCheck:
    """Tests for the stale_entry_points doctor check."""

    def test_check_ok_when_single_entry_point_in_local_bin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Single autoskillit binary at ~/.local/bin → OK."""
        import subprocess

        from autoskillit.cli._doctor import _check_stale_entry_points

        local_bin_path = str(Path.home() / ".local/bin/autoskillit")
        monkeypatch.setattr(shutil, "which", lambda name: local_bin_path)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": local_bin_path})(),
        )
        result = _check_stale_entry_points()
        assert result.check == "stale_entry_points"
        assert result.severity.value == "ok"

    def test_check_warning_when_stale_entry_point_outside_local_bin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """autoskillit binary outside ~/.local/bin → WARNING."""
        import subprocess

        from autoskillit.cli._doctor import _check_stale_entry_points

        stale_path = "/usr/local/micromamba/bin/autoskillit"
        monkeypatch.setattr(shutil, "which", lambda name: stale_path)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: type(
                "R",
                (),
                {
                    "returncode": 0,
                    "stdout": f"{stale_path}\n{Path.home()}/.local/bin/autoskillit",
                },
            )(),
        )
        result = _check_stale_entry_points()
        assert result.check == "stale_entry_points"
        assert result.severity.value == "warning"
        assert stale_path in result.message


def test_doctor_hook_health_checks_all_event_types(tmp_path: Path) -> None:
    """hook_health must verify PostToolUse and SessionStart scripts exist, not just PreToolUse."""
    from autoskillit.cli._doctor import _check_hook_health
    from autoskillit.core import Severity

    # Write a settings.json that includes token_summary_hook (PostToolUse)
    # but point it at a non-existent path.
    settings = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": ".*run_skill.*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 /nonexistent/token_summary_hook.py",
                        }
                    ],
                }
            ]
        }
    }
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings))

    result = _check_hook_health(settings_path)
    assert result.severity != Severity.OK, (
        "hook_health must report non-OK when a PostToolUse hook script is missing"
    )
    assert "token_summary_hook" in result.message or "PostToolUse" in result.message


# T-DRIFT-1: _count_hook_registry_drift() detects orphaned hooks
def test_count_hook_registry_drift_detects_orphaned_hooks(tmp_path: Path) -> None:
    """deployed − canonical must be counted and returned.
    Orphaned hooks are the fatal failure mode (ENOENT on every tool call).
    """
    from autoskillit.cli._doctor import _count_hook_registry_drift

    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    ghost_cmd = "python3 /path/to/autoskillit/hooks/status_health_guard.py"
    data = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "mcp__.*autoskillit.*__run_skill.*",
                    "hooks": [{"type": "command", "command": ghost_cmd}],
                }
            ]
        }
    }
    settings.write_text(json.dumps(data))
    result = _count_hook_registry_drift(settings)
    assert hasattr(result, "orphaned"), (
        "_count_hook_registry_drift must return a result with 'orphaned' field"
    )
    assert result.orphaned >= 1, f"Expected orphaned >= 1 for ghost entry, got {result.orphaned}"


# T-DRIFT-2: _check_hook_registry_drift() returns ERROR for orphaned hooks
def test_check_hook_registry_drift_error_on_orphaned_hooks(tmp_path: Path) -> None:
    from autoskillit.cli._doctor import _check_hook_registry_drift
    from autoskillit.core import Severity

    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    ghost_cmd = "python3 /path/to/autoskillit/hooks/status_health_guard.py"
    data = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "mcp__.*autoskillit.*__run_skill.*",
                    "hooks": [{"type": "command", "command": ghost_cmd}],
                }
            ]
        }
    }
    settings.write_text(json.dumps(data))
    result = _check_hook_registry_drift(settings)
    assert result.severity == Severity.ERROR, (
        f"Orphaned hooks must produce ERROR severity, got {result.severity}"
    )
    assert "status_health_guard.py" in result.message, (
        "Error message must name the orphaned script(s)"
    )


# T-DRIFT-3: User hooks must not appear as orphans
def test_count_hook_registry_drift_ignores_user_hooks(tmp_path: Path) -> None:
    """Non-autoskillit user hooks in settings.json must not be counted as orphaned.

    Regression: _extract_script_basenames() includes ALL commands without filtering,
    making user hooks appear as orphans in the deployed - canonical set diff.
    """
    from autoskillit.hook_registry import _count_hook_registry_drift, generate_hooks_json

    # Start with all canonical hooks so missing=0
    canonical_data = generate_hooks_json()
    # Add non-autoskillit user hooks alongside canonical ones
    user_hooks = [
        {"type": "command", "command": "python3 /home/user/my_guard.py"},
        {"type": "command", "command": 'wsl-notify-send.exe "Done!"'},
    ]
    canonical_data["hooks"].setdefault("PreToolUse", []).append(
        {"matcher": ".*", "hooks": user_hooks}
    )
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps(canonical_data))

    result = _count_hook_registry_drift(settings)
    assert result.orphaned == 0, (
        f"User hooks must not be counted as orphaned, got orphaned={result.orphaned}, "
        f"orphaned_cmds={result.orphaned_cmds}"
    )
    assert result.missing == 0, (
        f"All canonical hooks are deployed, expected missing=0, got {result.missing}"
    )


# T-DRIFT-4: Cross-environment path mismatch must not cause false drift
def test_count_hook_registry_drift_cross_env_path_mismatch(tmp_path: Path) -> None:
    """settings.json written by a different install (different pkg_root prefix)
    must not show drift when all script basenames match.

    Regression: full-path string comparison treats path-prefix differences
    as drift, even though the same scripts are deployed.
    """
    from autoskillit.hook_registry import HOOK_REGISTRY, _count_hook_registry_drift

    # Build settings.json with a DIFFERENT path prefix than current pkg_root()
    foreign_hooks_dir = (
        "/home/user/.local/share/uv/tools/autoskillit/lib/python3.13"
        "/site-packages/autoskillit/hooks"
    )
    by_event: dict[str, list[dict]] = {}
    for hdef in HOOK_REGISTRY:
        hook_commands = [
            {"type": "command", "command": f"python3 {foreign_hooks_dir}/{script}"}
            for script in hdef.scripts
        ]
        entry: dict = {"hooks": hook_commands}
        if hdef.event_type != "SessionStart":
            entry["matcher"] = hdef.matcher
        by_event.setdefault(hdef.event_type, []).append(entry)
    data = {"hooks": by_event}

    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps(data))

    result = _count_hook_registry_drift(settings)
    assert result.orphaned == 0, (
        f"Path prefix difference must not cause orphaned hooks, got orphaned={result.orphaned}"
    )
    assert result.missing == 0, (
        f"Path prefix difference must not cause missing hooks, got missing={result.missing}"
    )


# ---------------------------------------------------------------------------
# _check_source_version_drift — doctor check (cache-only, no network)
# ---------------------------------------------------------------------------


def test_check_source_version_drift_ok_outside_source_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GIT_VCS install with empty cache reports OK (no drift observable)."""
    from autoskillit.cli._doctor import _check_source_version_drift
    from autoskillit.cli._install_info import InstallInfo, InstallType
    from autoskillit.core import Severity

    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc1234",
        requested_revision="integration",
        url=None,
        editable_source=None,
    )
    monkeypatch.setattr("autoskillit.cli._install_info.detect_install", lambda: info)
    # Simulate empty cache and no source repo: resolve returns None
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.resolve_reference_sha", lambda info, home, **kw: None
    )

    result = _check_source_version_drift(home=tmp_path)
    assert result.severity == Severity.OK


def test_check_source_version_drift_ok_for_editable_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LOCAL_EDITABLE installs are under active development — drift check is skipped."""
    from autoskillit.cli._doctor import _check_source_version_drift
    from autoskillit.cli._install_info import InstallInfo, InstallType
    from autoskillit.core import Severity

    info = InstallInfo(
        install_type=InstallType.LOCAL_EDITABLE,
        commit_id=None,
        requested_revision=None,
        url="file:///home/user/autoskillit",
        editable_source=Path("/home/user/autoskillit"),
    )
    monkeypatch.setattr("autoskillit.cli._install_info.detect_install", lambda: info)

    result = _check_source_version_drift(home=tmp_path)
    assert result.severity == Severity.OK
    assert "editable" in result.message.lower() or "not applicable" in result.message.lower()


def test_check_source_version_drift_ok_for_pinned_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When requested_revision == commit_id, resolve_reference_sha short-circuits → no drift."""
    from autoskillit.cli._doctor import _check_source_version_drift
    from autoskillit.cli._install_info import InstallInfo, InstallType
    from autoskillit.core import Severity

    sha = "abcdef1234567890abcdef1234567890"
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id=sha,
        requested_revision=sha,  # pinned SHA = no drift possible
        url=None,
        editable_source=None,
    )
    monkeypatch.setattr("autoskillit.cli._install_info.detect_install", lambda: info)
    # When requested_revision == commit_id, resolve_reference_sha returns commit_id
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.resolve_reference_sha", lambda info, home, **kw: sha
    )

    result = _check_source_version_drift(home=tmp_path)
    assert result.severity == Severity.OK


def test_check_source_version_drift_ok_when_cache_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When SHA cannot be resolved (network/cache miss), doctor reports OK."""
    from autoskillit.cli._doctor import _check_source_version_drift
    from autoskillit.cli._install_info import InstallInfo, InstallType
    from autoskillit.core import Severity

    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="installed123",
        requested_revision="integration",
        url=None,
        editable_source=None,
    )
    monkeypatch.setattr("autoskillit.cli._install_info.detect_install", lambda: info)
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.resolve_reference_sha", lambda info, home, **kw: None
    )

    result = _check_source_version_drift(home=tmp_path)
    assert result.severity == Severity.OK
    # Message should note that the reference SHA is unavailable
    assert "unavailable" in result.message.lower(), (
        f"Expected 'unavailable' in message when resolve returns None, got: {result.message!r}"
    )


def test_check_source_version_drift_warning_on_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When cache has a different reference SHA than installed, reports WARNING with short SHAs."""
    from autoskillit.cli._doctor import _check_source_version_drift
    from autoskillit.cli._install_info import InstallInfo, InstallType
    from autoskillit.core import Severity

    installed_sha = "installed123abc"
    ref_sha = "reference456def"

    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id=installed_sha,
        requested_revision="integration",
        url=None,
        editable_source=None,
    )
    monkeypatch.setattr("autoskillit.cli._install_info.detect_install", lambda: info)
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.resolve_reference_sha", lambda info, home, **kw: ref_sha
    )

    result = _check_source_version_drift(home=tmp_path)
    assert result.severity == Severity.WARNING
    assert installed_sha[:8] in result.message
    assert ref_sha[:8] in result.message


# ---------------------------------------------------------------------------
# Check 14: Quota cache schema version (#711 Part B, Phase 4)
# ---------------------------------------------------------------------------


class TestCheckQuotaCacheSchema:
    """Tests for _check_quota_cache_schema doctor check."""

    def test_check_quota_cache_schema_ok_when_current(self, tmp_path):
        import json

        from autoskillit.cli._doctor import Severity, _check_quota_cache_schema
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
        from autoskillit.cli._doctor import Severity, _check_quota_cache_schema

        cache = tmp_path / "nonexistent.json"
        result = _check_quota_cache_schema(cache_path=cache)
        assert result.severity == Severity.OK
        assert "No quota cache" in result.message

    def test_check_quota_cache_schema_warning_when_no_schema_version_key(self, tmp_path):
        import json

        from autoskillit.cli._doctor import Severity, _check_quota_cache_schema

        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"fetched_at": "2026-01-01T00:00:00"}))
        result = _check_quota_cache_schema(cache_path=cache)
        assert result.severity == Severity.WARNING
        assert "schema drift" in result.message.lower()

    def test_check_quota_cache_schema_warning_when_older_schema_version(self, tmp_path):
        import json

        from autoskillit.cli._doctor import Severity, _check_quota_cache_schema

        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"schema_version": 1, "fetched_at": "2026-01-01T00:00:00"}))
        result = _check_quota_cache_schema(cache_path=cache)
        assert result.severity == Severity.WARNING

    def test_check_quota_cache_schema_warning_includes_cache_path_and_observed_value(
        self, tmp_path
    ):
        import json

        from autoskillit.cli._doctor import Severity, _check_quota_cache_schema

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
    import json as _json

    from autoskillit.cli._doctor import _check_hook_registry_drift
    from autoskillit.core import Severity

    # Seed a stale pretty_output.py in project scope
    project_settings = tmp_path / ".claude" / "settings.json"
    project_settings.parent.mkdir(parents=True)
    project_settings.write_text(
        _json.dumps(
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


# ---------------------------------------------------------------------------
# REQ-DOCTOR-001 — _check_claude_process_state_breakdown
# ---------------------------------------------------------------------------


class TestCheckClaudeProcessStateBreakdown:
    """Tests for the claude_process_state doctor check (Check 15)."""

    def _ps_result(self, stdout: str, returncode: int = 0):
        return type(
            "CompletedProcess",
            (),
            {"returncode": returncode, "stdout": stdout},
        )()

    def test_ok_when_only_sleeping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Single sleeping claude process → Severity.OK with state breakdown."""
        import subprocess

        from autoskillit.cli._doctor import Severity, _check_claude_process_state_breakdown

        header = "PID STAT %CPU COMMAND\n"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: self._ps_result(header + "1234 S 0.5 claude"),
        )
        result = _check_claude_process_state_breakdown()
        assert result.severity == Severity.OK
        assert "S=1" in result.message

    def test_warns_on_d_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """claude process in D state → Severity.WARNING with pid and pcpu in message."""
        import subprocess

        from autoskillit.cli._doctor import Severity, _check_claude_process_state_breakdown

        header = "PID STAT %CPU COMMAND\n"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: self._ps_result(header + "1234 D 99.0 claude"),
        )
        result = _check_claude_process_state_breakdown()
        assert result.severity == Severity.WARNING
        assert "D=1" in result.message
        assert "99.0" in result.message

    def test_ok_when_no_claude_processes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty ps output (no claude rows) → Severity.OK, 'No claude processes running'."""
        import subprocess

        from autoskillit.cli._doctor import Severity, _check_claude_process_state_breakdown

        header = "PID STAT %CPU COMMAND\n"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: self._ps_result(header + "5678 S 0.1 python"),
        )
        result = _check_claude_process_state_breakdown()
        assert result.severity == Severity.OK
        assert result.message == "No claude processes running"

    def test_ok_when_ps_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FileNotFoundError from ps → Severity.OK explaining ps unavailability."""
        import subprocess

        from autoskillit.cli._doctor import Severity, _check_claude_process_state_breakdown

        def _raise(*a, **kw):
            raise FileNotFoundError("ps")

        monkeypatch.setattr(subprocess, "run", _raise)
        result = _check_claude_process_state_breakdown()
        assert result.severity == Severity.OK
        assert "ps unavailable" in result.message
        assert "FileNotFoundError" in result.message


class TestDoctorInstallClassification:
    """Tests for _check_install_classification doctor check."""

    @pytest.mark.parametrize(
        "revision,expected_fragment",
        [
            ("stable", "stable"),
            ("integration", "integration"),
        ],
    )
    def test_doctor_reports_install_classification_git_vcs(
        self, monkeypatch: pytest.MonkeyPatch, revision: str, expected_fragment: str
    ) -> None:
        import json

        from autoskillit.cli._doctor import Severity, _check_install_classification

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

        from autoskillit.cli._doctor import Severity, _check_install_classification

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
        from autoskillit.cli._doctor import Severity, _check_update_dismissal_state

        result = _check_update_dismissal_state(home=tmp_path)
        assert result.severity == Severity.OK
        assert "No active dismissal" in result.message

    def test_doctor_reports_dismissal_state_populated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json
        from datetime import UTC, datetime
        from unittest.mock import MagicMock

        from autoskillit.cli._doctor import Severity, _check_update_dismissal_state
        from autoskillit.cli._update_checks import _write_dismiss_state

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
        import json
        from unittest.mock import MagicMock

        from autoskillit.cli._doctor import _check_source_version_drift

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
            "autoskillit.cli._update_checks.resolve_reference_sha",
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
        import json
        from unittest.mock import MagicMock

        from autoskillit.cli._doctor import _check_source_version_drift

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
            "autoskillit.cli._update_checks.resolve_reference_sha",
            lambda info, home, **kw: None,
        )

        from autoskillit.cli._doctor import Severity

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
    from autoskillit.cli._doctor import _check_dual_mcp_registration
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
    from autoskillit.cli._doctor import _check_dual_mcp_registration
    from autoskillit.core import Severity

    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({"mcpServers": {"autoskillit": {"type": "stdio"}}}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = _check_dual_mcp_registration()
    assert result.severity == Severity.OK


def test_check_installed_plugins_entry_real_structure_is_ok(tmp_path: Path) -> None:
    """With the real nested format, the check must report OK."""
    from autoskillit.cli._doctor import _check_installed_plugins_entry
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
    from autoskillit.cli._doctor import _check_installed_plugins_entry
    from autoskillit.core import Severity

    p = tmp_path / "installed_plugins.json"
    p.write_text(json.dumps({"autoskillit@autoskillit-local": {}}))
    result = _check_installed_plugins_entry(plugins_json_path=p)
    assert result.severity == Severity.WARNING


class TestGroupMFranchiseDoctorChecks:
    """Group M: Franchise doctor checks (ambient env detection + infra health + campaign ops)."""

    # M1: SESSION_TYPE unset → OK (unset is normal; check only fires on explicit 'leaf')
    def test_check_ambient_session_type_leaf_ok_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_ambient_session_type_leaf
        from autoskillit.core import Severity

        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        result = _check_ambient_session_type_leaf()
        assert result.severity == Severity.OK
        assert result.check == "ambient_session_type_leaf"

    # M2: SESSION_TYPE=leaf → WARN
    def test_check_ambient_session_type_leaf_warns_when_leaf(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_ambient_session_type_leaf
        from autoskillit.core import Severity

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
        result = _check_ambient_session_type_leaf()
        assert result.severity == Severity.WARNING

    # M3: SESSION_TYPE=orchestrator → OK (not this check's concern)
    def test_check_ambient_session_type_leaf_ok_when_orchestrator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_ambient_session_type_leaf
        from autoskillit.core import Severity

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        result = _check_ambient_session_type_leaf()
        assert result.severity == Severity.OK

    # M4: SESSION_TYPE=orchestrator → WARN from orchestrator check
    def test_check_ambient_session_type_orchestrator_warns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_ambient_session_type_orchestrator
        from autoskillit.core import Severity

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        result = _check_ambient_session_type_orchestrator()
        assert result.severity == Severity.WARNING
        assert "should only be set by autoskillit CLIs" in result.message

    # M5: SESSION_TYPE=franchise → WARN from franchise check
    def test_check_ambient_session_type_franchise_warns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_ambient_session_type_franchise
        from autoskillit.core import Severity

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
        result = _check_ambient_session_type_franchise()
        assert result.severity == Severity.WARNING
        assert "highest-privilege" in result.message

    # M6: SESSION_TYPE unset → OK for orchestrator and franchise checks
    def test_check_ambient_session_type_orchestrator_ok_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_ambient_session_type_orchestrator
        from autoskillit.core import Severity

        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        result = _check_ambient_session_type_orchestrator()
        assert result.severity == Severity.OK

    def test_check_ambient_session_type_franchise_ok_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_ambient_session_type_franchise
        from autoskillit.core import Severity

        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        result = _check_ambient_session_type_franchise()
        assert result.severity == Severity.OK

    # M7: CAMPAIGN_ID set → WARN
    def test_check_ambient_campaign_id_warns_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_ambient_campaign_id
        from autoskillit.core import Severity

        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "camp-123")
        result = _check_ambient_campaign_id()
        assert result.severity == Severity.WARNING
        assert "camp-123" in result.message
        assert "dispatch_food_truck" in result.message

    # M8: CAMPAIGN_ID unset → OK
    def test_check_ambient_campaign_id_ok_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_ambient_campaign_id
        from autoskillit.core import Severity

        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        result = _check_ambient_campaign_id()
        assert result.severity == Severity.OK

    # M9: sous-chef skill dir exists → OK
    def test_check_sous_chef_bundled_ok(self) -> None:
        from autoskillit.cli._doctor import _check_sous_chef_bundled
        from autoskillit.core import Severity

        result = _check_sous_chef_bundled()
        assert result.severity == Severity.OK

    # M10: sous-chef skill dir missing → ERROR
    def test_check_sous_chef_bundled_error_when_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_sous_chef_bundled
        from autoskillit.core import Severity

        monkeypatch.setattr("autoskillit.cli._doctor.pkg_root", lambda: tmp_path)
        result = _check_sous_chef_bundled()
        assert result.severity == Severity.ERROR
        assert "sous-chef" in result.message

    # M11: franchise_dispatch_guard registered and exists → OK
    def test_check_franchise_dispatch_guard_registered_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_franchise_dispatch_guard_registered
        from autoskillit.core import Severity

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "franchise_dispatch_guard.py").write_text("")
        monkeypatch.setattr(
            "autoskillit.cli._doctor.canonical_script_basenames",
            lambda: frozenset({"franchise_dispatch_guard.py"}),
        )
        monkeypatch.setattr("autoskillit.hook_registry.HOOKS_DIR", hooks_dir)
        result = _check_franchise_dispatch_guard_registered()
        assert result.severity == Severity.OK

    # M12: franchise_dispatch_guard not registered → ERROR
    def test_check_franchise_dispatch_guard_registered_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_franchise_dispatch_guard_registered
        from autoskillit.core import Severity

        monkeypatch.setattr(
            "autoskillit.cli._doctor.canonical_script_basenames",
            lambda: frozenset(),
        )
        result = _check_franchise_dispatch_guard_registered()
        assert result.severity == Severity.ERROR
        assert "sync-hooks" in result.message

    # M13: No state files → OK
    def test_check_stale_franchise_state_ok_when_no_state(self, tmp_path: Path) -> None:
        from autoskillit.cli._doctor import _check_stale_franchise_state
        from autoskillit.core import Severity

        result = _check_stale_franchise_state(project_dir=tmp_path)
        assert result.severity == Severity.OK

    # M14: State file with running dispatch and mtime > 7d → WARN
    def test_check_stale_franchise_state_warns_on_stale(self, tmp_path: Path) -> None:
        import os
        import time

        from autoskillit.cli._doctor import _check_stale_franchise_state
        from autoskillit.core import Severity

        state_dir = tmp_path / ".autoskillit" / "temp" / "franchise" / "camp-1"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.json"
        state_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "campaign_id": "camp-1",
                    "campaign_name": "test",
                    "manifest_path": "",
                    "started_at": 0,
                    "dispatches": [{"name": "d1", "status": "running"}],
                }
            )
        )
        old_time = time.time() - (8 * 86400)
        os.utime(state_file, (old_time, old_time))
        result = _check_stale_franchise_state(project_dir=tmp_path)
        assert result.severity == Severity.WARNING
        assert "camp-1" in result.message or "state.json" in result.message

    # M15: State file with running dispatch and mtime < 7d → OK
    def test_check_stale_franchise_state_ok_when_fresh(self, tmp_path: Path) -> None:
        from autoskillit.cli._doctor import _check_stale_franchise_state
        from autoskillit.core import Severity

        state_dir = tmp_path / ".autoskillit" / "temp" / "franchise" / "camp-1"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.json"
        state_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "campaign_id": "camp-1",
                    "campaign_name": "test",
                    "manifest_path": "",
                    "started_at": 0,
                    "dispatches": [{"name": "d1", "status": "running"}],
                }
            )
        )
        result = _check_stale_franchise_state(project_dir=tmp_path)
        assert result.severity == Severity.OK

    # M16: No campaigns/ dir → INFO onboarding hint
    def test_check_campaign_onboarding_hint_info_when_empty(self, tmp_path: Path) -> None:
        from autoskillit.cli._doctor import _check_campaign_onboarding_hint
        from autoskillit.core import Severity

        result = _check_campaign_onboarding_hint(project_dir=tmp_path)
        assert result.severity == Severity.INFO
        assert "make-campaign" in result.message

    # M17: campaigns/ has YAML files → OK
    def test_check_campaign_onboarding_hint_ok_when_populated(self, tmp_path: Path) -> None:
        from autoskillit.cli._doctor import _check_campaign_onboarding_hint
        from autoskillit.core import Severity

        campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
        campaigns_dir.mkdir(parents=True)
        (campaigns_dir / "my-campaign.yaml").write_text("name: my-campaign\nkind: campaign\n")
        result = _check_campaign_onboarding_hint(project_dir=tmp_path)
        assert result.severity == Severity.OK

    # M18: Duplicate clone destinations across dispatches → WARN
    def test_check_campaign_manifest_clone_dests_warns_on_duplicates(self, tmp_path: Path) -> None:
        from autoskillit.cli._doctor import _check_campaign_manifest_clone_dests
        from autoskillit.core import Severity

        campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
        campaigns_dir.mkdir(parents=True)
        recipe_yaml = textwrap.dedent("""\
            name: my-campaign
            kind: campaign
            dispatches:
              - name: task-1
                ingredients:
                  clone_path: /tmp/shared-clone
              - name: task-2
                ingredients:
                  clone_path: /tmp/shared-clone
        """)
        (campaigns_dir / "dup-campaign.yaml").write_text(recipe_yaml)
        result = _check_campaign_manifest_clone_dests(project_dir=tmp_path)
        assert result.severity == Severity.WARNING
        assert "/tmp/shared-clone" in result.message

    # M19: Unique clone destinations → OK
    def test_check_campaign_manifest_clone_dests_ok_unique(self, tmp_path: Path) -> None:
        from autoskillit.cli._doctor import _check_campaign_manifest_clone_dests
        from autoskillit.core import Severity

        campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
        campaigns_dir.mkdir(parents=True)
        recipe_yaml = textwrap.dedent("""\
            name: my-campaign
            kind: campaign
            dispatches:
              - name: task-1
                ingredients:
                  clone_path: /tmp/clone-1
              - name: task-2
                ingredients:
                  clone_path: /tmp/clone-2
        """)
        (campaigns_dir / "ok-campaign.yaml").write_text(recipe_yaml)
        result = _check_campaign_manifest_clone_dests(project_dir=tmp_path)
        assert result.severity == Severity.OK

    # M20: All 9 new checks appear in doctor JSON output
    def test_doctor_json_output_includes_franchise_checks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        cli.doctor(output_json=True)
        data = json.loads(capsys.readouterr().out)
        check_names = {r["check"] for r in data["results"]}
        franchise_checks = {
            "ambient_session_type_leaf",
            "ambient_session_type_orchestrator",
            "ambient_session_type_franchise",
            "ambient_campaign_id",
            "sous_chef_bundled",
            "franchise_dispatch_guard_registered",
            "stale_franchise_state",
            "campaign_onboarding_hint",
            "campaign_manifest_clone_dests",
        }
        assert franchise_checks <= check_names


class TestGroupNFeatureGateDoctorChecks:
    """N1–N8: Feature-gate checks and FranchiseConfig conditional validation."""

    # N1: Franchise checks skipped when feature disabled
    def test_franchise_doctor_checks_skipped_when_disabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from autoskillit.config import AutomationConfig

        mock_cfg = AutomationConfig(features={"franchise": False})
        monkeypatch.setattr("autoskillit.cli._doctor.load_config", lambda _: mock_cfg)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        cli.doctor(output_json=True)
        data = json.loads(capsys.readouterr().out)
        check_names = {r["check"] for r in data["results"]}
        franchise_infra = {
            "sous_chef_bundled",
            "franchise_dispatch_guard_registered",
            "stale_franchise_state",
            "campaign_onboarding_hint",
            "campaign_manifest_clone_dests",
        }
        assert franchise_infra.isdisjoint(check_names), (
            f"Franchise checks must be absent when feature is disabled, "
            f"but found: {franchise_infra & check_names}"
        )

    # N2: Franchise checks run when feature enabled
    def test_franchise_doctor_checks_run_when_enabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from autoskillit.config import AutomationConfig

        mock_cfg = AutomationConfig(features={"franchise": True})
        monkeypatch.setattr("autoskillit.cli._doctor.load_config", lambda _: mock_cfg)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        cli.doctor(output_json=True)
        data = json.loads(capsys.readouterr().out)
        check_names = {r["check"] for r in data["results"]}
        franchise_infra = {
            "sous_chef_bundled",
            "franchise_dispatch_guard_registered",
            "stale_franchise_state",
            "campaign_onboarding_hint",
            "campaign_manifest_clone_dests",
        }
        assert franchise_infra <= check_names

    # N3: Ambient env checks always run even when franchise disabled
    def test_ambient_env_checks_always_run_when_franchise_disabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from autoskillit.config import AutomationConfig

        mock_cfg = AutomationConfig(features={"franchise": False})
        monkeypatch.setattr("autoskillit.cli._doctor.load_config", lambda _: mock_cfg)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        cli.doctor(output_json=True)
        data = json.loads(capsys.readouterr().out)
        check_names = {r["check"] for r in data["results"]}
        ambient_checks = {
            "ambient_session_type_leaf",
            "ambient_session_type_orchestrator",
            "ambient_session_type_franchise",
            "ambient_campaign_id",
        }
        assert ambient_checks <= check_names

    # N4: Feature dependency check fires ERROR for unsatisfied dep
    def test_feature_dependency_check_fires_on_unsatisfied_dep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_feature_dependencies
        from autoskillit.core import Severity
        from autoskillit.core._type_constants import FeatureDef, FeatureLifecycle

        fake_feature = FeatureDef(
            name="test_feature",
            lifecycle=FeatureLifecycle.EXPERIMENTAL,
            description="test feature with dep",
            tool_tags=frozenset(),
            skill_categories=frozenset(),
            import_package=None,
            default_enabled=False,
            depends_on=frozenset({"franchise"}),
        )
        monkeypatch.setattr(
            "autoskillit.core.FEATURE_REGISTRY",
            {"test_feature": fake_feature},
        )
        result = _check_feature_dependencies({"test_feature": True, "franchise": False})
        assert result.severity == Severity.ERROR
        assert "test_feature" in result.message
        assert "franchise" in result.message

    # N5: Feature dependency check passes when deps satisfied
    def test_feature_dependency_check_passes_when_deps_satisfied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_feature_dependencies
        from autoskillit.core import Severity
        from autoskillit.core._type_constants import FeatureDef, FeatureLifecycle

        fake_feature = FeatureDef(
            name="test_feature",
            lifecycle=FeatureLifecycle.EXPERIMENTAL,
            description="test feature with dep",
            tool_tags=frozenset(),
            skill_categories=frozenset(),
            import_package=None,
            default_enabled=False,
            depends_on=frozenset({"franchise"}),
        )
        monkeypatch.setattr(
            "autoskillit.core.FEATURE_REGISTRY",
            {"test_feature": fake_feature},
        )
        result = _check_feature_dependencies({"test_feature": True, "franchise": True})
        assert result.severity == Severity.OK

    # N6: Feature dependency check passes with empty features
    def test_feature_dependency_check_passes_with_empty_features(self) -> None:
        from autoskillit.cli._doctor import _check_feature_dependencies
        from autoskillit.core import Severity

        result = _check_feature_dependencies({})
        assert result.severity == Severity.OK

    # N7: Feature registry consistency passes for real registry
    def test_feature_registry_consistency_passes(self) -> None:
        from autoskillit.cli._doctor import _check_feature_registry_consistency
        from autoskillit.core import Severity

        result = _check_feature_registry_consistency()
        assert result.severity == Severity.OK

    # N8: Feature registry consistency errors on bad import
    def test_feature_registry_consistency_errors_on_bad_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli._doctor import _check_feature_registry_consistency
        from autoskillit.core import Severity
        from autoskillit.core._type_constants import FeatureDef, FeatureLifecycle

        bad_feature = FeatureDef(
            name="bad_feature",
            lifecycle=FeatureLifecycle.EXPERIMENTAL,
            description="feature with bad import",
            tool_tags=frozenset(),
            skill_categories=frozenset(),
            import_package="nonexistent.pkg",
        )
        monkeypatch.setattr(
            "autoskillit.core.FEATURE_REGISTRY",
            {"bad_feature": bad_feature},
        )
        result = _check_feature_registry_consistency()
        assert result.severity == Severity.ERROR
        assert "bad_feature" in result.message
        assert "nonexistent.pkg" in result.message
