"""Tests for CLI doctor command and related utilities."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit import cli

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


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
        cli.doctor_cmd()
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
        # Create plugin cache directory for Check 2c + version_consistency plugin.json
        import importlib.metadata

        _cache_dir = (
            tmp_path / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
        )
        _cache_dir.mkdir(parents=True, exist_ok=True)
        _plugin_json = _cache_dir / ".claude-plugin" / "plugin.json"
        _plugin_json.parent.mkdir(parents=True, exist_ok=True)
        _plugin_json.write_text(json.dumps({"version": importlib.metadata.version("autoskillit")}))
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
        # Fleet checks: set SESSION_TYPE to a non-triggering value so ambient
        # checks 18-20 all return OK, and stub check 23 directly so it returns OK
        # without touching canonical_script_basenames (shared with hook-registration check 4).
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "worker")
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        from autoskillit.cli.doctor import DoctorResult
        from autoskillit.core import Severity

        monkeypatch.setattr(
            "autoskillit.cli.doctor._check_fleet_dispatch_guard_registered",
            lambda: DoctorResult(Severity.OK, "fleet_dispatch_guard_registered", "stubbed"),
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
            cli.doctor_cmd()
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
        cli.doctor_cmd()
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
        cli.doctor_cmd(output_json=True)
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
        cli.doctor_cmd(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        severities = {r["severity"] for r in data["results"]}
        assert severities <= {"ok", "warning", "error", "info"}

    def test_doctor_info_severity_not_treated_as_problem(self) -> None:
        """INFO findings must not appear in the problems section."""
        from autoskillit.cli.doctor import _NON_PROBLEM
        from autoskillit.core import Severity

        assert Severity.INFO in _NON_PROBLEM, "INFO must be in _NON_PROBLEM"
        assert Severity.OK in _NON_PROBLEM, "OK must be in _NON_PROBLEM"
        assert Severity.ERROR not in _NON_PROBLEM, "ERROR must not be in _NON_PROBLEM"
        assert Severity.WARNING not in _NON_PROBLEM, "WARNING must not be in _NON_PROBLEM"

    def test_doctor_passes_when_versions_match(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        request: pytest.FixtureRequest,
    ) -> None:
        """doctor reports ok when cached plugin.json version matches package."""
        import importlib.metadata

        pkg_version = importlib.metadata.version("autoskillit")
        cache_dir = (
            tmp_path / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
        )
        plugin_json = cache_dir / ".claude-plugin" / "plugin.json"
        plugin_json.parent.mkdir(parents=True)
        plugin_json.write_text(json.dumps({"version": pkg_version}))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        from autoskillit.version import version_info as _vi

        _vi.cache_clear()
        request.addfinalizer(_vi.cache_clear)
        cli.doctor_cmd(output_json=True)
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
        cfg_dir = tmp_path / ".autoskillit"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text("features:\n  fleet: true\n")
        cli.doctor_cmd(output_json=True)
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
            "ambient_session_type_fleet",
            "ambient_campaign_id",
            "feature_dependencies",
            "feature_registry_consistency",
            "sous_chef_bundled",
            "fleet_dispatch_guard_registered",
            "stale_fleet_state",
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
        cli.doctor_cmd()
        captured = capsys.readouterr()
        assert "ERROR:" in captured.out

    # DOC-REG-1
    def test_doctor_includes_mcp_server_registered_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor run_doctor() results include mcp_server_registered check."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor_cmd(output_json=True)
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
        cli.doctor_cmd(output_json=True)
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
        cli.doctor_cmd(output_json=True)
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
        cli.doctor_cmd(output_json=True)
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
        cli.doctor_cmd(output_json=True)
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
        cli.doctor_cmd(output_json=True)
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
        cli.doctor_cmd(output_json=True)
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
        cli.doctor_cmd(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        check_names = {r["check"] for r in data["results"]}
        assert "mcp_server_registered" in check_names
        assert "hook_registration" in check_names
        assert "marketplace_freshness" not in check_names
        assert "plugin_metadata" not in check_names
        assert "duplicate_mcp_server" not in check_names


class TestGroupFDoctor:
    """P8-2, P3-2: CLI refactoring — doctor delegation tests from TestGroupFRefactoring."""

    def test_doctor_delegates_to_doctor_module(self, monkeypatch, capsys):
        """cli.doctor_cmd() must delegate to cli.doctor.run_doctor(), not contain the logic."""
        import autoskillit.cli.doctor as _doctor_mod

        called_with: dict = {}

        def mock_run_doctor(*, output_json: bool = False) -> None:
            called_with["output_json"] = output_json

        monkeypatch.setattr(_doctor_mod, "run_doctor", mock_run_doctor)
        cli.doctor_cmd(output_json=True)
        assert called_with == {"output_json": True}

    def test_severity_and_doctorresult_in_doctor_module(self):
        """Severity and DoctorResult must be importable from autoskillit.cli.doctor."""
        from autoskillit.cli.doctor import DoctorResult, Severity

        r = DoctorResult(severity=Severity.OK, check="test", message="ok")
        assert r.severity == Severity.OK
        assert r.check == "test"


def test_doctor_fix_parameter_does_not_exist():
    """The doctor --fix no-op flag must be removed from the CLI."""
    import inspect

    from autoskillit import cli

    sig = inspect.signature(cli.doctor_cmd)
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

    cli.doctor_cmd()

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
    cli.doctor_cmd(output_json=True)
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
    cli.doctor_cmd(output_json=True)
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

    cli.doctor_cmd(output_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    check_names = {r["check"] for r in data["results"]}
    assert "stale_gate_file" not in check_names


def test_doctor_detects_plugin_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    """doctor must not report MCP unregistered when autoskillit is installed as a plugin."""
    import json as _json
    import subprocess
    import tempfile

    from autoskillit.cli.doctor import _check_mcp_server_registered
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
    from autoskillit.cli.doctor import _check_gitignore_completeness
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
    from autoskillit.cli.doctor import _check_gitignore_completeness
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
    cli.doctor_cmd(output_json=True)
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
    cli.doctor_cmd(output_json=True)
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
    cli.doctor_cmd(output_json=True)
    data = json.loads(capsys.readouterr().out)
    checks = [r for r in data["results"] if r["check"] == "secret_scanning_hook"]
    assert len(checks) == 1
    assert checks[0]["severity"] == "ok"


# SS-DOC-4 (unit test for check function directly)
def test_check_secret_scanning_hook_ok_with_gitleaks(tmp_path: Path) -> None:
    """_check_secret_scanning_hook returns OK when gitleaks hook is present."""
    from autoskillit.cli.doctor import _check_secret_scanning_hook
    from autoskillit.core import Severity

    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: dummy\n    hooks:\n      - id: gitleaks\n"
    )
    result = _check_secret_scanning_hook(tmp_path)
    assert result.severity == Severity.OK


# SS-DOC-5 (unit test for check function directly)
def test_check_secret_scanning_hook_error_without_scanner(tmp_path: Path) -> None:
    """_check_secret_scanning_hook returns ERROR when no .pre-commit-config.yaml."""
    from autoskillit.cli.doctor import _check_secret_scanning_hook
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
    from autoskillit.cli.doctor import _check_config_layers_for_secrets
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
    from autoskillit.cli.doctor import _check_config_layers_for_secrets
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
    from autoskillit.cli._hooks import _evict_stale_autoskillit_hooks, sync_hooks_to_settings
    from autoskillit.cli.doctor._doctor_hooks import _check_hook_registry_drift
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

    from autoskillit.cli.doctor._doctor_hooks import _check_hook_registry_drift
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
    cli.doctor_cmd(output_json=True)
    data = json.loads(capsys.readouterr().out)
    drift = next(r for r in data["results"] if r["check"] == "hook_registry_drift")
    # No settings.json in tmp_path → all canonical hooks missing → WARNING
    assert drift["severity"] == Severity.WARNING


class TestEditableInstallSourceExistsCheck:
    """Tests for the editable_install_source_exists doctor check."""

    def test_check_ok_when_not_editable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-editable install → OK."""
        import importlib.metadata as meta

        from autoskillit.cli.doctor import _check_editable_install_source_exists

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

        from autoskillit.cli.doctor import _check_editable_install_source_exists

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

        from autoskillit.cli.doctor import _check_editable_install_source_exists

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

        from autoskillit.cli.doctor import _check_editable_install_source_exists

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

        from autoskillit.cli.doctor import _check_stale_entry_points

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

        from autoskillit.cli.doctor import _check_stale_entry_points

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
    from autoskillit.cli.doctor._doctor_hooks import _check_hook_health
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


