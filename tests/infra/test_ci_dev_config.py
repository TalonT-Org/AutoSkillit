"""Structural enforcement: CI workflow and pre-commit configuration must contain
required quality gates. Tests here fail if enforcement infrastructure is removed.

Pattern mirrors test_version_consistency.py — reads config files and asserts
their structural properties. If a gate is deleted from the config, a test fails.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).parent.parent.parent
PRECOMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"
CONFTEST_PATH = REPO_ROOT / "tests" / "conftest.py"


class TestPreCommitConfig:
    def test_lockfile_check_hook_present(self):
        """pre-commit config must include a uv lock --check hook.

        Without this, developers can commit a stale uv.lock undetected.
        If this test fails, add a uv-lock-check hook to .pre-commit-config.yaml.
        """
        config = load_yaml(PRECOMMIT_CONFIG)
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

    def test_sub_claude_md_hook_present(self):
        """pre-commit config must include a sub-CLAUDE.md completeness check hook.

        Without this, agents can commit new .py files without updating the
        directory's CLAUDE.md file table, causing CI test failures.
        """
        config = load_yaml(PRECOMMIT_CONFIG)
        hooks = [hook for repo in config.get("repos", []) for hook in repo.get("hooks", [])]
        sub_claude_hooks = [h for h in hooks if "check_sub_claude_md" in h.get("entry", "")]
        assert sub_claude_hooks, (
            "Missing 'check-sub-claude-md' hook in .pre-commit-config.yaml — "
            "add it to catch sub-CLAUDE.md coverage gaps before CI"
        )
        hook = sub_claude_hooks[0]
        assert hook.get("pass_filenames") is False, (
            "check-sub-claude-md hook must use pass_filenames: false — "
            "the script does its own filesystem scan"
        )

    def test_contract_staleness_pre_commit_hook_exists(self):
        """pre-commit config must include a check-contract-freshness hook.

        Without this, recipe YAML edits that staleness-change contract cards
        go undetected until CI runs the staleness test.
        """
        config = load_yaml(PRECOMMIT_CONFIG)
        hooks = [hook for repo in config.get("repos", []) for hook in repo.get("hooks", [])]
        freshness_hooks = [h for h in hooks if "check_contract_freshness" in h.get("entry", "")]
        assert freshness_hooks, (
            "Missing 'check-contract-freshness' hook in .pre-commit-config.yaml — "
            "add it to catch recipe YAML changes that staleness-change contract cards before CI"
        )
        hook = freshness_hooks[0]
        assert hook.get("pass_filenames") is False, (
            "check-contract-freshness hook must use pass_filenames: false — "
            "the script does its own filesystem scan"
        )

    def test_per_file_ignores_e501_bounded(self):
        """E501 exemptions in per-file-ignores must not exceed the established cap.

        Adding new E501 exemptions grows technical debt — fix violations instead.
        If this test fails, refactor the long lines rather than adding a new exemption.
        """
        with (REPO_ROOT / "pyproject.toml").open("rb") as f:
            config = tomllib.load(f)
        per_file_ignores = (
            config.get("tool", {}).get("ruff", {}).get("lint", {}).get("per-file-ignores", {})
        )
        e501_count = sum(1 for rules in per_file_ignores.values() if "E501" in rules)
        _E501_EXEMPTION_CAP = 19
        assert e501_count <= _E501_EXEMPTION_CAP, (
            f"E501 exemptions in per-file-ignores exceeded cap of {_E501_EXEMPTION_CAP}: "
            f"found {e501_count} entries. Refactor long lines instead of adding exemptions."
        )


class TestCIWorkflow:
    def test_lockfile_check_present_in_workflow(self):
        """CI workflow must include a 'uv lock --check' step.

        This is the CI-level backstop that catches lockfile staleness even if
        pre-commit was bypassed (direct push, git commit --no-verify, etc.).
        """
        workflow = load_yaml(CI_WORKFLOW)
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
        workflow = load_yaml(CI_WORKFLOW)
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
        workflow = load_yaml(CI_WORKFLOW)
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
        workflow = load_yaml(CI_WORKFLOW)
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
        """All setup-uv action usages must specify a version pin.

        Without version, astral-sh/setup-uv calls the GitHub API to resolve the
        latest release on every cache miss. On macOS runners, cache misses are frequent
        (the cache key includes the Python version), causing network timeout failures
        before any uv command runs.

        If this test fails, add 'version: "X.Y.Z"' to all setup-uv steps in tests.yml.
        Note: the correct input parameter is 'version', not 'uv-version' — 'uv-version'
        is an output of the action and is silently ignored as an input.
        """
        workflow = load_yaml(CI_WORKFLOW)
        for job_name, job in workflow.get("jobs", {}).items():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if "setup-uv" in uses:
                    with_block = step.get("with", {}) or {}
                    assert "version" in with_block, (
                        f"CI job '{job_name}' uses {uses!r} without a version pin — "
                        "add 'version: \"X.Y.Z\"' to prevent GitHub API network failures"
                        " on macOS runner cache misses"
                    )

    def test_setup_task_action_has_version_pin(self):
        """All setup-task action usages must specify a version pin.

        Without a version pin, arduino/setup-task@v2 may pick up breaking changes
        in minor releases, silently altering CI behavior. This is the same class of
        issue as unpinned setup-uv, applied to the task runner action.

        If this test fails, add 'version: "X.Y.Z"' to all setup-task steps in tests.yml.
        """
        workflow = load_yaml(CI_WORKFLOW)
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

    def test_ci_push_trigger_excludes_develop(self) -> None:
        """Push trigger must NOT include develop — PRs from develop already
        get CI via pull_request trigger, and including it in push causes duplicate checks."""
        workflow = load_yaml(CI_WORKFLOW)
        # PyYAML parses the YAML 'on:' key as Python True (boolean)
        triggers = workflow.get(True, workflow.get("on", {}))
        push_branches = triggers["push"]["branches"]
        assert "develop" not in push_branches, (
            "develop must not be in push branches — "
            "it causes duplicate CI checks when a PR is open from develop"
        )

    def test_ci_pull_request_trigger_includes_develop(self) -> None:
        """PR trigger must include develop so PRs targeting it get CI."""
        workflow = load_yaml(CI_WORKFLOW)
        triggers = workflow.get(True, workflow.get("on", {}))
        pr_branches = triggers["pull_request"]["branches"]
        assert "develop" in pr_branches, "CI must trigger on PRs targeting develop branch"

    def test_ci_preflight_outputs_os_matrix(self) -> None:
        """preflight job must export an os-matrix output computed from base_ref."""
        workflow = load_yaml(CI_WORKFLOW)
        outputs = workflow["jobs"]["preflight"].get("outputs", {})
        assert "os-matrix" in outputs, (
            "preflight must export os-matrix so the test job can vary runners "
            "based on PR target branch"
        )

    def test_ci_test_matrix_uses_preflight_os_matrix(self) -> None:
        """test job matrix must consume the os-matrix from preflight, not a hardcoded list."""
        workflow = load_yaml(CI_WORKFLOW)
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
        workflow = load_yaml(CI_WORKFLOW)
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
        workflow = load_yaml(CI_WORKFLOW)
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
        workflow = load_yaml(CI_WORKFLOW)
        triggers = workflow.get(True, workflow.get("on", {}))
        push_branches = triggers.get("push", {}).get("branches", [])
        assert "stable" in push_branches, (
            "CI must trigger on push to stable branch — add 'stable' to push.branches in tests.yml"
        )

    def test_ruff_check_in_preflight_job(self):
        """CI preflight job must include a ruff check step.

        Without ruff in CI, lint violations that bypass pre-commit reach the repository
        unchallenged. If this test fails, add a 'uvx ruff check' step to the preflight job.
        """
        workflow = load_yaml(CI_WORKFLOW)
        preflight_steps = workflow["jobs"]["preflight"]["steps"]
        assert any("ruff check" in step.get("run", "") for step in preflight_steps), (
            "CI preflight job has no 'ruff check' step — "
            "add it after the lockfile check to create a hard lint gate"
        )


class TestRecipeWorkflowField:
    def test_ci_watch_event_value_is_captured_or_absent(self):
        """Every wait_for_ci step's event value must either be:
          (a) a template reference to an upstream-captured variable
              (e.g. ${{ context.ci_event }}), OR
          (b) absent (matches any event — the ci.py default when scope.event is None).
        Hardcoded literals ('push', 'pull_request', 'merge_group') are forbidden because
        they create capture inversions when the repo's actual trigger differs."""
        recipes_dir = REPO_ROOT / "src" / "autoskillit" / "recipes"
        for recipe_path in recipes_dir.glob("*.yaml"):
            recipe = load_yaml(recipe_path)
            for step_name, step in recipe.get("steps", {}).items():
                if step.get("tool") != "wait_for_ci":
                    continue
                event = step.get("with", {}).get("event")
                if event is None:
                    continue  # absent is fine — matches any event
                assert event.startswith("${{") and "context." in event, (
                    f"{recipe_path.name}:{step_name} — event must be a captured context "
                    f"reference or absent, not {event!r}"
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


class TestCIWorkflowExpressions:
    def test_ci_experimental_expression_covers_merge_group(self):
        """The AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED expression must
        handle merge_group events, not just pull_request.

        Without merge_group handling, the expression evaluates to 'false'
        in the merge queue for develop-targeting PRs, incorrectly disabling
        experimental features.
        """
        workflow = load_yaml(CI_WORKFLOW)
        jobs = workflow["jobs"]
        test_job = jobs["test"]

        # Find the Run tests step
        run_step = None
        for step in test_job["steps"]:
            if step.get("name") == "Run tests":
                run_step = step
                break

        assert run_step is not None, "Run tests step not found"

        # Check env block for AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED
        env_block = run_step.get("env", {})
        exp_val = env_block.get("AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED")

        assert exp_val is not None, (
            "AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED must be set in the "
            "Run tests step env block"
        )

        exp_str = str(exp_val)
        assert "merge_group" in exp_str, (
            "AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED expression must "
            "handle merge_group events. Current expression: " + exp_str
        )


class TestCIEnvVarIsolation:
    """Verify that every AUTOSKILLIT_* env var set in CI has a
    corresponding cleanup mechanism in the test conftest."""

    def test_ci_env_vars_have_conftest_cleanup(self):
        """Every AUTOSKILLIT_* env var set in CI workflow env blocks
        must have a corresponding monkeypatch.delenv autouse fixture
        in tests/conftest.py, OR be covered by a prefix-based cleanup.

        This prevents Dynaconf env var leakage into tests.
        """
        workflow = load_yaml(CI_WORKFLOW)
        conftest_text = CONFTEST_PATH.read_text()

        # Collect all AUTOSKILLIT_* env vars from CI
        ci_env_vars = set()
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                env = step.get("env", {})
                for key in env:
                    if key.startswith("AUTOSKILLIT_"):
                        ci_env_vars.add(key)
                run_block = step.get("run", "")
                # Also check inline exports
                for line in run_block.splitlines():
                    if "export AUTOSKILLIT_" in line:
                        parts = line.split("AUTOSKILLIT_", 1)
                        if len(parts) > 1:
                            var_name = "AUTOSKILLIT_" + parts[1].split("=")[0]
                            ci_env_vars.add(var_name.strip())

        missing = []
        for var in sorted(ci_env_vars):
            if "__" in var:
                prefix = var.rsplit("__", 1)[0] + "__"
                covered = var in conftest_text or prefix in conftest_text
            else:
                covered = var in conftest_text
            if not covered:
                missing.append(var)

        assert not missing, (
            f"CI env vars missing conftest cleanup: {missing}. "
            "Add a monkeypatch.delenv autouse fixture in tests/conftest.py "
            "or extend a prefix-based cleanup fixture."
        )


class TestSetupUvVersionPin:
    def test_setup_uv_version_pin_all_workflows(self):
        """Every workflow file that uses setup-uv must use the 'version'
        input parameter, not 'uv-version' (which is an output, not an input).

        This extends the existing test_setup_uv_action_has_version_pin to
        cover ALL workflow files, not just tests.yml.
        """
        workflows_dir = CI_WORKFLOW.parent
        violations = []
        for wf_path in sorted(workflows_dir.glob("*.yml")):
            workflow = load_yaml(wf_path)
            for job_name, job in workflow.get("jobs", {}).items():
                for step in job.get("steps", []):
                    uses = step.get("uses", "")
                    if "setup-uv" in uses:
                        with_block = step.get("with", {})
                        if "uv-version" in with_block:
                            violations.append(
                                f"{wf_path.name}:{job_name}: uses 'uv-version' "
                                f"(output) instead of 'version' (input)"
                            )
                        elif "version" not in with_block:
                            violations.append(f"{wf_path.name}:{job_name}: missing 'version' pin")

        assert not violations, "setup-uv version pin violations:\n" + "\n".join(violations)


class TestRuffClean:
    def test_ruff_check_clean(self):
        """ruff check must exit 0 on the full codebase — no E501 or F811 violations.

        This is the structural gate that ensures lint violations are fixed, not suppressed.
        If this test fails, run 'uv run ruff check' locally to see violations and fix them.
        """
        result = subprocess.run(
            ["uv", "run", "ruff", "check", str(REPO_ROOT)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, f"ruff check found violations:\n{result.stdout}"
