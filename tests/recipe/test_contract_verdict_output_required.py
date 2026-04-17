"""Contract guard: conditional-write auto-fix skills must declare a typed verdict output.

Every skill in the auto-fix family (resolve-failures, resolve-review,
resolve-claims-review, resolve-research-review) must declare a 'verdict'
output with allowed_values covering at least the four canonical values:
  real_fix, already_green, flake_suspected, ci_only_failure

diagnose-ci must declare 'failure_subtype' with at least the seven canonical values:
  flaky, timing_race, deterministic, fixture, import, env, unknown
"""

from __future__ import annotations

import pytest

from autoskillit.recipe.contracts import load_bundled_manifest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_AUTO_FIX_SKILLS = [
    "resolve-failures",
    "resolve-review",
    "resolve-claims-review",
    "resolve-research-review",
]

_REQUIRED_VERDICT_VALUES = {"real_fix", "already_green", "flake_suspected", "ci_only_failure"}
_REQUIRED_SUBTYPE_VALUES = {
    "flaky",
    "timing_race",
    "deterministic",
    "fixture",
    "import",
    "env",
    "unknown",
}


@pytest.mark.parametrize("skill_name", _AUTO_FIX_SKILLS)
def test_auto_fix_skill_write_behavior_is_conditional(skill_name: str) -> None:
    """Each auto-fix skill must declare write_behavior: conditional."""
    manifest = load_bundled_manifest()
    skill = manifest.get("skills", {}).get(skill_name)
    assert skill is not None, f"Skill '{skill_name}' not found in bundled manifest"
    assert skill.get("write_behavior") == "conditional", (
        f"Skill '{skill_name}' must have write_behavior: conditional, "
        f"got {skill.get('write_behavior')!r}"
    )


@pytest.mark.parametrize("skill_name", _AUTO_FIX_SKILLS)
def test_auto_fix_skill_declares_verdict_output(skill_name: str) -> None:
    """Each auto-fix skill must declare a 'verdict' output with allowed_values."""
    manifest = load_bundled_manifest()
    skill = manifest.get("skills", {}).get(skill_name)
    assert skill is not None, f"Skill '{skill_name}' not found in bundled manifest"
    outputs = skill.get("outputs", [])
    verdict_outputs = [o for o in outputs if o.get("name") == "verdict"]
    assert len(verdict_outputs) >= 1, (
        f"Skill '{skill_name}' must declare a 'verdict' output, "
        f"but outputs are: {[o.get('name') for o in outputs]}"
    )


@pytest.mark.parametrize("skill_name", _AUTO_FIX_SKILLS)
def test_auto_fix_skill_verdict_has_required_allowed_values(skill_name: str) -> None:
    """The 'verdict' output must include all four canonical allowed values."""
    manifest = load_bundled_manifest()
    skill = manifest.get("skills", {}).get(skill_name)
    assert skill is not None, f"Skill '{skill_name}' not found in bundled manifest"
    outputs = skill.get("outputs", [])
    verdict_outputs = [o for o in outputs if o.get("name") == "verdict"]
    assert len(verdict_outputs) >= 1, f"Skill '{skill_name}' has no 'verdict' output"
    allowed = set(verdict_outputs[0].get("allowed_values", []))
    missing = _REQUIRED_VERDICT_VALUES - allowed
    assert not missing, (
        f"Skill '{skill_name}' verdict output is missing required values: {missing}. "
        f"Current allowed_values: {allowed}"
    )


@pytest.mark.parametrize("skill_name", _AUTO_FIX_SKILLS)
def test_auto_fix_skill_verdict_type_is_string(skill_name: str) -> None:
    """The 'verdict' output must have type: string."""
    manifest = load_bundled_manifest()
    skill = manifest.get("skills", {}).get(skill_name)
    assert skill is not None, f"Skill '{skill_name}' not found in bundled manifest"
    outputs = skill.get("outputs", [])
    verdict_outputs = [o for o in outputs if o.get("name") == "verdict"]
    assert len(verdict_outputs) >= 1, f"Skill '{skill_name}' has no 'verdict' output"
    assert verdict_outputs[0].get("type") == "string", (
        f"Skill '{skill_name}' verdict output must have type: string, "
        f"got {verdict_outputs[0].get('type')!r}"
    )


@pytest.mark.parametrize("skill_name", _AUTO_FIX_SKILLS)
def test_auto_fix_skill_write_expected_when_uses_verdict(skill_name: str) -> None:
    """write_expected_when must reference verdict=real_fix (not fixes_applied)."""
    manifest = load_bundled_manifest()
    skill = manifest.get("skills", {}).get(skill_name)
    assert skill is not None, f"Skill '{skill_name}' not found in bundled manifest"
    patterns = skill.get("write_expected_when", [])
    assert any("verdict" in p for p in patterns), (
        f"Skill '{skill_name}' write_expected_when must reference 'verdict', got {patterns!r}"
    )


def test_diagnose_ci_declares_failure_subtype_output() -> None:
    """diagnose-ci must declare a 'failure_subtype' output."""
    manifest = load_bundled_manifest()
    skill = manifest.get("skills", {}).get("diagnose-ci")
    assert skill is not None, "Skill 'diagnose-ci' not found in bundled manifest"
    outputs = skill.get("outputs", [])
    subtype_outputs = [o for o in outputs if o.get("name") == "failure_subtype"]
    assert len(subtype_outputs) >= 1, (
        f"diagnose-ci must declare a 'failure_subtype' output; "
        f"current outputs: {[o.get('name') for o in outputs]}"
    )


def test_diagnose_ci_failure_subtype_has_required_values() -> None:
    """diagnose-ci failure_subtype must include all seven canonical allowed values."""
    manifest = load_bundled_manifest()
    skill = manifest.get("skills", {}).get("diagnose-ci")
    assert skill is not None, "Skill 'diagnose-ci' not found in bundled manifest"
    outputs = skill.get("outputs", [])
    subtype_outputs = [o for o in outputs if o.get("name") == "failure_subtype"]
    assert len(subtype_outputs) >= 1, "diagnose-ci has no 'failure_subtype' output"
    allowed = set(subtype_outputs[0].get("allowed_values", []))
    missing = _REQUIRED_SUBTYPE_VALUES - allowed
    assert not missing, (
        f"diagnose-ci failure_subtype is missing required values: {missing}. "
        f"Current allowed_values: {allowed}"
    )


def test_conditional_write_skills_have_verdict_or_fixes_applied_declared() -> None:
    """Generic future-proofing: every in-scope conditional-write skill must declare verdict.

    Excludes resolve-merge-conflicts (different oracle: pre-commit + manifest, out of scope)
    and retry-worktree (phases_implemented oracle, deferred to a separate Part).
    """
    _EXCLUDED = {"resolve-merge-conflicts", "retry-worktree"}
    manifest = load_bundled_manifest()
    violations: list[str] = []
    for skill_name, skill in manifest.get("skills", {}).items():
        if skill_name in _EXCLUDED:
            continue
        if skill.get("write_behavior") != "conditional":
            continue
        outputs = skill.get("outputs", [])
        output_names = {o.get("name") for o in outputs}
        if "verdict" not in output_names:
            violations.append(skill_name)
    assert not violations, (
        "The following conditional-write skills do not declare a 'verdict' output: "
        f"{violations}. Every conditional-write skill must declare verdict to enable "
        "recipe-level verdict-gated routing (prevents unconditional re-push loops)."
    )
