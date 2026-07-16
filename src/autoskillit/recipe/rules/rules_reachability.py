"""Symbolic reachability semantic rules for recipe pipelines.

Provides two rules:

``capture-inversion-detection``
    Flags steps that read ``context.X`` via ``${{ context.X }}`` when X is
    established by a conditional edge (``on_result.when``) that does not cover
    every path reaching that step.  Uses ``_bfs_with_facts`` to propagate
    conditional edge facts and intersect them at join points.

``event-scope-requires-upstream-capture``
    Flags ``wait_for_ci`` steps that hardcode a literal ``event`` value
    (e.g. ``event: "push"``) without a step that captures ``merge_group_trigger``
    upstream of them on every path.  When the repo only triggers CI on
    ``merge_group``, a hardcoded ``push`` event produces a ``no_runs`` timeout.

Both rules fire at severity ERROR and are registered via ``@semantic_rule``.
"""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import (
    ValidationContext,
    _bfs_with_facts,
)
from autoskillit.recipe._analysis_bfs import all_paths_cross, bfs_reachable_without_barrier
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

# Regex to find ${{ context.X }} references anywhere in a string value.
_CTX_REF_RE = re.compile(r"\$\{\{\s*context\.(\w+)\s*\}\}")


def _find_context_refs_in_step(step_name: str, ctx: ValidationContext) -> list[str]:
    """Return context variable names referenced in step's with_args or skill_command.

    Scans ``step.with_args`` values and the ``skill_command`` arg for
    ``${{ context.X }}`` patterns.
    """
    step = ctx.recipe.steps.get(step_name)
    if step is None:
        return []
    refs: list[str] = []
    for key, val in (step.with_args or {}).items():
        if key == "skill_command":
            continue  # handled below to avoid double-scan via values()
        if isinstance(val, str):
            refs.extend(_CTX_REF_RE.findall(val))
    skill_cmd = (step.with_args or {}).get("skill_command", "")
    if skill_cmd:
        refs.extend(_CTX_REF_RE.findall(skill_cmd))
    return refs


def _find_capture_producers(ctx: ValidationContext, var: str) -> list[str]:
    """Return all step names whose ``capture`` dict contains ``var``."""
    return [step.name for step in ctx.recipe.steps.values() if var in (step.capture or {})]


def _find_recipe_entry(ctx: ValidationContext) -> str | None:
    """Return the recipe entry step name: the first step with no in-edges in ``ctx.step_graph``.

    Falls back to the first step in ``ctx.recipe.steps`` iteration order when
    every step has an incoming edge (e.g. recipes that compose via
    ``sub_recipe`` and have no top-level entry). Returns ``None`` if the recipe
    has no steps at all.
    """
    all_targets = {t for targets in ctx.step_graph.values() for t in targets}
    entry = next(
        (name for name in ctx.recipe.steps if name not in all_targets),
        next(iter(ctx.recipe.steps), None),
    )
    return entry


