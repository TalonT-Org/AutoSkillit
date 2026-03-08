"""Structural enforcement: CI workflow and pre-commit configuration must contain
required quality gates. Tests here fail if enforcement infrastructure is removed.

Pattern mirrors test_version_consistency.py — reads config files and asserts
their structural properties. If a gate is deleted from the config, a test fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
PRECOMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"


class TestPreCommitConfig:
    def test_lockfile_check_hook_present(self):
        """pre-commit config must include a uv lock --check hook.

        Without this, developers can commit a stale uv.lock undetected.
        If this test fails, add a uv-lock-check hook to .pre-commit-config.yaml.
        """
        config = yaml.safe_load(PRECOMMIT_CONFIG.read_text())
        entries = [
            hook.get("entry", "")
            for repo in config.get("repos", [])
            for hook in repo.get("hooks", [])
        ]
        assert any("uv lock" in e and "--check" in e for e in entries), (
            "Missing 'uv lock --check' hook in .pre-commit-config.yaml — "
            "add it to prevent stale lockfile commits reaching CI"
        )

    def test_ruff_tid251_configured(self):
        """pyproject.toml ruff config must include TID251 in the select list.

        TID251 enforces the logging.getLogger ban documented in test_architecture.py.
        If removed, logging violations would go undetected at pre-commit time.
        """
        pyproject = REPO_ROOT / "pyproject.toml"
        content = pyproject.read_text()
        assert "TID251" in content, (
            "TID251 missing from ruff lint.select in pyproject.toml — "
            "test_architecture.py relies on this rule being enforced by ruff at pre-commit time"
        )


class TestCIWorkflow:
    def test_lockfile_check_present_in_workflow(self):
        """CI workflow must include a 'uv lock --check' step.

        This is the CI-level backstop that catches lockfile staleness even if
        pre-commit was bypassed (direct push, git commit --no-verify, etc.).
        """
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        run_commands = [
            step.get("run", "")
            for job in workflow.get("jobs", {}).values()
            for step in job.get("steps", [])
        ]
        assert any("uv lock" in cmd and "--check" in cmd for cmd in run_commands), (
            "CI workflow does not include 'uv lock --check' — "
            "add it to a preflight job that runs before the test matrix"
        )

    def test_preflight_job_exists(self):
        """CI workflow must have a dedicated preflight job separate from the test matrix.

        A preflight job runs once on a cheap single runner and validates prerequisites
        before the matrix fans out. When it fails, only one runner fails instead of all.
        """
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        jobs = workflow.get("jobs", {})
        assert "preflight" in jobs, (
            "No 'preflight' job in CI workflow — "
            "add a preflight job with lockfile check before the test matrix"
        )

    def test_test_job_needs_preflight(self):
        """The test matrix job must declare 'needs: preflight'.

        Without this, the test matrix spins up before the lockfile check completes,
        wasting runner time on a doomed run.
        """
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        jobs = workflow.get("jobs", {})
        test_job = jobs.get("test", {})
        needs = test_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "preflight" in needs, (
            "Test matrix job does not declare 'needs: preflight' — "
            "the preflight job must complete before test runners start"
        )

    def test_install_step_includes_dev_extras(self):
        """CI install step must include --extra dev or --all-extras.

        Dev dependencies (pytest, pytest-asyncio, pytest-xdist) are declared under
        [project.optional-dependencies].dev. Without --extra dev, uv sync --locked
        installs only runtime deps, causing 'No module named pytest' when task test-all runs.

        If this test fails, change the Install dependencies step in tests.yml to:
            run: uv sync --locked --extra dev
        """
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        run_commands = [
            step.get("run", "")
            for job in workflow.get("jobs", {}).values()
            for step in job.get("steps", [])
        ]
        assert any(
            "uv sync" in cmd and ("--extra dev" in cmd or "--all-extras" in cmd)
            for cmd in run_commands
        ), (
            "CI install step does not include '--extra dev' or '--all-extras' — "
            "dev dependencies (pytest, pytest-asyncio, pytest-xdist) will not be installed, "
            "causing task test-all to fail with 'No module named pytest'"
        )

    def test_setup_uv_action_has_version_pin(self):
        """All setup-uv action usages must specify a uv-version pin.

        Without uv-version, astral-sh/setup-uv calls the GitHub API to resolve the
        latest release on every cache miss. On macOS runners, cache misses are frequent
        (the cache key includes the Python version), causing network timeout failures
        before any uv command runs.

        If this test fails, add 'uv-version: "X.Y.Z"' to all setup-uv steps in tests.yml.
        """
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        for job_name, job in workflow.get("jobs", {}).items():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if "setup-uv" in uses:
                    with_block = step.get("with", {}) or {}
                    assert "uv-version" in with_block, (
                        f"CI job '{job_name}' uses {uses!r} without a uv-version pin — "
                        "add 'uv-version: \"X.Y.Z\"' to prevent GitHub API network failures"
                        " on macOS runner cache misses"
                    )

    def test_setup_task_action_has_version_pin(self):
        """All setup-task action usages must specify a version pin.

        Without a version pin, arduino/setup-task@v2 may pick up breaking changes
        in minor releases, silently altering CI behavior. This is the same class of
        issue as unpinned setup-uv, applied to the task runner action.

        If this test fails, add 'version: "X.Y.Z"' to all setup-task steps in tests.yml.
        """
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        for job_name, job in workflow.get("jobs", {}).items():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if "setup-task" in uses:
                    with_block = step.get("with", {}) or {}
                    assert "version" in with_block, (
                        f"CI job '{job_name}' uses {uses!r} without a version pin — "
                        "add 'version: \"X.Y.Z\"' to prevent silent behavior changes"
                        " from minor releases"
                    )

    def test_ci_push_trigger_includes_integration(self) -> None:
        """CI must trigger on push to integration branch."""
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        # PyYAML parses the YAML 'on:' key as Python True (boolean)
        triggers = workflow.get(True, workflow.get("on", {}))
        push_branches = triggers["push"]["branches"]
        assert "integration" in push_branches, (
            "CI must trigger on push to integration branch — "
            "this is the permanent accumulator that also needs CI on direct pushes"
        )

    def test_ci_preflight_outputs_os_matrix(self) -> None:
        """preflight job must export an os-matrix output computed from base_ref."""
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        outputs = workflow["jobs"]["preflight"].get("outputs", {})
        assert "os-matrix" in outputs, (
            "preflight must export os-matrix so the test job can vary runners "
            "based on PR target branch"
        )

    def test_ci_test_matrix_uses_preflight_os_matrix(self) -> None:
        """test job matrix must consume the os-matrix from preflight, not a hardcoded list."""
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        matrix = workflow["jobs"]["test"]["strategy"]["matrix"]
        os_value = matrix["os"]
        assert "fromJSON" in os_value or "needs.preflight" in str(os_value), (
            "test job os matrix must be dynamic (fromJSON of preflight output), "
            "not a hardcoded list — hardcoded list cannot vary by PR target"
        )

    def test_ci_preflight_computes_matrix_from_base_ref(self) -> None:
        """preflight must contain a step that computes os matrix based on github.base_ref."""
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        steps = workflow["jobs"]["preflight"]["steps"]
        matrix_steps = [s for s in steps if "base_ref" in str(s.get("run", ""))]
        assert matrix_steps, (
            "preflight must have a step that branches on github.base_ref to produce "
            "the os-matrix output"
        )

    def test_ci_integration_target_produces_ubuntu_only_matrix(self) -> None:
        """The base_ref=integration branch must produce a single-element ubuntu matrix."""
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        steps = workflow["jobs"]["preflight"]["steps"]
        for step in steps:
            run = step.get("run", "")
            if "base_ref" in run and "integration" in run:
                assert "ubuntu-latest" in run
                assert "macos" not in run.split("integration")[1].split("\n")[0], (
                    "When base_ref is integration, matrix must contain ubuntu-latest only"
                )
                break
        else:
            pytest.fail("No step computes integration-specific matrix in preflight")


class TestPtyTestGuard:
    def test_pty_wrapper_test_has_script_guard(self):
        """test_pty_wrapper_provides_tty must have a skipif guard for missing 'script' binary.

        pty_wrap_command() silently degrades to a no-op when shutil.which('script') is None.
        Without a skipif guard, the test fails with a misleading assertion error
        rather than a clear skip in minimal environments.
        """
        # Check if 'script' is available; if it is, this guard isn't exercised locally
        # but the structural assertion below still validates the test code.
        test_source = (REPO_ROOT / "tests" / "execution" / "test_process_pty.py").read_text()
        # The guard must reference both shutil.which and "script"
        assert (
            'shutil.which("script")' in test_source or "shutil.which('script')" in test_source
        ), (
            'test_process_pty.py does not use shutil.which("script") — '
            "test_pty_wrapper_provides_tty needs a skipif guard for missing script binary"
        )
