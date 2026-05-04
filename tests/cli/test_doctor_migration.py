"""Tests for doctor quota cache schema, install classification, version consistency, and drift."""

from __future__ import annotations

import json
import textwrap
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

        from autoskillit.cli.doctor import Severity, _check_claude_process_state_breakdown

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

        from autoskillit.cli.doctor import Severity, _check_claude_process_state_breakdown

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

        from autoskillit.cli.doctor import Severity, _check_claude_process_state_breakdown

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

        from autoskillit.cli.doctor import Severity, _check_claude_process_state_breakdown

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


@pytest.mark.feature("fleet")
class TestGroupMFranchiseDoctorChecks:
    """Group M: Fleet doctor checks (ambient env detection + infra health + campaign ops)."""

    # M1: SESSION_TYPE unset → OK (unset is normal; check only fires on explicit 'skill')
    def test_check_ambient_session_type_skill_ok_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_ambient_session_type_skill
        from autoskillit.core import Severity

        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        result = _check_ambient_session_type_skill()
        assert result.severity == Severity.OK
        assert result.check == "ambient_session_type_skill"

    # M2: SESSION_TYPE=skill → WARN
    def test_check_ambient_session_type_skill_warns_when_skill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_ambient_session_type_skill
        from autoskillit.core import Severity

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        result = _check_ambient_session_type_skill()
        assert result.severity == Severity.WARNING

    # M3: SESSION_TYPE=orchestrator → OK (not this check's concern)
    def test_check_ambient_session_type_skill_ok_when_orchestrator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_ambient_session_type_skill
        from autoskillit.core import Severity

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        result = _check_ambient_session_type_skill()
        assert result.severity == Severity.OK

    # M4: SESSION_TYPE=orchestrator → WARN from orchestrator check
    def test_check_ambient_session_type_orchestrator_warns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_ambient_session_type_orchestrator
        from autoskillit.core import Severity

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        result = _check_ambient_session_type_orchestrator()
        assert result.severity == Severity.WARNING
        assert "should only be set by autoskillit CLIs" in result.message

    # M5: SESSION_TYPE=fleet → WARN from fleet check
    def test_check_ambient_session_type_fleet_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from autoskillit.cli.doctor import _check_ambient_session_type_fleet
        from autoskillit.core import Severity

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        result = _check_ambient_session_type_fleet()
        assert result.severity == Severity.WARNING
        assert "highest-privilege" in result.message

    # M6: SESSION_TYPE unset → OK for orchestrator and fleet checks
    def test_check_ambient_session_type_orchestrator_ok_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_ambient_session_type_orchestrator
        from autoskillit.core import Severity

        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        result = _check_ambient_session_type_orchestrator()
        assert result.severity == Severity.OK

    def test_check_ambient_session_type_fleet_ok_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_ambient_session_type_fleet
        from autoskillit.core import Severity

        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        result = _check_ambient_session_type_fleet()
        assert result.severity == Severity.OK

    # M7: CAMPAIGN_ID set → WARN
    def test_check_ambient_campaign_id_warns_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_ambient_campaign_id
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
        from autoskillit.cli.doctor import _check_ambient_campaign_id
        from autoskillit.core import Severity

        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        result = _check_ambient_campaign_id()
        assert result.severity == Severity.OK

    # M9: sous-chef skill dir exists → OK
    def test_check_sous_chef_bundled_ok(self) -> None:
        from autoskillit.cli.doctor import _check_sous_chef_bundled
        from autoskillit.core import Severity

        result = _check_sous_chef_bundled()
        assert result.severity == Severity.OK

    # M10: sous-chef skill dir missing → ERROR
    def test_check_sous_chef_bundled_error_when_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_sous_chef_bundled
        from autoskillit.core import Severity

        monkeypatch.setattr("autoskillit.cli.doctor._doctor_fleet.pkg_root", lambda: tmp_path)
        result = _check_sous_chef_bundled()
        assert result.severity == Severity.ERROR
        assert "sous-chef" in result.message

    # M11: fleet_dispatch_guard registered and exists → OK
    def test_check_fleet_dispatch_guard_registered_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_fleet_dispatch_guard_registered
        from autoskillit.core import Severity

        hooks_dir = tmp_path / "hooks"
        (hooks_dir / "guards").mkdir(parents=True)
        (hooks_dir / "guards" / "fleet_dispatch_guard.py").write_text("")
        monkeypatch.setattr(
            "autoskillit.cli.doctor._doctor_fleet.canonical_script_basenames",
            lambda: frozenset({"guards/fleet_dispatch_guard.py"}),
        )
        monkeypatch.setattr("autoskillit.hook_registry.HOOKS_DIR", hooks_dir)
        result = _check_fleet_dispatch_guard_registered()
        assert result.severity == Severity.OK

    # M12: fleet_dispatch_guard not registered → ERROR
    def test_check_fleet_dispatch_guard_registered_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_fleet_dispatch_guard_registered
        from autoskillit.core import Severity

        monkeypatch.setattr(
            "autoskillit.cli.doctor._doctor_fleet.canonical_script_basenames",
            lambda: frozenset(),
        )
        result = _check_fleet_dispatch_guard_registered()
        assert result.severity == Severity.ERROR
        assert "sync-hooks" in result.message

    # M13: No state files → OK
    def test_check_stale_fleet_state_ok_when_no_state(self, tmp_path: Path) -> None:
        from autoskillit.cli.doctor import _check_stale_fleet_state
        from autoskillit.core import Severity

        result = _check_stale_fleet_state(project_dir=tmp_path)
        assert result.severity == Severity.OK

    # M14: State file with running dispatch and mtime > 7d → WARN
    def test_check_stale_fleet_state_warns_on_stale(self, tmp_path: Path) -> None:
        import os
        import time

        from autoskillit.cli.doctor import _check_stale_fleet_state
        from autoskillit.core import Severity

        state_dir = tmp_path / ".autoskillit" / "temp" / "fleet" / "camp-1"
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
        result = _check_stale_fleet_state(project_dir=tmp_path)
        assert result.severity == Severity.WARNING
        assert "camp-1" in result.message or "state.json" in result.message

    # M15: State file with running dispatch and mtime < 7d → OK
    def test_check_stale_fleet_state_ok_when_fresh(self, tmp_path: Path) -> None:
        from autoskillit.cli.doctor import _check_stale_fleet_state
        from autoskillit.core import Severity

        state_dir = tmp_path / ".autoskillit" / "temp" / "fleet" / "camp-1"
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
        result = _check_stale_fleet_state(project_dir=tmp_path)
        assert result.severity == Severity.OK

    # M16: No campaigns/ dir → INFO onboarding hint
    def test_check_campaign_onboarding_hint_info_when_empty(self, tmp_path: Path) -> None:
        from autoskillit.cli.doctor import _check_campaign_onboarding_hint
        from autoskillit.core import Severity

        result = _check_campaign_onboarding_hint(project_dir=tmp_path)
        assert result.severity == Severity.INFO
        assert "make-campaign" in result.message

    # M17: campaigns/ has YAML files → OK
    def test_check_campaign_onboarding_hint_ok_when_populated(self, tmp_path: Path) -> None:
        from autoskillit.cli.doctor import _check_campaign_onboarding_hint
        from autoskillit.core import Severity

        campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
        campaigns_dir.mkdir(parents=True)
        (campaigns_dir / "my-campaign.yaml").write_text("name: my-campaign\nkind: campaign\n")
        result = _check_campaign_onboarding_hint(project_dir=tmp_path)
        assert result.severity == Severity.OK

    # M18: Duplicate clone destinations across dispatches → WARN
    def test_check_campaign_manifest_clone_dests_warns_on_duplicates(self, tmp_path: Path) -> None:
        from autoskillit.cli.doctor import _check_campaign_manifest_clone_dests
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
        from autoskillit.cli.doctor import _check_campaign_manifest_clone_dests
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
    def test_doctor_json_output_includes_fleet_checks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        cfg_dir = tmp_path / ".autoskillit"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text("features:\n  fleet: true\n")
        cli.doctor_cmd(output_json=True)
        data = json.loads(capsys.readouterr().out)
        check_names = {r["check"] for r in data["results"]}
        fleet_checks = {
            "ambient_session_type_skill",
            "ambient_session_type_orchestrator",
            "ambient_session_type_fleet",
            "ambient_campaign_id",
            "sous_chef_bundled",
            "fleet_dispatch_guard_registered",
            "stale_fleet_state",
            "campaign_onboarding_hint",
            "campaign_manifest_clone_dests",
        }
        assert fleet_checks <= check_names


