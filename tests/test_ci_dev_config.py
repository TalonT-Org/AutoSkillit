"""Structural enforcement: CI workflow and pre-commit configuration must contain
required quality gates. Tests here fail if enforcement infrastructure is removed.

Pattern mirrors test_version_consistency.py — reads config files and asserts
their structural properties. If a gate is deleted from the config, a test fails.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
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


class TestPtyTestGuard:
    def test_pty_wrapper_test_has_script_guard(self):
        """test_pty_wrapper_provides_tty must have a skipif guard for missing 'script' binary.

        pty_wrap_command() silently degrades to a no-op when shutil.which('script') is None.
        Without a skipif guard, the test fails with a misleading assertion error
        rather than a clear skip in minimal environments.
        """
        # Check if 'script' is available; if it is, this guard isn't exercised locally
        # but the structural assertion below still validates the test code.
        test_source = (REPO_ROOT / "tests" / "test_process_lifecycle.py").read_text()
        # The guard must reference both shutil.which and "script"
        assert (
            'shutil.which("script")' in test_source or "shutil.which('script')" in test_source
        ), (
            'test_process_lifecycle.py does not use shutil.which("script") — '
            "test_pty_wrapper_provides_tty needs a skipif guard for missing script binary"
        )