# T-WT-3: _check_hook_health_all_scopes detects broken paths in project scope
def test_check_hook_health_detects_broken_paths_in_project_scope(tmp_path: Path) -> None:
    """_check_hook_health_all_scopes must detect broken hooks in project scope, not just user."""
    from autoskillit.cli.doctor import _check_hook_health_all_scopes
    from autoskillit.core import Severity

    # Setup project-scope settings with a broken hook path
    project_settings = tmp_path / ".claude" / "settings.json"
    project_settings.parent.mkdir(parents=True)
    project_settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": ".*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 /deleted/worktree/hooks/quota_guard.py",
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )

    results = _check_hook_health_all_scopes(tmp_path)
    broken = [r for r in results if r.severity != Severity.OK]
    assert broken, "Must detect broken hook paths in project scope"


# T-DRIFT-1: _count_hook_registry_drift() detects orphaned hooks
def test_count_hook_registry_drift_detects_orphaned_hooks(tmp_path: Path) -> None:
    """deployed − canonical must be counted and returned.
    Orphaned hooks are the fatal failure mode (ENOENT on every tool call).
    """
    from autoskillit.hook_registry import _count_hook_registry_drift

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
    from autoskillit.cli.doctor._doctor_hooks import _check_hook_registry_drift
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
    from autoskillit.cli._install_info import InstallInfo, InstallType
    from autoskillit.cli.doctor import _check_source_version_drift
    from autoskillit.core import Severity

    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc1234",
        requested_revision="develop",
        url=None,
        editable_source=None,
    )
    monkeypatch.setattr("autoskillit.cli._install_info.detect_install", lambda: info)
    # Simulate empty cache and no source repo: resolve returns None
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.resolve_reference_sha",
        lambda info, home, **kw: None,
    )

    result = _check_source_version_drift(home=tmp_path)
    assert result.severity == Severity.OK


