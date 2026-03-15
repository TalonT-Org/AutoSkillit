"""Structural contract tests for the release CI workflows.

Ensures version-bump.yml and release.yml are correctly shaped, guarded,
and consistent with repo conventions.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
VERSION_BUMP_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "version-bump.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

# ── helpers ───────────────────────────────────────────────────────────────────


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _uv_version_pins(workflow: dict) -> list[str]:
    """Return all uv-version values declared in setup-uv steps."""
    pins = []
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            uses = step.get("uses", "")
            if "setup-uv" in uses:
                pins.append(step.get("with", {}).get("uv-version", ""))
    return pins


def _find_step(job: dict, name_fragment: str) -> dict | None:
    """Return the first step whose name contains name_fragment (case-insensitive)."""
    return next(
        (s for s in job.get("steps", []) if name_fragment.lower() in s.get("name", "").lower()),
        None,
    )


def _find_integration_commit_step(job: dict) -> dict | None:
    """Return the integration version commit/push step."""
    return next(
        (
            s
            for s in job.get("steps", [])
            if "integration version" in s.get("name", "").lower()
            and ("commit" in s.get("name", "").lower() or "push" in s.get("name", "").lower())
        ),
        None,
    )


# ── version-bump.yml ──────────────────────────────────────────────────────────


class TestVersionBumpWorkflow:
    def test_workflow_file_exists(self):
        assert VERSION_BUMP_WORKFLOW.exists(), (
            f"version-bump workflow not found at {VERSION_BUMP_WORKFLOW}"
        )

    def test_triggered_on_pull_request_closed_to_main(self):
        wf = _load(VERSION_BUMP_WORKFLOW)
        # PyYAML parses 'on:' as boolean True (YAML 1.1); use True as key
        pr_trigger = wf.get(True, {}).get("pull_request", {})
        assert "closed" in pr_trigger.get("types", [])
        assert "main" in pr_trigger.get("branches", [])

    def test_not_triggered_on_push(self):
        """Version-bump is PR-event-based, not push-based."""
        wf = _load(VERSION_BUMP_WORKFLOW)
        # PyYAML parses 'on:' as boolean True (YAML 1.1); use True as key
        assert "push" not in wf.get(True, {}), (
            "version-bump.yml must not have a push trigger — it is PR-event-based"
        )

    def test_job_has_integration_branch_guard(self):
        """Job must only run when head.ref == 'integration'."""
        wf = _load(VERSION_BUMP_WORKFLOW)
        jobs = wf.get("jobs", {})
        assert len(jobs) >= 1
        job = next(iter(jobs.values()))
        condition = job.get("if", "")
        assert "merged" in condition, "Job must check github.event.pull_request.merged"
        assert "integration" in condition, "Job must guard on head.ref == 'integration'"

    def test_contents_write_permission(self):
        wf = _load(VERSION_BUMP_WORKFLOW)
        perms = wf.get("permissions", {})
        assert perms.get("contents") == "write", (
            "version-bump.yml needs contents: write to push commits"
        )

    def test_no_pull_requests_write_permission(self):
        """version-bump.yml no longer opens PRs — pull-requests: write must not be present."""
        wf = _load(VERSION_BUMP_WORKFLOW)
        perms = wf.get("permissions", {})
        assert "pull-requests" not in perms, (
            "version-bump.yml must not declare pull-requests: write — "
            "sync is now a force-push, not a PR"
        )

    def test_setup_uv_has_version_pin(self):
        wf = _load(VERSION_BUMP_WORKFLOW)
        pins = _uv_version_pins(wf)
        assert pins, "version-bump.yml must use astral-sh/setup-uv with a version pin"
        assert all(p for p in pins), "All setup-uv usages must specify uv-version"

    def test_uv_version_consistent_with_tests_yml(self):
        """uv version pin must match the pin used in tests.yml."""
        tests_wf = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text())
        bump_wf = _load(VERSION_BUMP_WORKFLOW)
        tests_pins = _uv_version_pins(tests_wf)
        bump_pins = _uv_version_pins(bump_wf)
        assert tests_pins and bump_pins
        assert bump_pins[0] == tests_pins[0], (
            f"uv-version in version-bump.yml ({bump_pins[0]}) must match "
            f"tests.yml ({tests_pins[0]})"
        )

    def test_pyproject_toml_is_updated(self):
        """Workflow must update pyproject.toml."""
        text = VERSION_BUMP_WORKFLOW.read_text()
        assert "pyproject.toml" in text

    def test_plugin_json_is_updated(self):
        """Workflow must update plugin.json."""
        text = VERSION_BUMP_WORKFLOW.read_text()
        assert "plugin.json" in text

    def test_uv_lock_is_regenerated(self):
        """Workflow must run uv lock."""
        text = VERSION_BUMP_WORKFLOW.read_text()
        assert "uv lock" in text

    def test_uses_github_actions_bot_identity(self):
        """Workflow must commit as github-actions[bot]."""
        text = VERSION_BUMP_WORKFLOW.read_text()
        assert "github-actions[bot]" in text

    def test_force_pushes_integration_to_main(self):
        """Workflow must force-push integration to match main after version bump."""
        wf = _load(VERSION_BUMP_WORKFLOW)
        job = next(iter(wf.get("jobs", {}).values()))
        sync_step = _find_step(job, "Force-push integration")
        assert sync_step is not None, "Workflow must have a force-push step"
        run_block = sync_step.get("run", "")
        assert "git push" in run_block, "Step must use git push"
        assert "--force" in run_block, "Step must force-push"
        assert "integration" in run_block, "Force-push target must be integration"
        assert "git merge --ff-only" not in run_block, (
            "Step must not use --ff-only merge; force-push is always safe here"
        )

    def test_checkout_uses_main_ref(self):
        """Checkout step must pin to the main branch, not the detached PR merge ref."""
        wf = _load(VERSION_BUMP_WORKFLOW)
        job = next(iter(wf.get("jobs", {}).values()))
        checkout_step = next(
            (s for s in job.get("steps", []) if "actions/checkout" in s.get("uses", "")),
            None,
        )
        assert checkout_step is not None
        ref = checkout_step.get("with", {}).get("ref", "")
        assert "main" in ref, "Checkout must use ref: main (not the default detached PR merge ref)"

    def test_integration_checkout_step_exists(self):
        wf = _load(VERSION_BUMP_WORKFLOW)
        job = next(iter(wf.get("jobs", {}).values()))
        assert _find_step(job, "Checkout integration branch") is not None

    def test_integration_version_compute_step_exists(self):
        wf = _load(VERSION_BUMP_WORKFLOW)
        job = next(iter(wf.get("jobs", {}).values()))
        assert _find_step(job, "Compute integration patch version") is not None

    def test_integration_commit_push_step_exists(self):
        wf = _load(VERSION_BUMP_WORKFLOW)
        job = next(iter(wf.get("jobs", {}).values()))
        assert _find_integration_commit_step(job) is not None

    def test_restore_protection_is_after_integration_commit(self):
        wf = _load(VERSION_BUMP_WORKFLOW)
        job = next(iter(wf.get("jobs", {}).values()))
        steps = job.get("steps", [])
        names = [s.get("name", "") for s in steps]
        restore_idx = next((i for i, n in enumerate(names) if "Restore integration" in n), None)
        assert restore_idx is not None, "Workflow must have a 'Restore integration' step"
        commit_idx = next(
            (
                i
                for i, n in enumerate(names)
                if "integration version" in n.lower() and "commit" in n.lower()
            ),
            None,
        )
        assert commit_idx is not None, "Workflow must have an integration version commit step"
        assert restore_idx > commit_idx

    def test_integration_push_is_not_force_push(self):
        wf = _load(VERSION_BUMP_WORKFLOW)
        job = next(iter(wf.get("jobs", {}).values()))
        int_commit_step = _find_integration_commit_step(job)
        assert int_commit_step is not None, (
            "Workflow must have an integration version commit/push step"
        )
        run_script = int_commit_step.get("run", "")
        assert "push --force" not in run_script
        assert "push -f " not in run_script
        assert "integration" in run_script

    def test_patch_increment_logic(self):
        """The integration version computation step uses shell $((PATCH + 1)) arithmetic."""
        wf = _load(VERSION_BUMP_WORKFLOW)
        job = next(iter(wf.get("jobs", {}).values()))
        step = _find_step(job, "Compute integration patch version")
        assert step is not None, "Workflow must have a 'Compute integration patch version' step"
        run_block = step.get("run", "")
        assert "$((PATCH + 1))" in run_block, (
            "Integration version step must use shell $((PATCH + 1)) arithmetic"
        )

    def test_patch_increment_does_not_overflow_minor(self):
        """Patch increment in the integration step must not modify MINOR."""
        wf = _load(VERSION_BUMP_WORKFLOW)
        job = next(iter(wf.get("jobs", {}).values()))
        step = _find_step(job, "Compute integration patch version")
        assert step is not None, "Workflow must have a 'Compute integration patch version' step"
        run_block = step.get("run", "")
        assert "$((MINOR + 1))" not in run_block, (
            "Integration version step must not increment MINOR — only PATCH"
        )


# ── release.yml ───────────────────────────────────────────────────────────────


class TestReleaseWorkflow:
    def test_workflow_file_exists(self):
        assert RELEASE_WORKFLOW.exists(), f"release workflow not found at {RELEASE_WORKFLOW}"

    def test_triggered_on_pull_request_closed_to_stable(self):
        wf = _load(RELEASE_WORKFLOW)
        # PyYAML parses 'on:' as boolean True (YAML 1.1); use True as key
        pr_trigger = wf.get(True, {}).get("pull_request", {})
        assert "closed" in pr_trigger.get("types", [])
        assert "stable" in pr_trigger.get("branches", [])

    def test_job_has_merged_guard(self):
        """Job must only run when the PR was actually merged."""
        wf = _load(RELEASE_WORKFLOW)
        job = next(iter(wf.get("jobs", {}).values()))
        condition = job.get("if", "")
        assert "merged" in condition

    def test_contents_write_permission(self):
        wf = _load(RELEASE_WORKFLOW)
        perms = wf.get("permissions", {})
        assert perms.get("contents") == "write"

    def test_setup_uv_has_version_pin(self):
        wf = _load(RELEASE_WORKFLOW)
        pins = _uv_version_pins(wf)
        assert pins
        assert all(p for p in pins)

    def test_uv_version_consistent_with_tests_yml(self):
        tests_wf = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text())
        release_wf = _load(RELEASE_WORKFLOW)
        tests_pins = _uv_version_pins(tests_wf)
        release_pins = _uv_version_pins(release_wf)
        assert tests_pins and release_pins
        assert release_pins[0] == tests_pins[0]

    def test_pyproject_toml_is_updated(self):
        text = RELEASE_WORKFLOW.read_text()
        assert "pyproject.toml" in text

    def test_plugin_json_is_updated(self):
        text = RELEASE_WORKFLOW.read_text()
        assert "plugin.json" in text

    def test_uv_lock_is_regenerated(self):
        text = RELEASE_WORKFLOW.read_text()
        assert "uv lock" in text

    def test_minor_version_bump_logic(self):
        """Release workflow must increment the minor version and reset patch to 0."""
        wf = _load(RELEASE_WORKFLOW)
        job = next(iter(wf.get("jobs", {}).values()))
        version_step = next(
            (s for s in job.get("steps", []) if s.get("id") == "version"),
            None,
        )
        assert version_step is not None, "Workflow must have a step with id: version"
        run_block = version_step.get("run", "")
        assert "MINOR + 1" in run_block, "Must increment MINOR by 1"
        assert ".$((MINOR + 1)).0" in run_block, "Must reset patch to 0 in new version string"

    def test_creates_annotated_git_tag(self):
        """Release workflow must create an annotated tag (git tag -a)."""
        text = RELEASE_WORKFLOW.read_text()
        assert "git tag -a" in text

    def test_tag_uses_v_prefix(self):
        """Release tag must use vX.Y.0 format."""
        text = RELEASE_WORKFLOW.read_text()
        assert "v$" in text or '"v' in text or "'v" in text

    def test_github_release_is_created(self):
        """Release workflow must create a GitHub Release."""
        text = RELEASE_WORKFLOW.read_text()
        assert "release create" in text or "gh release" in text

    def test_release_uses_generate_notes(self):
        """GitHub Release should use --generate-notes for auto-populated release body."""
        text = RELEASE_WORKFLOW.read_text()
        assert "generate-notes" in text or "generate_release_notes" in text

    def test_uses_github_actions_bot_identity(self):
        text = RELEASE_WORKFLOW.read_text()
        assert "github-actions[bot]" in text

    def test_checkout_uses_stable_ref(self):
        """Checkout must pin to stable, not the detached PR merge ref."""
        wf = _load(RELEASE_WORKFLOW)
        job = next(iter(wf.get("jobs", {}).values()))
        checkout_step = next(
            (s for s in job.get("steps", []) if "actions/checkout" in s.get("uses", "")),
            None,
        )
        assert checkout_step is not None
        ref = checkout_step.get("with", {}).get("ref", "")
        assert "stable" in ref
