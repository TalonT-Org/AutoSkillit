"""Admission truth-telling regression tests.

Tests for the class of bug where recipe admission (`load_and_validate`) and
runtime dispatch (`run_skill`) made independent backend-compatibility decisions
using different information. The four test classes below enforce the contract
that admission agrees with dispatch and that resolver-dependent findings
survive pruning filter erasure.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autoskillit.core import Severity, SkillSource
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe._rule_helpers import filter_pruning_false_positives
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Shared fixtures — fake resolver that satisfies both SkillLister and
# SkillResolver protocols.
# ---------------------------------------------------------------------------


def _make_skill_info(
    name: str = "git-only-skill",
    backend_requirements: frozenset[str] = frozenset({"claude-code"}),
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        source=SkillSource.BUNDLED_EXTENDED,
        path=Path("/nonexistent/SKILL.md"),
        categories=frozenset(),
        backend_requirements=backend_requirements,
    )


def _make_recipe_with_skill_step(skill_command: str) -> Recipe:
    steps = {
        "run-skill-step": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": skill_command, "cwd": "/tmp"},
        )
    }
    return Recipe(name="test-recipe", description="test", steps=steps)


def _mock_resolver(skill_info: SimpleNamespace | None) -> MagicMock:
    resolver = MagicMock()
    resolver.resolve.return_value = skill_info
    resolver.list_all.return_value = [skill_info] if skill_info else []
    return resolver


# ---------------------------------------------------------------------------
# Test 1a: filter_pruning_false_positives must not erase resolver-dependent
# ERROR findings. The pre-prune ValidationContext now carries skill_resolver
# and effective_backend_map so backend-incompatible-skill findings appear in
# the baseline and survive the intersection.
# ---------------------------------------------------------------------------


class TestFilterPruningScope:
    def test_backend_compat_finding_survives_filter_when_resolver_in_pre_prune(self):
        """backend-incompatible-skill findings from the post-prune pass must
        survive the intersection with the pre-prune baseline when both
        contexts include skill_resolver and effective_backend_map."""
        skill_info = _make_skill_info()  # claude-code-only
        recipe = _make_recipe_with_skill_step("/git-only-skill something")
        resolver = _mock_resolver(skill_info)

        # Pre-prune context WITH resolver (mirrors the fix at _api.py:376)
        pre_ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            effective_backend_map={"run-skill-step": "codex"},
            available_skills=frozenset({"git-only-skill"}),
        )
        pre_findings = run_semantic_rules(pre_ctx)
        pre_compat = [f for f in pre_findings if f.rule == "backend-incompatible-skill"]
        assert len(pre_compat) >= 1, (
            "Pre-prune context with skill_resolver must produce backend-compat findings"
        )

        # Post-prune context — same shape; same findings produced
        post_ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            effective_backend_map={"run-skill-step": "codex"},
            available_skills=frozenset({"git-only-skill"}),
        )
        post_findings = run_semantic_rules(post_ctx)

        survived = filter_pruning_false_positives(post_findings, pre_findings)
        survived_compat = [f for f in survived if f.rule == "backend-incompatible-skill"]
        assert len(survived_compat) >= 1, (
            "filter_pruning_false_positives erased backend-incompatible-skill "
            "findings — the filter scope was applied indiscriminately"
        )

    def test_legacy_resolverless_prune_erases_finding(self):
        """Sanity check: when the pre-prune context has NO resolver, the
        filter intersects with an empty baseline and erases the post-prune
        finding. This is the historical bug being fixed."""
        skill_info = _make_skill_info()
        recipe = _make_recipe_with_skill_step("/git-only-skill something")
        resolver = _mock_resolver(skill_info)

        # Pre-prune WITHOUT resolver (the historical bug shape)
        pre_ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=None,
            effective_backend_map=None,
            available_skills=frozenset({"git-only-skill"}),
        )
        pre_findings = run_semantic_rules(pre_ctx)
        assert not [f for f in pre_findings if f.rule == "backend-incompatible-skill"]

        # Post-prune WITH resolver
        post_ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            effective_backend_map={"run-skill-step": "codex"},
            available_skills=frozenset({"git-only-skill"}),
        )
        post_findings = run_semantic_rules(post_ctx)
        post_compat = [f for f in post_findings if f.rule == "backend-incompatible-skill"]
        assert len(post_compat) >= 1

        # Filter with empty pre-prune → erases the finding (the historical bug)
        survived = filter_pruning_false_positives(post_findings, pre_findings)
        assert not [f for f in survived if f.rule == "backend-incompatible-skill"]


# ---------------------------------------------------------------------------
# Test 1b: backend-incompatible-skill must evaluate against effective backend,
# not raw backend. A provider-overridden step is NOT flagged.
# ---------------------------------------------------------------------------


class TestEffectiveBackendAwareness:
    def test_provider_overridden_step_not_flagged(self):
        """A step whose effective backend is claude-code (via ANTHROPIC_BASE_URL)
        must NOT be flagged even when ctx.backend_name is codex."""
        skill_info = _make_skill_info()  # claude-code-only
        recipe = _make_recipe_with_skill_step("/git-only-skill something")
        resolver = _mock_resolver(skill_info)

        ctx = make_validation_context(
            recipe,
            backend_name="codex",  # orchestrator is codex
            skill_resolver=resolver,
            effective_backend_map={"run-skill-step": "claude-code"},  # but step is covered
            available_skills=frozenset({"git-only-skill"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 0, (
            f"Expected zero compat findings because the step has an effective "
            f"backend of claude-code (via ANTHROPIC_BASE_URL), got: "
            f"{[f.message for f in compat_findings]}"
        )

    def test_uncovered_step_flagged(self):
        """A step without a provider override remains flagged when the raw
        backend is codex and the skill requires claude-code."""
        skill_info = _make_skill_info()
        recipe = _make_recipe_with_skill_step("/git-only-skill something")
        resolver = _mock_resolver(skill_info)

        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            effective_backend_map={"run-skill-step": "codex"},  # NOT overridden
            available_skills=frozenset({"git-only-skill"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 1
        assert compat_findings[0].severity == Severity.ERROR
        assert "codex" in compat_findings[0].message

    def test_step_missing_from_map_falls_back_to_backend_name(self):
        """When a step is absent from effective_backend_map but the map is
        provided, the rule falls back to ctx.backend_name (standard behavior).
        """
        skill_info = _make_skill_info()
        recipe = _make_recipe_with_skill_step("/git-only-skill something")
        resolver = _mock_resolver(skill_info)

        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            effective_backend_map={},  # step not in map → falls back to backend_name
            available_skills=frozenset({"git-only-skill"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 1

    def test_none_step_backend_skipped(self):
        """Defense-in-depth: if step_backend is None for any reason, the rule
        skips that step rather than silently flagging it (because
        ``None not in frozenset(...)`` evaluates True)."""
        skill_info = _make_skill_info()
        recipe = _make_recipe_with_skill_step("/git-only-skill something")
        resolver = _mock_resolver(skill_info)

        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            effective_backend_map={"run-skill-step": None},  # type: ignore[dict-item]
            available_skills=frozenset({"git-only-skill"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 0, (
            "step_backend=None must be skipped (not flagged); got: "
            f"{[f.message for f in compat_findings]}"
        )


# ---------------------------------------------------------------------------
# Test 1c/2d: Admission ↔ dispatch agreement. For every bundled recipe x
# backend x provider-profile-shape, if admission says dispatch_feasible=True
# and a step survives pruning, that step's effective backend must satisfy
# skill requirements at dispatch time.
#
# This is implemented in tests/server/test_admission_dispatch_agreement.py
# and uses the real DefaultSkillResolver against installed SKILL.md files.
# Here we exercise the agreement contract on synthetic recipes.
# ---------------------------------------------------------------------------


class TestAdmissionDispatchAgreementSynthetic:
    """Synthetic admission-dispatch agreement tests.

    Real-recipe cross-validation lives in
    `tests/server/test_admission_dispatch_agreement.py`. These tests pin the
    contract on synthetic recipes so failures are localized and reproducible.
    """

    @staticmethod
    def _is_backend_incompatible(skill_info: object, effective_backend: str) -> bool:
        reqs = getattr(skill_info, "backend_requirements", None)
        return bool(reqs and effective_backend not in reqs)

    def test_admitted_pipeline_passes_dispatch_gate(self):
        """A recipe that admission considers dispatch_feasible=True with no
        backend-incompatible-skill findings must also pass the dispatch-time
        gate for every surviving run_skill step."""

        # Compatible setup: claude-code backend, claude-code skill requirement
        skill_info = _make_skill_info(backend_requirements=frozenset({"claude-code"}))
        recipe = _make_recipe_with_skill_step("/git-only-skill something")
        resolver = _mock_resolver(skill_info)

        ctx = make_validation_context(
            recipe,
            backend_name="claude-code",
            skill_resolver=resolver,
            effective_backend_map={"run-skill-step": "claude-code"},
            available_skills=frozenset({"git-only-skill"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        error_severity = [
            f for f in compat_findings if getattr(f, "severity", None) == Severity.ERROR
        ]
        assert len(error_severity) == 0, (
            f"Admission fired backend-incompatible-skill ERROR: "
            f"{[f.message for f in error_severity]}"
        )

        # Admission says feasible → dispatch must agree
        for step_name, step in ctx.recipe.steps.items():
            if step.tool != "run_skill":
                continue
            step_backend = (
                ctx.effective_backend_map.get(step_name, ctx.backend_name)
                if ctx.effective_backend_map
                else ctx.backend_name
            )
            assert skill_info is not None, "skill_info must be resolvable"
            assert step_backend is not None, "step_backend must not be None"
            assert self._is_backend_incompatible(skill_info, step_backend) is False, (
                f"Dispatch gate fails for step {step_name} on {step_backend} — "
                f"admission and dispatch disagree"
            )


# ---------------------------------------------------------------------------
# Test 1d: All git_metadata_write skill steps must be either guarded by
# skip_when_false=inputs.backend_supports_git_write OR rule-caught by
# backend-incompatible-skill when run on codex.
# ---------------------------------------------------------------------------


class TestGitMetadataWriteCoverageRuleCatch:
    """Synthetic test for the rule-catch branch of TestBundledRecipeStepGuards.

    Steps guarded only by inputs.open_pr (not backend_supports_git_write) must
    be caught by the backend-incompatible-skill rule when the effective
    backend is codex and the step has no provider override. This is the test
    for the four merge-conflict steps flagged in the plan's analysis.
    """

    def test_open_pr_guarded_step_is_rule_caught_on_codex(self):
        skill_info = _make_skill_info(backend_requirements=frozenset({"claude-code"}))
        # A step guarded only by inputs.open_pr (no backend_supports_git_write)
        steps = {
            "resolve_queue_merge_conflicts": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/git-only-skill foo"},
                skip_when_false="inputs.open_pr",
            )
        }
        recipe = Recipe(name="test-coverage", description="t", steps=steps)
        resolver = _mock_resolver(skill_info)

        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            effective_backend_map={"resolve_queue_merge_conflicts": "codex"},
            available_skills=frozenset({"git-only-skill"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 1, (
            f"Expected rule to catch open_pr-guarded step on codex. Got: "
            f"{[f.message for f in findings]}"
        )
        assert compat_findings[0].step_name == "resolve_queue_merge_conflicts"
