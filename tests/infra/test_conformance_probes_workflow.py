"""Structural tests for conformance-probes.yml workflow."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "conformance-probes.yml"
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return load_yaml(_WORKFLOW_PATH)


class TestTriggers:
    def test_schedule_trigger_present(self, workflow: dict) -> None:
        assert "schedule" in workflow[True]

    def test_schedule_cron_weekly_monday(self, workflow: dict) -> None:
        crons = workflow[True]["schedule"]
        assert any("5 * * 1" in entry["cron"] for entry in crons)

    def test_workflow_dispatch_trigger(self, workflow: dict) -> None:
        assert "workflow_dispatch" in workflow[True]

    def test_no_push_trigger(self, workflow: dict) -> None:
        assert "push" not in workflow[True]

    def test_no_pull_request_trigger(self, workflow: dict) -> None:
        assert "pull_request" not in workflow[True]


class TestPermissionsAndConcurrency:
    def test_permissions_issues_write(self, workflow: dict) -> None:
        assert workflow["permissions"]["issues"] == "write"

    def test_permissions_contents_read(self, workflow: dict) -> None:
        assert workflow["permissions"]["contents"] == "read"

    def test_concurrency_group_set(self, workflow: dict) -> None:
        assert "conformance-probes" in workflow["concurrency"]["group"]

    def test_cancel_in_progress(self, workflow: dict) -> None:
        assert workflow["concurrency"]["cancel-in-progress"] is True


class TestJobEnvironment:
    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_experimental_features_enabled(self, workflow: dict, job_name: str) -> None:
        job = workflow["jobs"][job_name]
        assert job["env"]["AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED"] == "true"

    def test_codex_probe_smoke_env(self, workflow: dict) -> None:
        assert workflow["jobs"]["codex-probe"]["env"]["CODEX_SMOKE_TEST"] == "1"

    def test_claude_probe_smoke_env(self, workflow: dict) -> None:
        assert workflow["jobs"]["claude-probe"]["env"]["CLAUDE_CODE_SMOKE_TEST"] == "1"


class TestActionPinning:
    _SHA_RE = re.compile(r"@[0-9a-f]{40}\b")

    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_all_actions_sha_pinned(self, workflow: dict, job_name: str) -> None:
        for step in workflow["jobs"][job_name].get("steps", []):
            uses = step.get("uses", "")
            if uses:
                assert self._SHA_RE.search(uses), f"Action not SHA-pinned: {uses}"

    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_setup_uv_uses_version_param(self, workflow: dict, job_name: str) -> None:
        for step in workflow["jobs"][job_name].get("steps", []):
            if "setup-uv" in step.get("uses", ""):
                with_block = step.get("with", {})
                assert "version" in with_block, "setup-uv must use 'version' param"
                assert "uv-version" not in with_block, "setup-uv must not use 'uv-version'"


class TestVersionResolution:
    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_has_resolve_version_step(self, workflow: dict, job_name: str) -> None:
        steps = workflow["jobs"][job_name]["steps"]
        version_steps = [
            s
            for s in steps
            if "resolve" in (s.get("name") or "").lower()
            and "version" in (s.get("name") or "").lower()
        ]
        assert len(version_steps) >= 1, f"No version resolution step in {job_name}"

    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_version_fallback_to_unknown(self, workflow: dict, job_name: str) -> None:
        steps = workflow["jobs"][job_name]["steps"]
        for step in steps:
            if (
                "resolve" in (step.get("name") or "").lower()
                and "version" in (step.get("name") or "").lower()
            ):
                assert "unknown" in step.get("run", ""), "Version step must fallback to 'unknown'"


class TestCacheGate:
    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_cache_restore_step(self, workflow: dict, job_name: str) -> None:
        steps = workflow["jobs"][job_name]["steps"]
        restore_steps = [s for s in steps if "cache/restore" in (s.get("uses") or "")]
        assert len(restore_steps) >= 1, f"No cache restore step in {job_name}"

    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_cache_save_step(self, workflow: dict, job_name: str) -> None:
        steps = workflow["jobs"][job_name]["steps"]
        save_steps = [s for s in steps if "cache/save" in (s.get("uses") or "")]
        assert len(save_steps) >= 1, f"No cache save step in {job_name}"

    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_probe_gated_on_cache_miss(self, workflow: dict, job_name: str) -> None:
        steps = workflow["jobs"][job_name]["steps"]
        gated_steps = [s for s in steps if "cache-hit" in (s.get("if") or "")]
        assert len(gated_steps) >= 1, f"No cache-gated probe step in {job_name}"

    @pytest.mark.parametrize(
        ("job_name", "expected_backend"),
        [("codex-probe", "codex"), ("claude-probe", "claude")],
    )
    def test_cache_key_contains_backend(
        self, workflow: dict, job_name: str, expected_backend: str
    ) -> None:
        steps = workflow["jobs"][job_name]["steps"]
        for step in steps:
            if "cache/restore" in (step.get("uses") or ""):
                key = step.get("with", {}).get("key", "")
                assert f"probe-{expected_backend}" in key


class TestPostFailure:
    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_post_failure_step_exists(self, workflow: dict, job_name: str) -> None:
        steps = workflow["jobs"][job_name]["steps"]
        failure_steps = [s for s in steps if s.get("if") == "failure()"]
        assert len(failure_steps) >= 1, f"No failure step in {job_name}"

    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_post_failure_calls_script(self, workflow: dict, job_name: str) -> None:
        steps = workflow["jobs"][job_name]["steps"]
        for step in steps:
            if step.get("if") == "failure()":
                assert "post-probe-failure.sh" in step.get("run", "")
