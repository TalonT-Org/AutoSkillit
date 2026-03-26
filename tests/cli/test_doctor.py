"""Tests for CLI doctor command and related utilities."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit import cli

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
                        "autoskillit": {"type": "stdio", "command": "autoskillit"},
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
        from autoskillit.core import _AUTOSKILLIT_GITIGNORE_ENTRIES, _ROOT_GITIGNORE_ENTRIES

        (tmp_path / ".autoskillit" / ".gitignore").write_text(
            "\n".join(_AUTOSKILLIT_GITIGNORE_ENTRIES) + "\n"
        )
        (tmp_path / ".gitignore").write_text("\n".join(_ROOT_GITIGNORE_ENTRIES) + "\n")
        (tmp_path / ".pre-commit-config.yaml").write_text(
            "repos:\n  - repo: dummy\n    hooks:\n      - id: gitleaks\n"
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
        with patch(
            "autoskillit.cli.shutil.which",
            side_effect=lambda cmd: (
                "/usr/local/bin/autoskillit" if cmd == "autoskillit" else shutil.which(cmd)
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
        assert severities <= {"ok", "warning", "error"}

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
    def test_update_command_does_not_exist(self):
        """REQ-APP-002: 'autoskillit update' is not a registered command."""
        assert not hasattr(cli, "update")

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


def test_doctor_clears_plugin_cache(tmp_path, monkeypatch, capsys):
    """Doctor must clear the plugin cache on every run."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    cache_dir = tmp_path / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
    cache_dir.mkdir(parents=True)
    (cache_dir / "0.3.0" / "hooks").mkdir(parents=True)
    (cache_dir / "0.3.0" / "hooks" / "pretty_output.py").write_text("# stale")
    cli.doctor()
    assert not cache_dir.exists()


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
    """Doctor must report OK when all .autoskillit/ files are covered."""
    from autoskillit.core.io import _ROOT_GITIGNORE_ENTRIES

    autoskillit_dir = tmp_path / ".autoskillit"
    autoskillit_dir.mkdir()
    (autoskillit_dir / ".gitignore").write_text(
        "temp/\n.secrets.yaml\nsync_manifest.json\n.onboarded\n"
    )
    (autoskillit_dir / "temp").mkdir()
    (autoskillit_dir / ".secrets.yaml").write_text("github:\n  token: ''\n")
    (tmp_path / ".gitignore").write_text("\n".join(_ROOT_GITIGNORE_ENTRIES) + "\n")

    monkeypatch.chdir(tmp_path)
    from autoskillit.cli._doctor import _check_gitignore_completeness
    from autoskillit.core import Severity

    result = _check_gitignore_completeness(tmp_path)
    assert result.severity == Severity.OK


# RG-DROOT-1
def test_doctor_warns_when_root_gitignore_missing_secrets_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doctor must WARN when root .gitignore lacks .autoskillit/.secrets.yaml."""
    from autoskillit.core.io import _AUTOSKILLIT_GITIGNORE_ENTRIES

    autoskillit_dir = tmp_path / ".autoskillit"
    autoskillit_dir.mkdir()
    # .autoskillit/.gitignore is complete — only root is missing
    (autoskillit_dir / ".gitignore").write_text("\n".join(_AUTOSKILLIT_GITIGNORE_ENTRIES) + "\n")
    (autoskillit_dir / ".secrets.yaml").write_text("github:\n  token: ''\n")
    # No root .gitignore

    monkeypatch.chdir(tmp_path)
    from autoskillit.cli._doctor import _check_gitignore_completeness
    from autoskillit.core import Severity

    result = _check_gitignore_completeness(tmp_path)
    assert result.severity == Severity.WARNING
    assert ".autoskillit/.secrets.yaml" in result.message


# RG-DROOT-2
def test_doctor_ok_when_root_gitignore_has_all_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doctor must report OK when both .autoskillit/.gitignore and root .gitignore are complete."""
    from autoskillit.core.io import _AUTOSKILLIT_GITIGNORE_ENTRIES, _ROOT_GITIGNORE_ENTRIES

    autoskillit_dir = tmp_path / ".autoskillit"
    autoskillit_dir.mkdir()
    (autoskillit_dir / ".gitignore").write_text("\n".join(_AUTOSKILLIT_GITIGNORE_ENTRIES) + "\n")
    (autoskillit_dir / ".secrets.yaml").write_text("github:\n  token: ''\n")
    (tmp_path / ".gitignore").write_text("\n".join(_ROOT_GITIGNORE_ENTRIES) + "\n")

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


# DC-13: doctor JSON output includes hook_registry_drift check
def test_doctor_json_output_includes_hook_registry_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    cli.doctor(output_json=True)
    data = json.loads(capsys.readouterr().out)
    check_names = {r["check"] for r in data["results"]}
    assert "hook_registry_drift" in check_names
