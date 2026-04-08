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

    def test_ci_push_trigger_excludes_integration(self) -> None:
        """Push trigger must NOT include integration — PRs from integration already
        get CI via pull_request trigger, and including it in push causes duplicate checks."""
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        # PyYAML parses the YAML 'on:' key as Python True (boolean)
        triggers = workflow.get(True, workflow.get("on", {}))
        push_branches = triggers["push"]["branches"]
        assert "integration" not in push_branches, (
            "integration must not be in push branches — "
            "it causes duplicate CI checks when a PR is open from integration"
        )

    def test_ci_pull_request_trigger_includes_integration(self) -> None:
        """PR trigger must include integration so PRs targeting it get CI."""
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        triggers = workflow.get(True, workflow.get("on", {}))
        pr_branches = triggers["pull_request"]["branches"]
        assert "integration" in pr_branches, "CI must trigger on PRs targeting integration branch"

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

    def test_ci_preflight_computes_matrix_from_branch_context(self) -> None:
        """preflight must contain a step that computes os matrix based on branch context.

        Uses github.base_ref for pull_request events and github.ref for push events,
        since github.base_ref is empty on direct push events.
        """
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        steps = workflow["jobs"]["preflight"]["steps"]
        matrix_steps = [
            s
            for s in steps
            if any(kw in str(s.get("run", "")) for kw in ("base_ref", "github.ref"))
            and "stable" in str(s.get("run", ""))
        ]
        assert matrix_steps, (
            "preflight must have a step that branches on github.base_ref (for PRs) "
            "or github.ref (for pushes) to produce the os-matrix output"
        )

    def test_ci_stable_target_produces_dual_os_matrix(self) -> None:
        """The stable branch must produce a dual-element ubuntu+macos matrix."""
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        steps = workflow["jobs"]["preflight"]["steps"]
        for step in steps:
            run = step.get("run", "")
            if "stable" in run and ("base_ref" in run or "github.ref" in run):
                assert "macos" in run, "When branch targets stable, matrix must include macOS"
                break
        else:
            pytest.fail("No step computes stable-specific matrix in preflight")

    def test_ci_push_trigger_includes_stable(self) -> None:
        """CI must trigger on push to stable branch.

        stable is the production-ready branch — direct pushes (from admin bypass or
        automated tooling) must still run CI. Without this trigger, a push to stable
        skips all checks.
        """
        workflow = yaml.safe_load(CI_WORKFLOW.read_text())
        triggers = workflow.get(True, workflow.get("on", {}))
        push_branches = triggers.get("push", {}).get("branches", [])
        assert "stable" in push_branches, (
            "CI must trigger on push to stable branch — add 'stable' to push.branches in tests.yml"
        )


class TestRecipeWorkflowField:
    def test_ci_watch_steps_carry_event_field(self):
        """All wait_for_ci steps in bundled top-level recipes must specify an event.

        Without an event field, wait_for_ci can match pull_request runs when a push
        run is expected, causing the CI watcher to report a passing status for the
        wrong trigger. The workflow field was removed in favor of config-level
        ci.workflow defaults; event discrimination must be explicit in each recipe step.
        """
        recipes_dir = REPO_ROOT / "src" / "autoskillit" / "recipes"
        for recipe_path in recipes_dir.glob("*.yaml"):
            recipe = yaml.safe_load(recipe_path.read_text())
            for step_name, step in recipe.get("steps", {}).items():
                if step.get("tool") == "wait_for_ci":
                    assert "event" in step.get("with", {}), (
                        f"{recipe_path.name}:{step_name} missing event in with: — "
                        "add 'event: \"push\"' to scope CI polling to the correct trigger event"
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
        test_source = (REPO_ROOT / "tests" / "execution" / "test_process_pty.py").read_text()
        # The guard must reference both shutil.which and "script"
        assert (
            'shutil.which("script")' in test_source or "shutil.which('script')" in test_source
        ), (
            'test_process_pty.py does not use shutil.which("script") — '
            "test_pty_wrapper_provides_tty needs a skipif guard for missing script binary"
        )
