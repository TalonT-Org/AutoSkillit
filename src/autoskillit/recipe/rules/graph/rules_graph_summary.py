"""Semantic rule validating a recipe's high-level summary against its step graph."""

from __future__ import annotations

from itertools import combinations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import _build_success_step_graph, bfs_reachable
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

# Matches an explicit optional-phase marker: "(name?)" — hyphen/underscore agnostic,
# parsed independently of the '>' separator convention used by every bundled recipe.
_OPTIONAL_TOKEN_RE = re.compile(r"\(([A-Za-z0-9_-]+)\?\)")


def _normalize_label(label: str) -> str:
    return label.strip().lower().replace("_", "-")


def _phase_tokens(summary: str) -> list[tuple[str, bool]]:
    """Extract (normalized_label, is_optional) tokens from a summary, in order.

    Splits on '>' and independently parses each chunk for an explicit
    '(name?)' marker or a bare leading label — trailing parenthetical
    annotations without '?' (e.g. "merge (per plan part)") are ignored, not
    mistaken for an optional marker.
    """
    tokens: list[tuple[str, bool]] = []
    for chunk in summary.split(">"):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _OPTIONAL_TOKEN_RE.match(chunk)
        if m:
            tokens.append((_normalize_label(m.group(1)), True))
            continue
        words = chunk.split()
        if not words:
            continue
        head = words[0].strip("()")
        if head:
            tokens.append((_normalize_label(head), False))
    return tokens


def _ingredient_name_from_ref(ref: str) -> str | None:
    if ref.startswith("inputs."):
        return ref[len("inputs.") :]
    return None


def _gate_ingredient(recipe: Recipe, ref: str) -> RecipeIngredient | None:
    name = _ingredient_name_from_ref(ref)
    if name is None:
        return None
    return recipe.ingredients.get(name)


def _is_user_configurable_gate(recipe: Recipe, ref: str) -> bool:
    """Whether `ref` (a skip_when_false/true value) gates on a user-facing ingredient.

    Excludes hidden and authority:config ingredients (auto-resolved capability
    flags such as backend_supports_git_write) — those are mechanically gated
    but not something a user configures, so their steps are not phase
    waypoints the summary needs to disclose.
    """
    ing = _gate_ingredient(recipe, ref)
    if ing is None:
        return False
    return not ing.hidden and ing.authority != "config"


def _is_simple_boolean_gate(recipe: Recipe, ref: str) -> bool:
    """Whether `ref` gates on a plain on/off ingredient (vs. a multi-value sentinel).

    Excludes ingredients like "investigate" whose default is 'auto' (resolved
    externally in fleet/batch dispatch) from the strict '?' agreement check —
    those are gated mechanically but are not a simple user-facing toggle.
    """
    ing = _gate_ingredient(recipe, ref)
    if ing is None:
        return False
    return ing.default in (None, "", "true", "false")


def _candidate_labels(step_name: str, step: RecipeStep) -> set[str]:
    """Normalized labels a summary token could plausibly use for this step.

    Includes the step's own key, its resolved skill name, and the name of
    the ingredient gating it — recipes sometimes label a cluster of steps
    sharing one gate with the gate's own name (e.g. remediation.yaml's
    "(open_pr?)" covers prepare_pr/run_arch_lenses/compose_pr/review_pr).
    """
    labels = {_normalize_label(step_name)}
    if step.skill_name:
        labels.add(_normalize_label(step.skill_name))
    gate_ref = step.skip_when_false or step.skip_when_true
    if gate_ref is not None:
        gate_name = _ingredient_name_from_ref(gate_ref)
        if gate_name:
            labels.add(_normalize_label(gate_name))
    return labels


