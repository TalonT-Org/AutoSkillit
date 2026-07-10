"""Data-flow analysis for recipe pipelines.

Extracted from validator.py to break the circular import between
validator.py and rules.py (which needed to defer-import analyze_dataflow
and _build_step_graph to avoid the cycle).

Import chain: _analysis.py → contracts.py, io.py, schema.py
Neither contracts.py nor io.py imports _analysis.py, so no cycle exists.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoskillit.core import SkillResolver

from autoskillit.core import (
    ActiveIngredientSpec,
    ActiveRecipeRuntimeSnapshot,
    ActiveRecipeStepSpec,
    ActiveRunSkillSpec,
    ValidationRecipeView,
)
from autoskillit.recipe._analysis_bfs import _bfs_with_facts, bfs_reachable
from autoskillit.recipe._analysis_blocks import extract_blocks
from autoskillit.recipe._analysis_detectors import (
    _detect_dead_outputs,
    _detect_implicit_handoffs,
    _detect_ref_invalidations,
    _detect_stale_captured_paths,
)
from autoskillit.recipe._analysis_graph import (
    RouteEdge,
    _build_step_graph,
    _extract_routing_edges,
    _is_infrastructure_step,
    build_recipe_graph,
)
from autoskillit.recipe._contracts_manifest import load_bundled_manifest
from autoskillit.recipe._delivery import (
    DeliveryEvidenceMap,
    analyze_recipe_delivery,
)
from autoskillit.recipe.io import iter_steps_with_context  # noqa: F401 — re-exported for rules
from autoskillit.recipe.schema import (
    DataFlowReport,
    DataFlowWarning,
    Recipe,
    RecipeBlock,
    RecipeStep,
)

__all__ = [
    "build_recipe_graph",
    "RouteEdge",
    "_extract_routing_edges",
    "_build_step_graph",
    "_is_infrastructure_step",
    "bfs_reachable",
    "_bfs_with_facts",
    "extract_blocks",
    "_detect_dead_outputs",
    "_detect_ref_invalidations",
    "_detect_implicit_handoffs",
    "ValidationContext",
    "ValidationSnapshot",
    "build_active_recipe_runtime_snapshot",
    "analyze_dataflow",
    "make_validation_context",
    "iter_steps_with_context",
]


def _normalize_manifest_for_fingerprint(
    manifest: dict[str, Any] | MappingProxyType[str, Any],
) -> MappingProxyType[str, Any]:
    """Return a deeply immutable view of the manifest for fingerprint derivation.

    ``MappingProxyType`` wraps the top-level dict so callers cannot mutate
    the cache; nested dicts remain immutable-by-convention since the
    bundled manifest is read-only at runtime.
    """
    return MappingProxyType(dict(manifest))


@dataclass(frozen=True, slots=True)
class ValidationSnapshot:
    """One immutable validation pass: declared + effective views, evidence, fingerprints.

    A snapshot owns both the pre-prune (declared) and post-prune (effective)
    recipe views, each paired with matching evidence and graph/dataflow/blocks
    data. The fingerprint pair (``manifest_fingerprint``,
    ``recipe_invocation_fingerprint``) is computed once when the snapshot is
    built; both fields together forbid cross-recipe evidence substitution —
    evidence from recipe A cannot be paired with recipe B even when both use
    the same manifest. The fingerprints are distinct from the source
    ``content_hash`` / ``composite_hash`` provenance identities (those remain
    authoritative for rerun detection on the raw YAML).
    """

    declared_recipe: MappingProxyType[str, Any]
    effective_recipe: MappingProxyType[str, Any]
    declared_evidence: DeliveryEvidenceMap
    effective_evidence: DeliveryEvidenceMap
    declared_graph: MappingProxyType[str, Any]
    effective_graph: MappingProxyType[str, Any]
    declared_dataflow: DataFlowReport
    effective_dataflow: DataFlowReport
    declared_blocks: tuple[RecipeBlock, ...]
    effective_blocks: tuple[RecipeBlock, ...]
    normalized_manifest: MappingProxyType[str, Any]
    manifest_fingerprint: str
    recipe_invocation_fingerprint: str

    @property
    def owned_recipe(self) -> MappingProxyType[str, Any]:
        """Backward-compatible accessor — returns the effective (post-prune) view.

        Existing code that consumed the single-view snapshot continues to read
        the effective view. New code should select explicitly via ``view()``.
        """
        return self.effective_recipe

    @property
    def delivery_evidence(self) -> DeliveryEvidenceMap:
        """Backward-compatible accessor — returns the effective (post-prune) evidence."""
        return self.effective_evidence

    def view(self, recipe_view: ValidationRecipeView) -> _ValidationView:
        """Return the paired recipe + evidence + graph + dataflow + blocks for ``recipe_view``.

        Selects either the pre-prune (``DECLARED``) or post-prune
        (``EFFECTIVE``) view via the discriminator enum so consumers cannot
        mix declared recipes with effective evidence (or vice versa).
        """
        if recipe_view == ValidationRecipeView.DECLARED:
            return _ValidationView(
                recipe=self.declared_recipe,
                evidence=self.declared_evidence,
                graph=self.declared_graph,
                dataflow=self.declared_dataflow,
                blocks=self.declared_blocks,
            )
        return _ValidationView(
            recipe=self.effective_recipe,
            evidence=self.effective_evidence,
            graph=self.effective_graph,
            dataflow=self.effective_dataflow,
            blocks=self.effective_blocks,
        )

    def to_step_evidence(self, step_name: str) -> Any:
        """Backward-compatible: return effective-view evidence for ``step_name`` or None."""
        return self.effective_evidence.for_step(step_name)


@dataclass(frozen=True, slots=True)
class _ValidationView:
    """One declared-or-effective view: paired recipe, evidence, and analysis."""

    recipe: MappingProxyType[str, Any]
    evidence: DeliveryEvidenceMap
    graph: MappingProxyType[str, Any]
    dataflow: DataFlowReport
    blocks: tuple[RecipeBlock, ...]


def _compute_manifest_fingerprint(manifest: MappingProxyType[str, Any]) -> str:
    """Derive a stable manifest fingerprint from the loaded manifest content.

    Implementation is intentionally simple — a content-derived id for the
    bundled contract set. The source ``content_hash`` is reserved for
    raw-YAML provenance and is NOT reused here.
    """
    import hashlib
    import json

    payload = json.dumps(dict(manifest), sort_keys=True, default=str)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _compute_recipe_invocation_fingerprint(
    owned_view: MappingProxyType[str, Any],
) -> str:
    """Derive a recipe-invocation fingerprint from the composed/post-prune view.

    The fingerprint covers step keys, tools, command/control fields, routes,
    captures, and resolution decisions — not the recipe name, source
    ``content_hash``, or ``composite_hash``. Two recipes with identical
    invocation structure produce identical fingerprints; a renamed recipe
    with identical structure does too.
    """
    import hashlib
    import json

    recipe_dict = dict(owned_view)
    raw_steps = recipe_dict.get("steps", {}) or {}
    invocation_fields = (
        "tool",
        "action",
        "python",
        "constant",
        "with_args",
        "on_success",
        "on_failure",
        "on_context_limit",
        "on_rate_limit",
        "on_result",
        "retries",
        "on_exhausted",
        "capture",
        "capture_list",
        "optional",
        "skip_when_false",
        "skip_when_true",
        "model",
        "provider",
        "sub_recipe",
        "gate",
        "optional_context_refs",
        "stale_threshold",
        "idle_output_timeout",
        "block",
        "pass_through",
        "dispatch_items",
    )
    digest_tools: list[dict[str, Any]] = []
    for k, v in dict(raw_steps).items():
        step_dict: dict[str, Any]
        if is_dataclass(v) and not isinstance(v, type):
            step_dict = asdict(v)
        elif hasattr(v, "items"):
            step_dict = dict(v)
        elif hasattr(v, "__dict__"):
            step_dict = dict(vars(v))
        else:
            step_dict = {}
        digest_tools.append(
            {"name": str(k), **{field: step_dict.get(field) for field in invocation_fields}}
        )
    digest_fields: dict[str, Any] = {
        "step_keys": sorted(digest_tools, key=lambda d: d["name"]),
    }
    payload = json.dumps(digest_fields, sort_keys=True, default=str)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def build_validation_snapshot(
    recipe: Recipe,
    *,
    manifest: dict[str, Any] | None = None,
    effective_recipe: Recipe | None = None,
    prebuilt_step_graph: dict[str, set[str]] | None = None,
    prebuilt_dataflow: DataFlowReport | None = None,
) -> ValidationSnapshot:
    """Build the canonical immutable validation snapshot for one pass.

    The recipe view is a deep copy owned by the snapshot, so callers can
    continue mutating the original recipe without affecting the snapshot
    or the rules that consume it. The manifest is the bundled
    ``skill_contracts.yaml`` loaded once per snapshot — semantic rules must
    consume ``snapshot.normalized_manifest`` rather than re-loading it.

    When ``effective_recipe`` is supplied (the post-prune recipe from
    ``_prune_skipped_steps``), the snapshot owns both the declared and
    effective views paired with matching evidence, graph, dataflow, and
    blocks. When omitted, both views collapse to the declared recipe —
    useful for tests that never exercise pruning.

    When ``prebuilt_step_graph`` and ``prebuilt_dataflow`` are supplied
    (typically from :func:`make_validation_context`'s top-level pass),
    the snapshot reuses them for the declared view to avoid repeating
    the step-graph BFS and dataflow analysis during the same validation
    transaction.
    """
    declared_owned = copy.deepcopy(recipe)
    declared_view = MappingProxyType(vars(declared_owned))
    effective_owned = (
        copy.deepcopy(effective_recipe) if effective_recipe is not None else declared_owned
    )
    effective_view = MappingProxyType(vars(effective_owned))
    loaded_manifest = manifest if manifest is not None else load_bundled_manifest()
    normalized = _normalize_manifest_for_fingerprint(loaded_manifest)
    declared_evidence = analyze_recipe_delivery(declared_owned)
    effective_evidence = analyze_recipe_delivery(effective_owned)
    if prebuilt_step_graph is not None:
        declared_step_graph = prebuilt_step_graph
    else:
        declared_step_graph = _build_step_graph(declared_owned)
    if declared_owned is effective_owned:
        effective_step_graph = declared_step_graph
    else:
        effective_step_graph = _build_step_graph(effective_owned)
    declared_graph = MappingProxyType({k: set(v) for k, v in declared_step_graph.items()})
    effective_graph = MappingProxyType({k: set(v) for k, v in effective_step_graph.items()})
    if prebuilt_dataflow is not None:
        declared_dataflow = prebuilt_dataflow
    else:
        declared_dataflow = analyze_dataflow(declared_owned, step_graph=declared_step_graph)
    if declared_owned is effective_owned:
        effective_dataflow = declared_dataflow
    else:
        effective_dataflow = analyze_dataflow(effective_owned, step_graph=effective_step_graph)
    declared_predecessors: dict[str, set[str]] = {}
    for src, successors in declared_step_graph.items():
        for dst in successors:
            declared_predecessors.setdefault(dst, set()).add(src)
    effective_predecessors: dict[str, set[str]] = {}
    for src, successors in effective_step_graph.items():
        for dst in successors:
            effective_predecessors.setdefault(dst, set()).add(src)
    declared_blocks = extract_blocks(
        declared_owned, declared_step_graph, predecessors=declared_predecessors
    )
    effective_blocks = extract_blocks(
        effective_owned, effective_step_graph, predecessors=effective_predecessors
    )
    manifest_fp = _compute_manifest_fingerprint(normalized)
    invocation_fp = _compute_recipe_invocation_fingerprint(effective_view)
    return ValidationSnapshot(
        declared_recipe=declared_view,
        effective_recipe=effective_view,
        declared_evidence=declared_evidence,
        effective_evidence=effective_evidence,
        declared_graph=declared_graph,
        effective_graph=effective_graph,
        declared_dataflow=declared_dataflow,
        effective_dataflow=effective_dataflow,
        declared_blocks=declared_blocks,
        effective_blocks=effective_blocks,
        normalized_manifest=normalized,
        manifest_fingerprint=manifest_fp,
        recipe_invocation_fingerprint=invocation_fp,
    )


def _runtime_step_routes(step: RecipeStep) -> tuple[tuple[str, str], ...]:
    routes: list[tuple[str, str]] = []
    for edge_kind in (
        "on_success",
        "on_failure",
        "on_context_limit",
        "on_rate_limit",
        "on_exhausted",
    ):
        target = getattr(step, edge_kind, None)
        if target:
            routes.append((edge_kind, target))
    if step.on_result is not None:
        for value, target in step.on_result.routes.items():
            routes.append((f"on_result:{value}", target))
        for condition in step.on_result.conditions:
            routes.append(("on_result", condition.route))
    return tuple(routes)


def _runtime_ingredient_authority(
    *, hidden: bool, authority: str | None, default: str | None
) -> str:
    if hidden or authority == "config":
        return "hidden"
    if default is None:
        return "user"
    return "default"


def build_active_recipe_runtime_snapshot(
    recipe: Recipe,
    *,
    post_prune_step_names: Iterable[str],
    required_packs: Iterable[str] | None = None,
    required_features: Iterable[str] | None = None,
    content_hash: str | None = None,
    composite_hash: str | None = None,
    recipe_version: str | None = None,
    project_identity: str = "",
) -> ActiveRecipeRuntimeSnapshot:
    """Seal one validated post-prune recipe view for runtime consumers."""
    live_names = tuple(post_prune_step_names)
    live_name_set = frozenset(live_names)
    effective_recipe = copy.deepcopy(recipe)
    effective_recipe.steps = {
        name: step for name, step in effective_recipe.steps.items() if name in live_name_set
    }
    validation = build_validation_snapshot(recipe, effective_recipe=effective_recipe)

    step_specs: list[ActiveRecipeStepSpec] = []
    run_specs: list[ActiveRunSkillSpec] = []
    for step_key, step in effective_recipe.steps.items():
        step_specs.append(
            ActiveRecipeStepSpec(
                step_key=step_key,
                tool=step.tool or step.action or "",
                skip_when_false=step.skip_when_false or "",
                routes=_runtime_step_routes(step),
            )
        )
        if step.tool != "run_skill":
            continue
        evidence = validation.effective_evidence.for_step(step_key)
        with_args = step.with_args or {}
        run_specs.append(
            ActiveRunSkillSpec(
                step_key=step_key,
                expected_skill_command_template=str(with_args.get("skill_command", "")),
                expected_cwd_template=str(with_args.get("cwd", "")),
                declared_model=step.model,
                declared_step_provider=step.provider,
                declared_output_dir=(
                    str(with_args["output_dir"]) if "output_dir" in with_args else None
                ),
                declared_stale_threshold=step.stale_threshold,
                declared_idle_output_timeout=step.idle_output_timeout,
                optional_context_refs=tuple(step.optional_context_refs),
                expected_bindings=evidence.input_bindings if evidence is not None else (),
            )
        )

    ingredients = tuple(
        ActiveIngredientSpec(
            name=name,
            default=ingredient.default,
            required=ingredient.required,
            authority=_runtime_ingredient_authority(
                hidden=ingredient.hidden,
                authority=ingredient.authority,
                default=ingredient.default,
            ),
        )
        for name, ingredient in recipe.ingredients.items()
    )
    return ActiveRecipeRuntimeSnapshot(
        recipe_kind=recipe.name,
        normalized_ingredients=ingredients,
        required_packs=tuple(required_packs or recipe.requires_packs),
        required_features=tuple(required_features or recipe.requires_features),
        post_prune_steps=tuple(step_specs),
        run_skill_specs=tuple(run_specs),
        recipe_version=recipe_version or recipe.recipe_version or recipe.version or "",
        recipe_invocation_fingerprint=validation.recipe_invocation_fingerprint,
        manifest_fingerprint=validation.manifest_fingerprint,
        content_hash=content_hash if content_hash is not None else recipe.content_hash,
        composite_hash=composite_hash if composite_hash is not None else recipe.composite_hash,
        project_identity=project_identity,
    )


@dataclass
class ValidationContext:
    """Shared computation for a single validation pass.

    Built once per ``run_semantic_rules`` invocation so that rules consuming
    the step graph or dataflow report do not repeat those expensive builds.

    ``contract_snapshot`` is the canonical, immutable validation snapshot
    (recipe view, normalized manifest, delivery evidence, fingerprint pair).
    Rules that need contract content or delivery evidence must consume
    ``ctx.contract_snapshot`` rather than calling ``load_bundled_manifest``
    inside the pass.
    """

    recipe: Recipe
    step_graph: dict[str, set[str]]
    dataflow: DataFlowReport
    contract_snapshot: ValidationSnapshot | None = None
    delivery_evidence: DeliveryEvidenceMap | None = None
    manifest_fingerprint: str = ""
    recipe_invocation_fingerprint: str = ""
    available_recipes: frozenset[str] = field(default_factory=frozenset)
    available_skills: frozenset[str] = field(default_factory=frozenset)
    available_sub_recipes: frozenset[str] = field(default_factory=frozenset)
    project_dir: Path | None = None
    disabled_subsets: frozenset[str] = field(default_factory=frozenset)
    disabled_features: frozenset[str] = field(default_factory=frozenset)
    provider_profiles: frozenset[str] = field(default_factory=frozenset)
    skill_category_map: dict[str, frozenset[str]] | None = None
    overridden_skills: frozenset[str] | None = None
    backend_name: str | None = None
    skill_resolver: SkillResolver | None = None
    effective_backend_map: dict[str, str] | None = None
    blocks: tuple[RecipeBlock, ...] = field(default_factory=tuple)
    predecessors: dict[str, set[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def analyze_dataflow(
    recipe: Recipe,
    *,
    step_graph: dict[str, set[str]] | None = None,
) -> DataFlowReport:
    """Analyze pipeline data flow quality (non-blocking warnings).

    Args:
        recipe: The recipe to analyze.
        step_graph: Optional pre-built routing graph. When provided, the
            expensive ``_build_step_graph`` call is skipped.
    """
    graph = step_graph if step_graph is not None else _build_step_graph(recipe)

    warnings: list[DataFlowWarning] = []
    warnings.extend(_detect_dead_outputs(recipe, graph))
    warnings.extend(_detect_implicit_handoffs(recipe))
    warnings.extend(_detect_ref_invalidations(recipe, graph))
    warnings.extend(_detect_stale_captured_paths(recipe, graph))

    if warnings:
        summary = f"{len(warnings)} data-flow warning{'s' if len(warnings) != 1 else ''} found."
    else:
        summary = (
            "No data-flow warnings. All captures are consumed"
            " and skill outputs are explicitly wired."
        )

    return DataFlowReport(warnings=warnings, summary=summary)


def make_validation_context(
    recipe: Recipe,
    *,
    available_recipes: frozenset[str] = frozenset(),
    available_skills: frozenset[str] = frozenset(),
    available_sub_recipes: frozenset[str] = frozenset(),
    project_dir: Path | None = None,
    disabled_subsets: frozenset[str] = frozenset(),
    disabled_features: frozenset[str] = frozenset(),
    provider_profiles: frozenset[str] = frozenset(),
    backend_name: str | None = None,
    skill_resolver: SkillResolver | None = None,
    effective_backend_map: dict[str, str] | None = None,
    contract_snapshot: ValidationSnapshot | None = None,
) -> ValidationContext:
    """Build a ``ValidationContext`` from a recipe.

    Constructs the step graph and data-flow report once so that semantic
    rules can share the pre-built objects without redundant computation.

    When ``contract_snapshot`` is not provided, one is built via
    :func:`build_validation_snapshot` so the context always carries an
    immutable manifest + delivery evidence + fingerprint pair. Rules MUST
    consume the snapshot rather than re-loading the bundled manifest
    inside the pass.
    """
    step_graph = _build_step_graph(recipe)
    dataflow = analyze_dataflow(recipe, step_graph=step_graph)
    # Build predecessor map once; also passed to extract_blocks to avoid
    # recomputing the same inversion inside that function.
    predecessors: dict[str, set[str]] = {}
    for src, successors in step_graph.items():
        for dst in successors:
            predecessors.setdefault(dst, set()).add(src)
    snapshot = (
        contract_snapshot
        if contract_snapshot is not None
        else build_validation_snapshot(
            recipe,
            prebuilt_step_graph=step_graph,
            prebuilt_dataflow=dataflow,
        )
    )
    return ValidationContext(
        recipe=recipe,
        step_graph=step_graph,
        dataflow=dataflow,
        contract_snapshot=snapshot,
        delivery_evidence=snapshot.delivery_evidence,
        manifest_fingerprint=snapshot.manifest_fingerprint,
        recipe_invocation_fingerprint=snapshot.recipe_invocation_fingerprint,
        available_recipes=available_recipes,
        available_skills=available_skills,
        available_sub_recipes=available_sub_recipes,
        project_dir=project_dir,
        disabled_subsets=disabled_subsets,
        disabled_features=disabled_features,
        provider_profiles=provider_profiles,
        backend_name=backend_name,
        skill_resolver=skill_resolver,
        effective_backend_map=effective_backend_map,
        blocks=extract_blocks(recipe, step_graph, predecessors=predecessors),
        predecessors=predecessors,
    )