@pytest.mark.feature("fleet")
class TestGroupNFeatureGateDoctorChecks:
    """N1–N8: Feature-gate checks and FleetConfig conditional validation."""

    # N1: Fleet checks skipped when feature disabled
    def test_fleet_doctor_checks_skipped_when_disabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from autoskillit.config import AutomationConfig

        mock_cfg = AutomationConfig(features={"fleet": False})
        monkeypatch.setattr("autoskillit.cli.doctor.load_config", lambda _: mock_cfg)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        cli.doctor_cmd(output_json=True)
        data = json.loads(capsys.readouterr().out)
        check_names = {r["check"] for r in data["results"]}
        fleet_infra = {
            "sous_chef_bundled",
            "fleet_dispatch_guard_registered",
            "stale_fleet_state",
            "campaign_onboarding_hint",
            "campaign_manifest_clone_dests",
        }
        assert fleet_infra.isdisjoint(check_names), (
            f"Fleet checks must be absent when feature is disabled, "
            f"but found: {fleet_infra & check_names}"
        )

    # N2: Fleet checks run when feature enabled
    def test_fleet_doctor_checks_run_when_enabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from autoskillit.config import AutomationConfig

        mock_cfg = AutomationConfig(features={"fleet": True})
        monkeypatch.setattr("autoskillit.cli.doctor.load_config", lambda _: mock_cfg)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        cli.doctor_cmd(output_json=True)
        data = json.loads(capsys.readouterr().out)
        check_names = {r["check"] for r in data["results"]}
        fleet_infra = {
            "sous_chef_bundled",
            "fleet_dispatch_guard_registered",
            "stale_fleet_state",
            "campaign_onboarding_hint",
            "campaign_manifest_clone_dests",
        }
        assert fleet_infra <= check_names
        fleet_results = [r for r in data["results"] if r["check"] in fleet_infra]
        assert all(r["severity"] in {"ok", "info"} for r in fleet_results), (
            f"Expected all fleet checks to have non-error severity (ok/info), "
            f"got: {[(r['check'], r['severity']) for r in fleet_results]}"
        )

    # N3: Ambient env checks always run even when fleet disabled
    def test_ambient_env_checks_always_run_when_fleet_disabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from autoskillit.config import AutomationConfig

        mock_cfg = AutomationConfig(features={"fleet": False})
        monkeypatch.setattr("autoskillit.cli.doctor.load_config", lambda _: mock_cfg)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        cli.doctor_cmd(output_json=True)
        data = json.loads(capsys.readouterr().out)
        check_names = {r["check"] for r in data["results"]}
        ambient_checks = {
            "ambient_session_type_skill",
            "ambient_session_type_orchestrator",
            "ambient_session_type_fleet",
            "ambient_campaign_id",
        }
        assert ambient_checks <= check_names

    # N4: Feature dependency check fires ERROR for unsatisfied dep
    def test_feature_dependency_check_fires_on_unsatisfied_dep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_feature_dependencies
        from autoskillit.core import Severity
        from autoskillit.core.types._type_constants import FeatureDef, FeatureLifecycle

        fake_feature = FeatureDef(
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
        from autoskillit.cli.doctor import _check_feature_dependencies
        from autoskillit.core import Severity
        from autoskillit.core.types._type_constants import FeatureDef, FeatureLifecycle

        fake_feature = FeatureDef(
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
        assert result.message == "All feature dependencies satisfied"

    # N6: Feature dependency check passes with empty features
    def test_feature_dependency_check_passes_with_empty_features(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_feature_dependencies
        from autoskillit.core import Severity

        monkeypatch.setattr("autoskillit.core.FEATURE_REGISTRY", {})
        result = _check_feature_dependencies({})
        assert result.severity == Severity.OK

    # N7: Feature registry consistency passes for real registry
    def test_feature_registry_consistency_passes(self) -> None:
        from autoskillit.cli.doctor import _check_feature_registry_consistency
        from autoskillit.core import Severity

        result = _check_feature_registry_consistency()
        assert result.severity == Severity.OK

    # N8: Feature registry consistency errors on bad import
    def test_feature_registry_consistency_errors_on_bad_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_feature_registry_consistency
        from autoskillit.core import Severity
        from autoskillit.core.types._type_constants import FeatureDef, FeatureLifecycle

        bad_feature = FeatureDef(
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
