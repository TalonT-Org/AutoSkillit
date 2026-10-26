"""Structural tests for conformance-probes.yml workflow."""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml
from tests._git_inventory import git_ls_files

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "conformance-probes.yml"
)
_REPO_ROOT = _WORKFLOW_PATH.parents[2]


def _probe_cache_inputs(expression: str) -> tuple[str, ...]:
    match = re.search(r"hashFiles\(([^)]+)\)", expression)
    assert match is not None
    inputs = tuple(re.findall(r"'([^']+)'", match.group(1)))
    assert inputs
    return inputs


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
        env = workflow["jobs"]["claude-probe"]["env"]
        assert env["CLAUDE_CODE_SMOKE_TEST"] == "1"
        assert env["CLAUDE_STARTUP_READINESS_SMOKE"] == "1"

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

    def test_claude_probe_pins_supported_version_platform_rows(self, workflow: dict) -> None:
        matrix = workflow["jobs"]["claude-probe"]["strategy"]["matrix"]["include"]
        rows = {(row["os"], row["claude-version"], row["probe-tmpdir"]) for row in matrix}
        assert rows == {
            ("ubuntu-22.04", "2.1.296", "/dev/shm/pytest-probes"),
            ("macos-14", "2.1.296", "/tmp/pytest-probes"),
        }

    def test_claude_probe_installs_and_exports_exact_pinned_binary(self, workflow: dict) -> None:
        steps = workflow["jobs"]["claude-probe"]["steps"]
        install = next(step for step in steps if step.get("name") == "Install pinned Claude CLI")
        run = install["run"]
        assert "@anthropic-ai/claude-code@${{ matrix.claude-version }}" in run
        assert "CLAUDE_CODE_EXECPATH" in run
        resolve = next(step for step in steps if step.get("id") == "resolve-version")
        assert '"$CLAUDE_CODE_EXECPATH" --version' in resolve["run"]

    def test_codex_probe_installs_and_verifies_exact_pinned_cli(self, workflow: dict) -> None:
        steps = workflow["jobs"]["codex-probe"]["steps"]
        install_index, install = next(
            (index, step)
            for index, step in enumerate(steps)
            if step.get("name") == "Install pinned Codex CLI"
        )
        resolve_index, resolve = next(
            (index, step)
            for index, step in enumerate(steps)
            if step.get("id") == "resolve-version"
        )

        assert install["run"] == "npm install --global @openai/codex@0.156.1"
        assert '"codex-cli 0.156.1"' in resolve["run"]
        assert install_index < resolve_index