@semantic_rule(
    name="capture-inversion-detection",
    description=(
        "Flags steps that read context.X via conditional-edge facts when X's "
        "conditional producer is downstream on some path."
    ),
    severity=Severity.ERROR,
)
def _check_capture_inversion(ctx: ValidationContext) -> list[RuleFinding]:
    """Flag context variable reads where the var is conditionally established but
    not known on every path to the reading step.

    This fires when:
    - Step S reads ``${{ context.X }}``
    - X is established only by a conditional ``on_result.when`` edge (not by
      an unconditional capture)
    - X is not present in the intersected fact set at S (i.e. not known on
      every path from the recipe entry to S)

    Note: variables captured unconditionally by tool steps are NOT flagged here
    because they do not appear in the ``_bfs_with_facts`` conditional fact domain.
    """
    entry = _find_recipe_entry(ctx)
    if entry is None:
        return []

    facts = _bfs_with_facts(ctx.step_graph, ctx.recipe, start=entry)

    # Pre-compute the full conditional fact domain once — not inside the inner loop.
    all_facts_in_recipe: set[tuple[str, str]] = set()
    for node_facts in facts.values():
        for fs in node_facts:
            all_facts_in_recipe.update(fs)
    recipe_fact_vars = {v for v, _ in all_facts_in_recipe}

    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        context_refs = _find_context_refs_in_step(step_name, ctx)
        if not context_refs:
            continue
        step_facts_set = facts.get(step_name, {frozenset()})
        # Intersected fact set at this step (single element after _bfs_with_facts return)
        intersected = next(iter(step_facts_set), frozenset())
        known_vars = {var for var, _ in intersected}

        for var in context_refs:
            # Only flag vars that appear in the conditional fact domain of the recipe.
            # If no step establishes (var, value) via a conditional edge, the var is
            # captured unconditionally — not an inversion.

            if var not in recipe_fact_vars:
                continue  # var is not conditionally established anywhere — skip

            if var in known_vars:
                continue  # fact is known on every path to this step — OK

            producers = _find_capture_producers(ctx, var)
            if not producers:
                continue  # no producer anywhere — not an inversion, different bug

            # Strict forward-path dominance: exonerate if any producer strictly
            # dominates step_name on success paths — every success-path from entry
            # to step_name crosses the producer, so var is always captured before
            # step_name runs. Using backward reachability (_ancestors) here would
            # accept "on some path" producers that don't dominate fork-join readers,
            # producing false negatives in real inversions. bfs_reachable_without_barrier
            # builds its own success-path graph from ctx.recipe (post-prune) and
            # returns all steps reachable from entry without crossing the barrier.
            # If step_name is NOT in this set, the producer strictly dominates it.
            exonerated = False
            for _producer in producers:
                _reachable_without = bfs_reachable_without_barrier(ctx.recipe, entry, _producer)
                if step_name not in _reachable_without:
                    exonerated = True
                    break
            if exonerated:
                continue

            findings.append(
                make_finding(
                    rule_name="capture-inversion-detection",
                    step_name=step_name,
                    message=(
                        f"Step {step_name!r} reads context.{var} but the producer "
                        f"{producers[0]!r} does not establish {var!r} on every path. "
                        f"Move producer upstream or gate the reader on producer's capture."
                    ),
                )
            )

    return findings


@semantic_rule(
    name="event-scope-requires-upstream-capture",
    description=(
        "wait_for_ci steps must not hardcode a literal event value without "
        "an upstream step that captures merge_group_trigger on every path."
    ),
    severity=Severity.ERROR,
)
def _check_event_scope_requires_upstream_capture(ctx: ValidationContext) -> list[RuleFinding]:
    """Flag wait_for_ci steps that hardcode a literal event without upstream capture.

    A hardcoded ``event: "push"`` on a ``wait_for_ci`` step produces a
    ``no_runs`` timeout on repos that only trigger CI on ``merge_group``.
    The correct pattern is to capture ``merge_group_trigger`` (or ``ci_event``)
    from ``check_repo_merge_state`` upstream and bind it dynamically.

    Skips steps where ``event`` is absent (matches any trigger) or is a
    template reference starting with ``${{`` (already dynamic).
    """
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue

        event = (step.with_args or {}).get("event")
        if event is None:
            continue  # absent is fine — matches any event
        if isinstance(event, str) and event.startswith("${{"):
            continue  # dynamic reference — correct pattern

        # Literal event value: dominator check — every path from the recipe
        # entry to this step must cross at least one merge_group_trigger
        # producer. The prior existential ``any(p in ancestor_set)`` check
        # accepted producers reachable via a single branch of a fork, producing
        # false negatives for fork-join topologies where the other branch
        # bypasses the producer.
        mg_producers = _find_capture_producers(ctx, "merge_group_trigger")
        entry = _find_recipe_entry(ctx)
        if entry is None:
            entry = step_name  # no recipe entry — fall back to step-local view

        if any(all_paths_cross(ctx.step_graph, entry, p, step_name) for p in mg_producers):
            continue  # at least one producer dominates — context is known

        producer_desc = (
            f"producer steps {mg_producers!r}"
            if mg_producers
            else "no producer for merge_group_trigger"
        )
        findings.append(
            make_finding(
                rule_name="event-scope-requires-upstream-capture",
                step_name=step_name,
                message=(
                    f"wait_for_ci step {step_name!r} hardcodes event={event!r} without "
                    f"an upstream merge_group_trigger capture ({producer_desc} "
                    f"is not an ancestor). On a repo that only triggers on merge_group, "
                    f'this produces a no_runs timeout. Use event: "${{{{ context.ci_event }}}}" '
                    f"and capture ci_event from check_repo_merge_state upstream."
                ),
            )
        )

    return findings
