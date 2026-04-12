"""CI workflow structural tests."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_ci_workflow_does_not_pre_regenerate_hooks_json() -> None:
    """The CI workflow must not have a 'Generate hooks.json (if absent)' step.

    That step was tautological: hooks.json is gitignored, CI generates it fresh,
    then the test compared on-disk vs generate_hooks_json() — always passing.
    The new test uses registry.sha256 (committed) which cannot be silenced by pre-regen.
    """
    import yaml

    workflow = yaml.safe_load(
        (_repo_root() / ".github" / "workflows" / "tests.yml").read_text()
    )
    step_names = [
        s.get("name")
        for job in workflow["jobs"].values()
        for s in job.get("steps", [])
    ]
    assert not any("Generate hooks.json" in (n or "") for n in step_names), (
        "CI must not pre-regenerate hooks.json — this defeats drift detection. "
        "Remove the 'Generate hooks.json (if absent)' step."
    )
