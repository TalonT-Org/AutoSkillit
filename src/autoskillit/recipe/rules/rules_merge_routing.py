"""Semantic rules for merge_worktree route completeness, cross-site consistency,
failure-domain matching, and recovery-class classification (R1-R3)."""

from __future__ import annotations

from collections import deque

import regex as re

from autoskillit.core import MergeFailedStep, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import _build_success_step_graph
from autoskillit.recipe._rule_helpers import _SKILL_CMD_PATTERN
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

_RECOVERABLE_FAILED_STEPS: frozenset[str] = frozenset(
    {
        MergeFailedStep.DIRTY_TREE,
        MergeFailedStep.TEST_GATE,
        MergeFailedStep.TEST_GATE_CONTENTION,
        MergeFailedStep.POST_REBASE_TEST_GATE,
        MergeFailedStep.REBASE,
        MergeFailedStep.DIRTY_MAIN_REPO,
        MergeFailedStep.REF_COHERENCE,
    }
)

_TERMINAL_FAILED_STEPS: frozenset[str] = frozenset(
    {
        MergeFailedStep.PATH_VALIDATION,
        MergeFailedStep.PROTECTED_BRANCH,
        MergeFailedStep.BRANCH_DETECTION,
        MergeFailedStep.GENERATED_FILE_CLEANUP,
        MergeFailedStep.FETCH,
        MergeFailedStep.PRE_REBASE_CHECK,
        MergeFailedStep.MERGE_COMMITS_DETECTED,
        MergeFailedStep.MERGE,
        MergeFailedStep.EDITABLE_INSTALL_GUARD,
        MergeFailedStep.EMBEDDED_WORKTREE,
    }
)

_MERGE_FAILURE_DOMAINS: dict[str, str] = {
    MergeFailedStep.DIRTY_TREE: "code",
    MergeFailedStep.TEST_GATE: "code",
    MergeFailedStep.TEST_GATE_CONTENTION: "code",
    MergeFailedStep.POST_REBASE_TEST_GATE: "code",
    MergeFailedStep.REBASE: "git_conflict",
    MergeFailedStep.REF_COHERENCE: "push_recovery",
}

_REQUIRED_SKILL_BY_DOMAIN: dict[str, str] = {
    "code": "resolve-failures",
    "git_conflict": "resolve-merge-conflicts",
}

# Behavioral recovery classes for domains that cannot be validated by exact skill
# identity. Keys are domain names; values are the recovery class a route must
# reach via the nearest-depth success-path traversal.
_REQUIRED_RECOVERY_CLASS: dict[str, str] = {
    "push_recovery": "push_recovery",
}

# Recovery signatures used by _classify_recovery_class. Each signature maps a
# distinct identity kind to a recovery class. Lookup order is deterministic.
_RECOVERY_SIGNATURES_TOOL: dict[str, str] = {
    "push_to_remote": "push_recovery",
}
_RECOVERY_SIGNATURES_SKILL: dict[str, str] = {
    "resolve-failures": "fix_loop",
    "resolve-merge-conflicts": "rebase_loop",
    "make-plan": "direct_remediate",
}
_RECOVERY_SIGNATURES_CALLABLE: dict[str, str] = {
    "autoskillit.recipe._cmd_rpc.main_repo_guard": "dirty_retry",
}

# Canonical _FAILED_STEP_PATTERN; rules_merge_context imports this rather than
# maintaining its own copy.
_FAILED_STEP_PATTERN = re.compile(r"result\.failed_step\s*==\s*['\"](\w+)['\"]")