def test_check_source_version_drift_ok_for_editable_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LOCAL_EDITABLE installs are under active development — drift check is skipped."""
    from autoskillit.cli._install_info import InstallInfo, InstallType
    from autoskillit.cli.doctor import _check_source_version_drift
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
    from autoskillit.cli._install_info import InstallInfo, InstallType
    from autoskillit.cli.doctor import _check_source_version_drift
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
        "autoskillit.cli.update._update_checks.resolve_reference_sha", lambda info, home, **kw: sha
    )

    result = _check_source_version_drift(home=tmp_path)
    assert result.severity == Severity.OK


def test_check_source_version_drift_ok_when_cache_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When SHA cannot be resolved (network/cache miss), doctor reports OK."""
    from autoskillit.cli._install_info import InstallInfo, InstallType
    from autoskillit.cli.doctor import _check_source_version_drift
    from autoskillit.core import Severity

    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="installed123",
        requested_revision="develop",
        url=None,
        editable_source=None,
    )
    monkeypatch.setattr("autoskillit.cli._install_info.detect_install", lambda: info)
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.resolve_reference_sha",
        lambda info, home, **kw: None,
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
    from autoskillit.cli._install_info import InstallInfo, InstallType
    from autoskillit.cli.doctor import _check_source_version_drift
    from autoskillit.core import Severity

    installed_sha = "installed123abc"
    ref_sha = "reference456def"

    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id=installed_sha,
        requested_revision="develop",
        url=None,
        editable_source=None,
    )
    monkeypatch.setattr("autoskillit.cli._install_info.detect_install", lambda: info)
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.resolve_reference_sha",
        lambda info, home, **kw: ref_sha,
    )

    result = _check_source_version_drift(home=tmp_path)
    assert result.severity == Severity.WARNING
    assert installed_sha[:8] in result.message
    assert ref_sha[:8] in result.message