class TestCacheGate:
    def test_live_web_agent_gate_is_required_uncached_and_uploads_evidence(
        self, workflow: dict
    ) -> None:
        steps = workflow["jobs"]["codex-probe"]["steps"]
        gate_index, gate = next(
            (index, step)
            for index, step in enumerate(steps)
            if step.get("name") == "Run authenticated live-web agent gate"
        )
        upload_index, upload = next(
            (index, step)
            for index, step in enumerate(steps)
            if step.get("name") == "Upload live-web agent gate evidence"
        )
        restore_index = next(
            index
            for index, step in enumerate(steps)
            if "cache/restore" in (step.get("uses") or "")
        )

        assert "if" not in gate
        assert gate.get("continue-on-error") is not True
        assert "task test-smoke-codex-web-agent-live-gate" in gate["run"]
        assert gate_index < upload_index < restore_index
        assert upload["if"] == "always()"
        assert upload["with"]["name"] == "codex-live-web-agent-gate-0.156.1"
        assert upload["with"]["path"].endswith("/live-web-agent-gate.json")
        assert upload["with"]["if-no-files-found"] == "error"
        assert upload["with"]["retention-days"] == 7

    def test_live_explorer_mcp_gate_is_required_before_attestation_upload(
        self, workflow: dict
    ) -> None:
        steps = workflow["jobs"]["codex-probe"]["steps"]
        gate_index, gate = next(
            (index, step)
            for index, step in enumerate(steps)
            if step.get("name") == "Run authenticated production explorer MCP gate"
        )
        evidence_upload_index, evidence_upload = next(
            (index, step)
            for index, step in enumerate(steps)
            if step.get("name") == "Upload production explorer live-gate evidence"
        )
        attestation_upload_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Upload Luna explorer conformance attestation"
        )

        assert "if" not in gate
        assert gate.get("continue-on-error") is not True
        assert "task test-smoke-codex-explorer-live-gate" in gate["run"]
        assert gate_index < evidence_upload_index < attestation_upload_index
        assert evidence_upload["if"] == "always()"
        assert evidence_upload["with"]["if-no-files-found"] == "error"
        assert evidence_upload["with"]["path"].endswith("/live-explorer-gate.json")

    def test_luna_explorer_gate_is_non_cached_and_publishes_attestation(
        self, workflow: dict
    ) -> None:
        steps = workflow["jobs"]["codex-probe"]["steps"]
        gate_index, gate = next(
            (index, step)
            for index, step in enumerate(steps)
            if step.get("name") == "Run non-cached Luna explorer capability gate"
        )
        restore_index = next(
            index
            for index, step in enumerate(steps)
            if "cache/restore" in (step.get("uses") or "")
        )
        upload_index, upload = next(
            (index, step)
            for index, step in enumerate(steps)
            if step.get("name") == "Upload Luna explorer conformance attestation"
        )
        assert "if" not in gate
        assert gate.get("continue-on-error") is not True
        assert "task test-smoke-codex-explorer-gate" in gate["run"]
        assert gate_index < upload_index < restore_index
        assert upload["with"]["if-no-files-found"] == "error"
        assert upload["with"]["path"] == ".autoskillit/temp/conformance/"

    def test_codex_config_parse_gate_precedes_cache_and_live_probe(self, workflow: dict) -> None:
        steps = workflow["jobs"]["codex-probe"]["steps"]
        parse_index, parse_step = next(
            (index, step)
            for index, step in enumerate(steps)
            if step.get("name") == "Validate installed Codex config parsing"
        )
        restore_index = next(
            index
            for index, step in enumerate(steps)
            if "cache/restore" in (step.get("uses") or "")
        )
        live_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Run Codex conformance probes"
        )

        assert parse_step.get("run") == "task test-codex-config-parse"
        assert "if" not in parse_step
        assert parse_step.get("continue-on-error") is not True
        assert parse_index < restore_index < live_index

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

    @pytest.mark.parametrize(
        ("job_name", "backend", "probe_step_name"),
        [
            ("codex-probe", "codex", "Run Codex conformance probes"),
            ("claude-probe", "claude", "Run Claude Code conformance probes"),
        ],
    )
    def test_cache_is_exact_and_invalidated_by_all_probe_inputs(
        self, workflow: dict, job_name: str, backend: str, probe_step_name: str
    ) -> None:
        steps = workflow["jobs"][job_name]["steps"]
        checkout_index = next(
            index
            for index, step in enumerate(steps)
            if (step.get("uses") or "").startswith("actions/checkout@")
        )
        restore_steps = [
            (index, step)
            for index, step in enumerate(steps)
            if "cache/restore" in (step.get("uses") or "")
        ]
        save_steps = [step for step in steps if "cache/save" in (step.get("uses") or "")]
        assert len(restore_steps) == 1
        assert len(save_steps) == 1
        restore_index, restore = restore_steps[0]
        save = save_steps[0]
        probe = next(step for step in steps if step.get("name") == probe_step_name)

        key_prefix = (
            f"probe-{backend}-${{{{ steps.resolve-version.outputs.cli_version }}}}-"
            "${{ steps.resolve-policy.outputs.policy_identity }}-${{ runner.os }}-"
        )
        key = restore["with"]["key"]
        cache_inputs = _probe_cache_inputs(key)
        expected_cache_inputs = {
            "path": f".autoskillit/temp/probe-cache/{backend}",
            "key": key,
        }

        assert checkout_index < restore_index
        assert key.startswith(key_prefix)
        assert restore["with"] == expected_cache_inputs
        assert save["with"] == expected_cache_inputs
        targets = re.findall(r"\btests/[\w/.-]+\.py\b", probe["run"])
        assert targets
        assert all(
            any(fnmatchcase(target, pattern) for pattern in cache_inputs) for target in targets
        )
        assert {
            "src/autoskillit/**",
            "tests/**",
            ".autoskillit/recipes/**",
            "pyproject.toml",
            "uv.lock",
            ".github/workflows/conformance-probes.yml",
        } <= set(cache_inputs)
        assert probe["if"] == "steps.restore-cache.outputs.cache-hit != 'true'"
        assert save["if"] == "success() && steps.restore-cache.outputs.cache-hit != 'true'"

    @pytest.mark.parametrize("job_name", ["codex-probe", "claude-probe"])
    def test_cache_hash_inputs_match_tracked_repository_files(
        self, workflow: dict, job_name: str
    ) -> None:
        restore = next(
            step
            for step in workflow["jobs"][job_name]["steps"]
            if "cache/restore" in (step.get("uses") or "")
        )
        for pathspec in _probe_cache_inputs(restore["with"]["key"]):
            assert git_ls_files(_REPO_ROOT, pathspec), pathspec


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
                if step.get("run"):
                    assert "post-probe-failure.sh" in step["run"]

    def test_claude_always_uploads_bounded_startup_traces(self, workflow: dict) -> None:
        steps = workflow["jobs"]["claude-probe"]["steps"]
        upload = next(
            step for step in steps if step.get("name") == "Upload bounded startup-readiness traces"
        )
        assert upload["if"] == "always()"
        assert "upload-artifact@" in upload["uses"]
        assert upload["with"]["path"].endswith("*")
        assert upload["with"]["retention-days"] == 14
