"""Tests for fleet doctor checks — Group M ambient env/infra/campaign, Group N feature gates."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from autoskillit import cli

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


@pytest.mark.feature("fleet")
class TestGroupMFranchiseDoctorChecks:
    """Group M: Fleet doctor checks (ambient env detection + infra health + campaign ops)."""

    # M1: SESSION_TYPE unset → OK (unset is normal; check fires on 'skill' and deprecated 'leaf')
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
        assert result.check == "ambient_session_type_skill"

    # M2b: SESSION_TYPE=leaf (removed) → ERROR
    def test_check_ambient_session_type_skill_errors_when_leaf(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor import _check_ambient_session_type_skill
        from autoskillit.core import Severity

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
        result = _check_ambient_session_type_skill()
        assert result.severity == Severity.ERROR
        assert result.check == "ambient_session_type_skill"

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
        from autoskillit.core.types._type_constants_features import FeatureDef
        from autoskillit.core.types._type_enums import FeatureLifecycle

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
        from autoskillit.core.types._type_constants_features import FeatureDef
        from autoskillit.core.types._type_enums import FeatureLifecycle

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
        from autoskillit.core.types._type_constants_features import FeatureDef
        from autoskillit.core.types._type_enums import FeatureLifecycle

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
