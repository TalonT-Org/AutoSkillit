"""Semantic rules for merge_worktree routing completeness."""

from __future__ import annotations

from collections import deque

import regex as re

from autoskillit.core import MergeFailedStep, Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext, bfs_reachable
from autoskillit.recipe._analysis_bfs import _build_success_step_graph
from autoskillit.recipe._rule_helpers import _is_loop_guard_step
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

logger = get_logger(__name__)


def _is_commit_guard(step_name: str, ctx: ValidationContext) -> bool:
    """Return True if step_name is a commit_guard predecessor for merge_worktree.

    A commit_guard step is one whose name starts with 'commit_guard' OR whose
    tool is 'run_cmd' and whose cmd contains 'git commit'.
    """
    if step_name.startswith("commit_guard"):
        return True
    step = ctx.recipe.steps.get(step_name)
    if step and step.tool == "run_cmd":
        cmd = step.with_args.get("cmd", "")
        if "git commit" in cmd:
            return True
    return False


_RECOVERABLE_FAILED_STEPS: frozenset[str] = frozenset(
    {
        MergeFailedStep.DIRTY_TREE,
        MergeFailedStep.TEST_GATE,
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
# pre_remediation_merge and merge steps intentionally diverge for these four
# failed_step values because pre_remediation_merge runs before remediation
# starts and merge runs after. ref_coherence must still match across sites.
_CROSS_SITE_SITE_PAIR_EXEMPTIONS: dict[tuple[str, str], frozenset[str]] = {
    ("pre_remediation_merge", "merge"): frozenset(
        {
            MergeFailedStep.DIRTY_TREE,
            MergeFailedStep.TEST_GATE,
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


_SKILL_CMD_PATTERN = re.compile(r"/(?:autoskillit:)?([\w-]+)")


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


def _has_commit_guard_ancestor(
    step_name: str, ctx: ValidationContext, *, max_depth: int = 5
) -> bool:
    """BFS over predecessors to find a commit_guard within *max_depth* hops."""
    visited: set[str] = set()
    frontier = ctx.predecessors.get(step_name, set())
    for _ in range(max_depth):
        if not frontier:
            break
        for p in frontier:
            if _is_commit_guard(p, ctx):
                return True
        visited |= frontier
        next_frontier: set[str] = set()
        for p in frontier:
            next_frontier |= ctx.predecessors.get(p, set()) - visited
        frontier = next_frontier
    return False


@semantic_rule(
    name="merge-fix-cycle-without-iteration-guard",
    description=(
        "A merge_worktree step routes recoverable failures to a fix/assess step, "
        "creating a merge→fix→test→merge cycle. Without a check_loop_iteration guard, "
        "this cycle can loop unboundedly on structurally unresolvable conflicts."
    ),
    severity=Severity.ERROR,
)
def _check_merge_fix_cycle_without_guard(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        if not step.on_result or not step.on_result.conditions:
            continue

        fix_routes: set[str] = set()
        for cond in step.on_result.conditions:
            if cond.when and re.search(r"\bfailed_step\b", cond.when):
                fix_routes.add(cond.route)

        if not fix_routes:
            continue

        has_guard = False
        for fix_step_name in fix_routes:
            reachable = bfs_reachable(ctx.step_graph, fix_step_name) | {fix_step_name}
            for reached in reachable:
                if _is_loop_guard_step(reached, ctx):
                    has_guard = True
                    break
            if has_guard:
                break

        if not has_guard:
            findings.append(
                make_finding(
                    rule_name="merge-fix-cycle-without-iteration-guard",
                    step_name=step_name,
                    message=(
                        f"merge_worktree step '{step_name}' routes recoverable failures "
                        f"to {sorted(fix_routes)}, creating a merge→fix→test cycle "
                        f"with no check_loop_iteration guard. This can loop unboundedly "
                        f"on structurally unresolvable conflicts. Add a check_merge_fix_loop "
                        f"step (run_python calling check_loop_iteration) between test and "
                        f"commit_guard to cap the cycle at 3 iterations."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="gh-pr-merge-silent-success-routing",
    description=(
        "A run_cmd step that executes 'gh pr merge' must not route its on_failure "
        "to register_clone_success. A failed merge means the PR was NOT merged; routing "
        "to the success terminal silently reports the PR as done when it is not. "
        "Cleanup steps are exempt: steps with optional=True, or steps whose name starts "
        "with 'release_issue_' (all release_issue_* steps are terminal cleanup steps by "
        "convention — they never perform primary merge work)."
    ),
    severity=Severity.ERROR,
)
def _check_gh_pr_merge_silent_success_degradation(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = step.with_args.get("cmd", "")
        if not isinstance(cmd, str) or "gh pr merge" not in cmd:
            continue
        # Exempt cleanup steps: optional=True, or name starts with release_issue_
        # (release_issue_* steps are terminal cleanup steps by convention)
        if step.optional or step_name.startswith("release_issue_"):
            continue
        if step.on_failure == "register_clone_success":
            findings.append(
                make_finding(
                    rule_name="gh-pr-merge-silent-success-routing",
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' runs 'gh pr merge' but routes "
                        f"on_failure to 'register_clone_success' (a success terminal). "
                        f"A failed merge command means the PR was NOT merged. "
                        f"Route on_failure to an escalation target such as "
                        f"'release_issue_failure' or 'verify_queue_enrollment'."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="merge-without-commit-guard",
    description=(
        "A merge_worktree step has no commit_guard predecessor. Any path reaching "
        "merge with uncommitted changes will fail at the dirty-tree gate, burning "
        "an expensive recovery cycle. Add a commit_guard run_cmd step before merge."
    ),
    severity=Severity.ERROR,
)
def _check_merge_without_commit_guard(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        if not _has_commit_guard_ancestor(step_name, ctx):
            findings.append(
                make_finding(
                    rule_name="merge-without-commit-guard",
                    step_name=step_name,
                    message=(
                        f"merge_worktree step '{step_name}' has no commit_guard predecessor. "
                        f"Uncommitted changes from context-exhausted skills will trigger "
                        f"the dirty-tree gate, causing an expensive recovery cycle. "
                        f"Add a commit_guard run_cmd step immediately before this step."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# release-issue-on-unconfirmed-merge rule
# ---------------------------------------------------------------------------

_TIMEOUT_CONDITION_RE = re.compile(r"timeout", re.IGNORECASE)
_MERGE_WAIT_TOOLS = frozenset({"wait_for_merge_queue"})
_DIRECT_WAIT_NAMES = re.compile(r"wait_for_(direct|immediate)_merge")
_REGISTER_CLONE_UNCONFIRMED = "register_clone_unconfirmed"


def _collect_timeout_exit_steps(ctx: ValidationContext) -> set[str]:
    """Collect step names that are timeout exits from merge-wait steps.

    A timeout exit is any step name that appears as the route of an on_result
    condition containing 'timeout' in its when-expression, where the source
    step is a merge-wait step (wait_for_merge_queue, wait_for_direct_merge,
    wait_for_immediate_merge run_cmd steps). The on_failure of any merge-wait
    step is also treated as a timeout exit (tool error is also unconfirmed).
    """
    exits: set[str] = set()
    for step_name, step in ctx.recipe.steps.items():
        is_merge_wait = step.tool in _MERGE_WAIT_TOOLS or (
            step.tool == "run_cmd" and bool(_DIRECT_WAIT_NAMES.search(step_name))
        )
        if not is_merge_wait:
            continue
        if step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.when and _TIMEOUT_CONDITION_RE.search(cond.when):
                    exits.add(cond.route)
        if step.on_failure:
            exits.add(step.on_failure)
    return exits


@semantic_rule(
    name="release-issue-on-unconfirmed-merge",
    description=(
        "A release_issue step must not be reachable from a merge-wait timeout exit. "
        "When wait_for_merge_queue / wait_for_direct_merge / wait_for_immediate_merge "
        "times out, the PR is still actively in the queue. Calling release_issue removes "
        "the in-progress label, leaving the issue visually unclaimed while the merge is "
        "still pending. Route timeout exits to register_clone_unconfirmed instead."
    ),
    severity=Severity.ERROR,
)
def _check_release_issue_on_unconfirmed_merge(ctx: ValidationContext) -> list[RuleFinding]:
    timeout_exits = _collect_timeout_exit_steps(ctx)
    if not timeout_exits:
        return []

    # BFS from all timeout exits using the forward step_graph
    reachable: set[str] = set()
    frontier = set(timeout_exits) & set(ctx.step_graph)
    while frontier:
        reachable |= frontier
        next_frontier: set[str] = set()
        for name in frontier:
            next_frontier |= ctx.step_graph.get(name, set()) - reachable
        frontier = next_frontier

    findings: list[RuleFinding] = []
    for step_name in reachable:
        step = ctx.recipe.steps.get(step_name)
        if step and step.tool == "release_issue":
            findings.append(
                make_finding(
                    rule_name="release-issue-on-unconfirmed-merge",
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls release_issue but is reachable from a "
                        f"merge-wait timeout exit ({sorted(timeout_exits)}). "
                        f"Calling release_issue on a timeout path removes the in-progress label "
                        f"while the PR may still be queued. Replace with "
                        f"{_REGISTER_CLONE_UNCONFIRMED} (status: unconfirmed) so the label"
                        f" is kept."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# merge-enrollment-auto-consistency rule
# ---------------------------------------------------------------------------

_AUTO_MERGE_FALSE_PATTERN = re.compile(
    r"auto_merge_available\s*==\s*['\"]?false['\"]?", re.IGNORECASE
)


def _is_auto_flagged_step(step_name: str, ctx: ValidationContext) -> bool:
    """Return True if step uses --auto in a gh pr merge command or calls toggle_auto_merge."""
    step = ctx.recipe.steps.get(step_name)
    if step is None:
        return False
    if step.tool == "run_cmd":
        cmd = step.with_args.get("cmd", "")
        if isinstance(cmd, str) and "gh pr merge" in cmd and "--auto" in cmd:
            return True
    if step.tool == "toggle_auto_merge":
        return True
    return False


@semantic_rule(
    name="merge-enrollment-auto-consistency",
    description=(
        "gh pr merge steps with --auto must not be reachable from auto_merge_available=false "
        "routing arms. When auto_merge_available is false, --auto and toggle_auto_merge will "
        "fail because the repository does not support enablePullRequestAutoMerge."
    ),
    severity=Severity.ERROR,
)
def _check_merge_enrollment_auto_consistency(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    no_auto_targets: set[str] = set()
    for step_name, step in ctx.recipe.steps.items():
        if step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.when and _AUTO_MERGE_FALSE_PATTERN.search(cond.when):
                    no_auto_targets.add(cond.route)

    for target in no_auto_targets:
        reachable = bfs_reachable(ctx.step_graph, target) | {target}
        for reached in reachable:
            if _is_auto_flagged_step(reached, ctx):
                findings.append(
                    make_finding(
                        rule_name="merge-enrollment-auto-consistency",
                        step_name=reached,
                        message=(
                            f"Step '{reached}' uses --auto or toggle_auto_merge but is "
                            f"reachable from an auto_merge_available=false routing arm "
                            f"(via '{target}'). Use enqueue_pr instead."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="merge-site-push-symmetry",
    description=(
        "A merge_worktree step's success fallthrough does not reach push_to_remote "
        "before the next merge_worktree or recipe-terminal step. Without the push, "
        "the local branch advances un-published, guaranteeing ref_coherence "
        "divergence at the next merge site (issue #4274 root cause)."
    ),
    severity=Severity.WARNING,
)
def _check_merge_site_push_symmetry(ctx: ValidationContext) -> list[RuleFinding]:
    """Verify each merge_worktree's success path reaches push_to_remote first.

    Issue #4274 root cause: pre_remediation_merge success routed directly to
    ``remediate`` without a push_to_remote step in between. Every successful
    merge must push the local branch before the next merge site or the recipe
    terminal — otherwise ref_coherence divergence is structurally guaranteed.

    Algorithm:
    1. Find every ``merge_worktree`` step.
    2. Identify its success-fallthrough route (the unconditional ``on_result``
       condition, falling back to ``on_success``).
    3. BFS forward on the success-path graph from that target.
    4. Fire if a ``merge_worktree`` step is reached before any
       ``push_to_remote`` step (the push must come first on the success path).
    """
    findings: list[RuleFinding] = []
    success_graph = _build_success_step_graph(ctx.recipe)

    def _success_fallthrough_target(step: object) -> str | None:
        on_result = getattr(step, "on_result", None)
        if on_result is not None and on_result.conditions:
            for cond in on_result.conditions:
                if cond.when is None:
                    return cond.route
        on_success = getattr(step, "on_success", None)
        return on_success

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        target = _success_fallthrough_target(step)
        if target is None:
            continue

        visited: set[str] = set()
        queue: list[str] = [target]
        push_found = False
        earlier_merge = None
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            current_step = ctx.recipe.steps.get(current)
            if current_step is None:
                continue
            if getattr(current_step, "tool", None) == "push_to_remote":
                push_found = True
                break
            if getattr(current_step, "tool", None) == "merge_worktree":
                earlier_merge = current
                break
            queue.extend(success_graph.get(current, set()))

        if earlier_merge is not None:
            findings.append(
                make_finding(
                    rule_name="merge-site-push-symmetry",
                    step_name=step_name,
                    message=(
                        f"merge_worktree step '{step_name}' success fallthrough "
                        f"reaches '{earlier_merge}' before any push_to_remote — "
                        f"insert an inter-part push step before '{earlier_merge}' "
                        f"to close the ref_coherence divergence window."
                    ),
                )
            )
        elif not push_found and target not in visited:
            findings.append(
                make_finding(
                    rule_name="merge-site-push-symmetry",
                    step_name=step_name,
                    message=(
                        f"merge_worktree step '{step_name}' success fallthrough "
                        f"never reaches push_to_remote — insert a push_to_remote "
                        f"step before the next merge_worktree or recipe terminal."
                    ),
                )
            )

    return findings