# T-CACHE-INTEGRITY-1: doctor detects plugin cache hooks.json with broken paths
def test_doctor_plugin_cache_integrity(tmp_path: Path) -> None:
    """_check_plugin_cache_integrity must return ERROR when cached hooks.json has broken paths."""
    import json as _json

    from autoskillit.cli.doctor._doctor_mcp import _check_plugin_cache_integrity
    from autoskillit.core import Severity

    fake_cache = tmp_path / "cache"
    version_dir = fake_cache / "0.9.347"
    version_dir.mkdir(parents=True)
    stale_hooks = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python3 {tmp_path}/hooks/quota_guard.py",
                        }
                    ],
                }
            ]
        }
    }
    (version_dir / "hooks.json").write_text(_json.dumps(stale_hooks))

    result = _check_plugin_cache_integrity(cache_dir=fake_cache)

    assert result.severity == Severity.ERROR, (
        "_check_plugin_cache_integrity must return ERROR when cached hooks.json has broken paths"
    )
    assert "quota_guard.py" in result.message


# T-CACHE-INTEGRITY-2: doctor returns OK when cached hooks.json paths all exist
def test_doctor_plugin_cache_integrity_ok_when_valid(tmp_path: Path) -> None:
    """_check_plugin_cache_integrity must return OK when all cached hook paths are valid."""
    import json as _json

    from autoskillit.cli.doctor._doctor_mcp import _check_plugin_cache_integrity
    from autoskillit.core import Severity

    fake_cache = tmp_path / "cache"
    version_dir = fake_cache / "0.9.347"
    version_dir.mkdir(parents=True)
    valid_script = tmp_path / "hooks" / "guards" / "quota_guard.py"
    valid_script.parent.mkdir(parents=True)
    valid_script.write_text("# valid")
    valid_hooks = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python3 {valid_script}",
                        }
                    ],
                }
            ]
        }
    }
    (version_dir / "hooks.json").write_text(_json.dumps(valid_hooks))

    result = _check_plugin_cache_integrity(cache_dir=fake_cache)

    assert result.severity == Severity.OK


# T-CACHE-VERSION-1: _check_cache_version_mismatch returns ERROR when kitchen open + mismatch
def test_doctor_cache_version_mismatch_with_kitchen_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_check_cache_version_mismatch must return ERROR when kitchen is open and versions differ."""
    import autoskillit.version as _ver
    from autoskillit.cli.doctor._doctor_mcp import _check_cache_version_mismatch
    from autoskillit.core import Severity

    monkeypatch.setattr("autoskillit.core.any_kitchen_open", lambda **kw: True)
    monkeypatch.setattr(
        _ver,
        "version_info",
        lambda **kw: {
            "match": False,
            "plugin_json_version": "0.9.347",
            "package_version": "0.9.351",
        },
    )

    result = _check_cache_version_mismatch()

    assert result.severity == Severity.ERROR, (
        "_check_cache_version_mismatch must return ERROR when kitchen open and version mismatch"
    )
    assert "0.9.347" in result.message
    assert "0.9.351" in result.message


# T-CACHE-VERSION-2: _check_cache_version_mismatch returns WARNING when kitchen closed + mismatch
def test_doctor_cache_version_mismatch_without_kitchen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_check_cache_version_mismatch must return WARNING (not ERROR) when kitchen is closed."""
    import autoskillit.version as _ver
    from autoskillit.cli.doctor._doctor_mcp import _check_cache_version_mismatch
    from autoskillit.core import Severity

    monkeypatch.setattr("autoskillit.core.any_kitchen_open", lambda **kw: False)
    monkeypatch.setattr(
        _ver,
        "version_info",
        lambda **kw: {
            "match": False,
            "plugin_json_version": "0.9.347",
            "package_version": "0.9.351",
        },
    )

    result = _check_cache_version_mismatch()

    assert result.severity == Severity.WARNING


# T-CACHE-VERSION-3: _check_cache_version_mismatch returns OK when versions match
def test_doctor_cache_version_mismatch_ok_when_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_check_cache_version_mismatch must return OK when versions match."""
    import autoskillit.version as _ver
    from autoskillit.cli.doctor._doctor_mcp import _check_cache_version_mismatch
    from autoskillit.core import Severity

    monkeypatch.setattr("autoskillit.core.any_kitchen_open", lambda **kw: False)
    monkeypatch.setattr(
        _ver,
        "version_info",
        lambda **kw: {
            "match": True,
            "plugin_json_version": "0.9.351",
            "package_version": "0.9.351",
        },
    )

    result = _check_cache_version_mismatch()

    assert result.severity == Severity.OK
