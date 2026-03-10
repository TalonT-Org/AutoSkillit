"""Structural contract tests for the release CI workflows.

Ensures version-bump.yml and release.yml are correctly shaped, guarded,
and consistent with repo conventions.
"""

from __future__ import annotations

from pathlib import Path

import yaml
import pytest

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


# ── version-bump.yml ──────────────────────────────────────────────────────────

class TestVersionBumpWorkflow:
    def test_workflow_file_exists(self):
        assert VERSION_BUMP_WORKFLOW.exists(), (
            f"version-bump workflow not found at {VERSION_BUMP_WORKFLOW}"
        )

    def test_triggered_on_pull_request_closed_to_main(self):
        wf = _load(VERSION_BUMP_WORKFLOW)
        pr_trigger = wf.get("on", {}).get("pull_request", {})
        assert "closed" in pr_trigger.get("types", [])
        assert "main" in pr_trigger.get("branches", [])

    def test_not_triggered_on_push(self):
        """Version-bump is PR-event-based, not push-based."""
        wf = _load(VERSION_BUMP_WORKFLOW)
        assert "push" not in wf.get("on", {}), (
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

    def test_pull_requests_write_permission(self):
        wf = _load(VERSION_BUMP_WORKFLOW)
        perms = wf.get("permissions", {})
        assert perms.get("pull-requests") == "write", (
            "version-bump.yml needs pull-requests: write to open sync PRs"
        )

    def test_setup_uv_has_version_pin(self):
        wf = _load(VERSION_BUMP_WORKFLOW)
        pins = _uv_version_pins(wf)
        assert pins, "version-bump.yml must use astral-sh/setup-uv with a version pin"
        assert all(p for p in pins), "All setup-uv usages must specify uv-version"

    def test_uv_version_consistent_with_tests_yml(self):
        """uv version pin must match the pin used in tests.yml."""
        tests_wf = yaml.safe_load(
            (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text()
        )
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

    def test_syncs_main_into_integration(self):
        """Workflow must attempt to merge/sync main into integration."""
        text = VERSION_BUMP_WORKFLOW.read_text()
        assert "integration" in text
        assert "ff-only" in text or "fast-forward" in text.lower() or "merge" in text

    def test_fallback_sync_pr_creation(self):
        """Workflow must open a PR when fast-forward fails."""
        text = VERSION_BUMP_WORKFLOW.read_text()
        assert "gh pr create" in text or "pr create" in text

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
        assert "main" in ref, (
            "Checkout must use ref: main (not the default detached PR merge ref)"
        )


# ── release.yml ───────────────────────────────────────────────────────────────

class TestReleaseWorkflow:
    def test_workflow_file_exists(self):
        assert RELEASE_WORKFLOW.exists(), (
            f"release workflow not found at {RELEASE_WORKFLOW}"
        )

    def test_triggered_on_pull_request_closed_to_stable(self):
        wf = _load(RELEASE_WORKFLOW)
        pr_trigger = wf.get("on", {}).get("pull_request", {})
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
        tests_wf = yaml.safe_load(
            (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text()
        )
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
        text = RELEASE_WORKFLOW.read_text()
        # Must reference MINOR increment and reset PATCH to 0
        assert "MINOR" in text
        assert ".0" in text

    def test_creates_annotated_git_tag(self):
        """Release workflow must create an annotated tag (git tag -a)."""
        text = RELEASE_WORKFLOW.read_text()
        assert "git tag" in text
        assert "-a" in text or "annotated" in text.lower()

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