@semantic_rule(
    name="summary-graph-divergence",
    description=(
        "The recipe's summary: line must disclose every user-configurable phase "
        "waypoint (run_skill steps gated by skip_when_false/skip_when_true on a "
        "user-facing, plain boolean-like ingredient), mark those phases with "
        "'(name?)' and non-gated phases without '?', and order matched phases "
        "consistently with the success-path step graph."
    ),
    severity=Severity.WARNING,
)
def _check_summary_graph_divergence(ctx: ValidationContext) -> list[RuleFinding]:
    recipe = ctx.recipe
    summary = recipe.summary
    if not summary:
        return []

    tokens = _phase_tokens(summary)
    optional_labels = {label for label, is_opt in tokens if is_opt}
    token_labels = {label for label, _ in tokens}
    first_index: dict[str, int] = {}
    for i, (label, _is_opt) in enumerate(tokens):
        first_index.setdefault(label, i)

    findings: list[RuleFinding] = []
    seen: set[tuple[str, str]] = set()

    def emit(step_name: str, reason: str, message: str) -> None:
        key = (step_name, reason)
        if key in seen:
            return
        seen.add(key)
        findings.append(
            make_finding(
                rule_name="summary-graph-divergence", step_name=step_name, message=message
            )
        )

    # (step_name, matched_label, summary_index) for steps matched via their OWN
    # step name or skill name (not the gate-ingredient fallback) — only these
    # have a well-defined single graph position, so only these participate in
    # the ordering check.
    matched_direct: list[tuple[str, str, int]] = []

    for step_name, step in recipe.steps.items():
        if step.tool != "run_skill":
            continue
        gate_ref = step.skip_when_false or step.skip_when_true
        if gate_ref is None:
            continue
        if not _is_user_configurable_gate(recipe, gate_ref):
            continue

        own_labels = {_normalize_label(step_name)}
        if step.skill_name:
            own_labels.add(_normalize_label(step.skill_name))
        all_labels = _candidate_labels(step_name, step)

        matching = all_labels & token_labels
        if not matching:
            emit(
                step_name,
                "missing",
                f"Step '{step_name}' is a user-configurable phase (gated by "
                f"skip_when_false/skip_when_true) but no normalized form of its "
                f"step name, skill name, or gating ingredient appears in the "
                f"recipe's summary: line. Disclose it as '(label?)'.",
            )
            continue

        matched_own = own_labels & matching
        if matched_own:
            label = next(iter(matched_own))
            matched_direct.append((step_name, label, first_index[label]))
        else:
            label = next(iter(matching))

        if _is_simple_boolean_gate(recipe, gate_ref) and label not in optional_labels:
            emit(
                step_name,
                "missing-optional-marker",
                f"Step '{step_name}' is gated (skip_when_false/skip_when_true) but its "
                f"summary token '{label}' is shown without the '?' optional marker. "
                f"Use '({label}?)'.",
            )

    # Reject '?' on a matched, ungated run_skill phase.
    for step_name, step in recipe.steps.items():
        if step.tool != "run_skill":
            continue
        gate_ref = step.skip_when_false or step.skip_when_true
        if gate_ref is not None:
            continue
        own_labels = {_normalize_label(step_name)}
        if step.skill_name:
            own_labels.add(_normalize_label(step.skill_name))
        matched = own_labels & optional_labels
        if matched:
            label = next(iter(matched))
            emit(
                step_name,
                "false-optional-marker",
                f"Step '{step_name}' is not gated (no skip_when_false/skip_when_true) "
                f"but its summary token '{label}' is marked '?' as optional.",
            )
        else:
            matched_bare = own_labels & token_labels
            if matched_bare:
                label = next(iter(matched_bare))
                matched_direct.append((step_name, label, first_index[label]))

    # Ordering check: pairwise success-graph reachability for directly matched
    # phase steps. Reverse-only reachability contradicts the summary's
    # left-to-right order; bidirectional reachability (a shared cycle) and
    # incomparable branch nodes impose no false total order.
    if len(matched_direct) > 1:
        graph = _build_success_step_graph(recipe)
        reachable_cache: dict[str, set[str]] = {}

        def reachable_from(node: str) -> set[str]:
            if node not in reachable_cache:
                reachable_cache[node] = bfs_reachable(graph, node)
            return reachable_cache[node]

        by_index = sorted(set(matched_direct), key=lambda t: t[2])
        for (name_i, label_i, idx_i), (name_j, label_j, idx_j) in combinations(by_index, 2):
            if idx_i == idx_j or name_i == name_j:
                continue
            forward = name_j in reachable_from(name_i)
            backward = name_i in reachable_from(name_j)
            if backward and not forward:
                emit(
                    name_j,
                    "order-divergence",
                    f"Summary lists '{label_i}' before '{label_j}', but the "
                    f"success-path step graph only reaches '{name_i}' from "
                    f"'{name_j}' (the reverse order). Reorder the summary or "
                    f"correct the routing.",
                )

    return findings
