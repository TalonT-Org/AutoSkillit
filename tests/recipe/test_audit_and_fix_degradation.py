"""Verify audit-and-fix.yaml gracefully degrades when review-pr is absent."""

from __future__ import annotations

from pathlib import Path

import yaml

RECIPE_PATH = Path(__file__).parent.parent.parent / "src/autoskillit/recipes/audit-and-fix.yaml"


def _recipe() -> dict:
    return yaml.safe_load(RECIPE_PATH.read_text())


def test_audit_and_fix_uses_autoskillit_audit_prefix() -> None:
    """audit-and-fix.yaml must reference /autoskillit:audit-* not /audit-*."""
    recipe = _recipe()
    for step in recipe.get("steps", []):
        cmd = step.get("skill_command", "")
        if "audit" in cmd and not cmd.startswith("/autoskillit:"):
            # Only flag raw /audit- references, not /autoskillit:audit-
            if cmd.startswith("/audit-"):
                raise AssertionError(
                    f"Step '{step.get('id')}' uses unbundled skill '{cmd}'. "
                    "Use '/autoskillit:audit-*' for bundled variants."
                )


def test_audit_and_fix_review_pr_step_has_skip_when_false() -> None:
    """review-pr step in audit-and-fix must have skip_when_false for graceful degradation."""
    recipe = _recipe()
    review_steps = [
        s
        for s in recipe.get("steps", [])
        if "review-pr" in s.get("skill_command", "")
        or "resolve-review" in s.get("skill_command", "")
    ]
    for step in review_steps:
        assert "skip_when_false" in step, (
            f"Step '{step.get('id')}' calls review-pr/resolve-review but has no "
            "'skip_when_false'. Add graceful degradation."
        )