@semantic_rule(
    name="merge-routing-incomplete",
    description=(
        "Every merge_worktree step with predicate on_result must explicitly route "
        "all recoverable MergeFailedStep values to a recovery step. "
        "Unhandled values fall through to the result.error catch-all, which typically "
        "discards a recoverable worktree."
    ),
    severity=Severity.ERROR,
)
def _check_merge_routing_completeness(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        if not step.on_result or not step.on_result.conditions:
            continue

        matched: set[str] = set()
        for condition in step.on_result.conditions:
            if condition.when is None:
                continue
            m = _FAILED_STEP_PATTERN.search(condition.when)
            if m:
                matched.add(m.group(1))

        missing = _RECOVERABLE_FAILED_STEPS - matched
        if missing:
            findings.append(
                make_finding(
                    rule_name="merge-routing-incomplete",
                    step_name=step_name,
                    message=(
                        f"merge_worktree on_result is missing explicit routes for "
                        f"recoverable failures: {sorted(missing)}. "
                        f"These will fall through to the result.error catch-all, "
                        f"discarding a recoverable worktree."
                    ),
                )
            )
    return findings


# Predicate-qualified keys for cross-site merge routing comparison. Each failed_step
# may have one or more predicate-qualified variants; the key combines them so
# duplicate arms never overwrite each other in the per-site evidence map.
_CROSS_SITE_VARIANT_DEFAULT = "default"
_CROSS_SITE_VARIANT_ANCESTRY = "ancestry-aware"
_CROSS_SITE_VARIANT_FALLBACK = "fallback"


def _predicate_variant(condition_when: str | None, failed_step_value: str) -> str:
    """Return a predicate-qualified variant label for a failed_step arm.

    For ref_coherence, distinguish at least ancestry-aware (contains both
    ``ref_coherence`` and ``remote_is_ancestor``) and fallback (contains
    ``ref_coherence`` but not ``remote_is_ancestor``). All other failed_steps
    collapse to a single default variant.
    """
    if condition_when is None:
        return _CROSS_SITE_VARIANT_DEFAULT
    when_lower = condition_when.lower()
    if failed_step_value == MergeFailedStep.REF_COHERENCE:
        has_ref = "ref_coherence" in when_lower
        has_ancestor = "remote_is_ancestor" in when_lower
        if has_ref and has_ancestor:
            return _CROSS_SITE_VARIANT_ANCESTRY
        if has_ref:
            return _CROSS_SITE_VARIANT_FALLBACK
    return _CROSS_SITE_VARIANT_DEFAULT


# Exact site pair exemption for the bundled remediation recipe: the
# pre_remediation_merge and merge steps intentionally diverge for these five
# failed_step values because pre_remediation_merge runs before remediation
# starts and merge runs after. ref_coherence must still match across sites.
_CROSS_SITE_SITE_PAIR_EXEMPTIONS: dict[tuple[str, str], frozenset[str]] = {
    ("pre_remediation_merge", "merge"): frozenset(
        {
            MergeFailedStep.DIRTY_TREE,
            MergeFailedStep.TEST_GATE,
            MergeFailedStep.TEST_GATE_CONTENTION,
            MergeFailedStep.POST_REBASE_TEST_GATE,
            MergeFailedStep.REBASE,
        }
    ),
}


def _check_merge_routing_cross_site_consistency(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    merge_sites: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        if not step.on_result or not step.on_result.conditions:
            continue
        per_variant: dict[str, list[tuple[str, str]]] = {}
        for condition in step.on_result.conditions:
            if condition.when is None:
                continue
            m = _FAILED_STEP_PATTERN.search(condition.when)
            if not m:
                continue
            failed_step_value = m.group(1)
            if failed_step_value not in _RECOVERABLE_FAILED_STEPS:
                continue
            variant = _predicate_variant(condition.when, failed_step_value)
            key = f"{failed_step_value}::{variant}"
            per_variant.setdefault(key, []).append((condition.route, condition.when))
        if per_variant:
            merge_sites[step_name] = per_variant

    site_names = sorted(merge_sites)
    if len(site_names) < 2:
        return findings

    all_keys: set[str] = set()
    for per_variant in merge_sites.values():
        all_keys.update(per_variant.keys())

    for key in sorted(all_keys):
        sites_with_key = [s for s in site_names if key in merge_sites[s]]
        if len(sites_with_key) < 2:
            continue

        failed_step_value = key.split("::", 1)[0]
        site_set = frozenset(sites_with_key)
        exemption_match = False
        for pair, exempt_steps in _CROSS_SITE_SITE_PAIR_EXEMPTIONS.items():
            if frozenset(pair) == site_set and failed_step_value in exempt_steps:
                exemption_match = True
                break
        if exemption_match:
            continue

        classifications: dict[str, str | None] = {}
        targets: dict[str, str] = {}
        for site in sites_with_key:
            arm = merge_sites[site][key][0]
            route = arm[0]
            targets[site] = route
            classifications[site] = _classify_recovery_class(route, ctx)

        classified = {s: c for s, c in classifications.items() if c is not None}
        unclassified = [s for s in classifications if classifications[s] is None]

        mismatch = False
        if classified and unclassified:
            mismatch = True
        elif len(classified) >= 2 and len(set(classified.values())) > 1:
            mismatch = True
        elif len(unclassified) >= 2:
            unclassified_targets = {targets[s] for s in unclassified}
            if len(unclassified_targets) > 1:
                mismatch = True

        if not mismatch:
            continue

        site_list = ", ".join(sites_with_key)
        target_list = ", ".join(f"{s}->{targets[s]}({classifications[s]})" for s in sites_with_key)
        findings.append(
            make_finding(
                rule_name="merge-routing-cross-site-consistency",
                step_name=sites_with_key[0],
                message=(
                    f"merge_worktree sites [{site_list}] have inconsistent predicate-"
                    f"qualified routing for failed_step '{failed_step_value}' "
                    f"(variant '{key.split('::', 1)[1]}'): {target_list}. The "
                    f"same failed_step at different merge sites must reach the "
                    f"same recovery class."
                ),
            )
        )
    return findings


@semantic_rule(
    name="merge-routing-cross-site-consistency",
    description=(
        "Every merge_worktree step in the same recipe must route each "
        "predicate-qualified failed_step arm to the same recovery class. "
        "Recovery class is determined by the nearest-depth success-path "
        "traversal. Classified-vs-unclassified and different unclassified "
        "targets are both mismatches."
    ),
    severity=Severity.ERROR,
)
def _check_merge_routing_cross_site_consistency_decorated(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    return _check_merge_routing_cross_site_consistency(ctx)


def _classify_recovery_class(
    route: str,
    ctx: ValidationContext,
    *,
    max_depth: int = 4,
    success_graph: dict[str, set[str]] | None = None,
) -> str | None:
    """Classify a recovery route by its nearest-depth recovery signature.

    Traversal is level-ordered (BFS by depth, not by unordered reachable set) over
    the success-path graph so a later incidental loop target cannot override a
    nearer recovery signature. Inspection covers tool identity (push_to_remote),
    exact resolved skill identity, and exact callable identity. The first depth
    that contains a signature must identify exactly one unique class; if no depth
    yields a signature within ``max_depth`` or the first signature-bearing depth
    is ambiguous, return ``None``.
    """
    graph = success_graph if success_graph is not None else _build_success_step_graph(ctx.recipe)
    if route not in graph:
        return None
    visited: set[str] = {route}
    frontier: deque[str] = deque([route])
    for _depth in range(max_depth + 1):
        next_frontier: deque[str] = deque()
        depth_signatures: set[str] = set()
        for node in frontier:
            step = ctx.recipe.steps.get(node)
            if step is not None:
                if step.tool in _RECOVERY_SIGNATURES_TOOL:
                    depth_signatures.add(_RECOVERY_SIGNATURES_TOOL[step.tool])
                if step.tool == "run_skill":
                    skill_cmd = (step.with_args or {}).get("skill_command", "")
                    skill_name = resolve_skill_name(skill_cmd)
                    if skill_name and skill_name in _RECOVERY_SIGNATURES_SKILL:
                        depth_signatures.add(_RECOVERY_SIGNATURES_SKILL[skill_name])
                if step.tool == "run_python":
                    callable_name = (step.with_args or {}).get("callable", "")
                    if callable_name in _RECOVERY_SIGNATURES_CALLABLE:
                        depth_signatures.add(_RECOVERY_SIGNATURES_CALLABLE[callable_name])
            if _depth == max_depth:
                continue
            for successor in graph.get(node, ()):
                if successor in visited:
                    continue
                visited.add(successor)
                next_frontier.append(successor)
        if len(depth_signatures) == 1:
            return next(iter(depth_signatures))
        if len(depth_signatures) > 1:
            return None
        if not next_frontier:
            return None
        frontier = next_frontier
    return None


@semantic_rule(
    name="merge-failure-skill-domain-mismatch",
    description=(
        "A merge_worktree on_result condition routes a recoverable failed_step to "
        "a route whose behavior does not match the failure domain. Git-conflict "
        "failures (rebase) must route to resolve-merge-conflicts; code failures "
        "(dirty_tree, test_gate, post_rebase_test_gate) must route to "
        "resolve-failures; ref_coherence ancestry arms must classify as "
        "push_recovery."
    ),
    severity=Severity.ERROR,
)
def _check_merge_failure_skill_domain_mismatch(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    success_graph = _build_success_step_graph(ctx.recipe)
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        if not step.on_result or not step.on_result.conditions:
            continue

        for condition in step.on_result.conditions:
            if condition.when is None:
                continue
            m = _FAILED_STEP_PATTERN.search(condition.when)
            if not m:
                continue
            failed_step_value = m.group(1)
            domain = _MERGE_FAILURE_DOMAINS.get(failed_step_value)
            if domain is None:
                continue

            # For REF_COHERENCE only the ancestry-aware arm is validated; the
            # fallback arm is an intentional escalation terminal whose target
            # differs from the ancestry arm by design.
            if failed_step_value == MergeFailedStep.REF_COHERENCE:
                when_lower = condition.when.lower()
                if "remote_is_ancestor" not in when_lower:
                    continue
                required_class = _REQUIRED_RECOVERY_CLASS.get(domain)
                if required_class is None:
                    continue
                actual_class = _classify_recovery_class(
                    condition.route, ctx, success_graph=success_graph
                )
                if actual_class != required_class:
                    findings.append(
                        make_finding(
                            rule_name="merge-failure-skill-domain-mismatch",
                            step_name=step_name,
                            message=(
                                f"failed_step == '{failed_step_value}' ancestry arm "
                                f"(domain: {domain}) routes to '{condition.route}' "
                                f"which classifies as {actual_class!r}, expected "
                                f"{required_class!r}."
                            ),
                        )
                    )
                continue

            target_step = ctx.recipe.steps.get(condition.route)
            if target_step is None or target_step.tool != "run_skill":
                continue

            skill_cmd = target_step.with_args.get("skill_command", "")
            skill_match = _SKILL_CMD_PATTERN.search(skill_cmd)
            if not skill_match:
                continue
            actual_skill = skill_match.group(1)

            required_skill = _REQUIRED_SKILL_BY_DOMAIN[domain]
            if actual_skill != required_skill:
                findings.append(
                    make_finding(
                        rule_name="merge-failure-skill-domain-mismatch",
                        step_name=step_name,
                        message=(
                            f"failed_step == '{failed_step_value}' (domain: {domain}) "
                            f"routes to step '{condition.route}' which invokes "
                            f"'{actual_skill}', but {domain} failures require "
                            f"'{required_skill}'."
                        ),
                    )
                )
    return findings
