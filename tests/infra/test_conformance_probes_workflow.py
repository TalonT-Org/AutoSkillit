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


def _on(workflow: dict) -> dict:
    """Return the workflow triggers section, compatible with both YAML 1.1 and 1.2 loaders.

    PyYAML SafeLoader (YAML 1.1) parses bare ``on:`` as boolean ``True``.
    A YAML 1.2-compliant loader (ruamel.yaml, etc.) preserves it as the string ``"on"``.
    Using ``get`` with both keys avoids KeyError on either loader.
    """
    return workflow.get("on") or workflow.get(True) or {}


@pytest.fixture(scope="module")
def workflow() -> dict:
    return load_yaml(_WORKFLOW_PATH)


class TestTriggers:
    def test_schedule_trigger_present(self, workflow: dict) -> None:
        assert "schedule" in _on(workflow)

    def test_schedule_cron_weekly_monday(self, workflow: dict) -> None:
        crons = _on(workflow)["schedule"]
        assert any("0 5 * * 1" in entry["cron"] for entry in crons)

    def test_workflow_dispatch_trigger(self, workflow: dict) -> None:
        assert "workflow_dispatch" in _on(workflow)

    def test_no_push_trigger(self, workflow: dict) -> None:
        assert "push" not in _on(workflow)

    def test_no_pull_request_trigger(self, workflow: dict) -> None:
        assert "pull_request" not in _on(workflow)


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

    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_live_probe_timeout_covers_default_dispatch(
        self, workflow: dict, job_name: str
    ) -> None:
        assert workflow["jobs"][job_name]["timeout-minutes"] == 75


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
        steps = workflow["jobs"][job_name].get("steps", [])
        uv_steps = [s for s in steps if "setup-uv" in (s.get("uses") or "")]
        assert uv_steps, f"No setup-uv step found in {job_name}"
        for step in uv_steps:
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
        resolve_steps = [
            s
            for s in steps
            if "resolve" in (s.get("name") or "").lower()
            and "version" in (s.get("name") or "").lower()
        ]
        assert resolve_steps, f"No version resolution step found in {job_name}"
        for step in resolve_steps:
            assert "unknown" in step.get("run", ""), "Version step must fallback to 'unknown'"


class TestCacheGate:
    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_exports_output_discipline_policy_identity(
        self, workflow: dict, job_name: str
    ) -> None:
        steps = workflow["jobs"][job_name]["steps"]
        policy_steps = [s for s in steps if s.get("id") == "resolve-policy"]
        assert len(policy_steps) == 1
        run = policy_steps[0].get("run", "")
        assert "PROBE_POLICY_IDENTITY" in run
        assert "policy_identity=${POLICY_IDENTITY}" in run
        assert "GITHUB_OUTPUT" in run

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
        restore_steps = [s for s in steps if "cache/restore" in (s.get("uses") or "")]
        assert restore_steps, f"No cache/restore step found in {job_name}"
        for step in restore_steps:
            key = step.get("with", {}).get("key", "")
            assert f"probe-{expected_backend}" in key

    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_restore_and_save_keys_include_policy_identity(
        self, workflow: dict, job_name: str
    ) -> None:
        steps = workflow["jobs"][job_name]["steps"]
        cache_steps = [
            s
            for s in steps
            if "cache/restore" in (s.get("uses") or "") or "cache/save" in (s.get("uses") or "")
        ]
        assert len(cache_steps) == 2
        for step in cache_steps:
            key = step.get("with", {}).get("key", "")
            assert "steps.resolve-policy.outputs.policy_identity" in key


class TestOutputBudgetE2E:
    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_job_runs_real_server_harness(self, workflow: dict, job_name: str) -> None:
        run_steps = [step.get("run", "") for step in workflow["jobs"][job_name]["steps"]]
        assert any("tests/server/test_output_budget_e2e.py" in run for run in run_steps)

    def test_claude_job_exports_isolated_credential(self, workflow: dict) -> None:
        steps = workflow["jobs"]["claude-probe"]["steps"]
        probe_step = next(
            step for step in steps if step.get("name") == "Run Claude Code conformance probes"
        )
        env = probe_step["env"]
        assert "ANTHROPIC_API_KEY" in env
        assert "CLAUDE_CODE_OAUTH_TOKEN" in env
        assert "No isolated Claude credential" in probe_step["run"]


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
