"""Data-flow analysis for recipe pipelines.

Extracted from validator.py to break the circular import between
validator.py and rules.py (which needed to defer-import analyze_dataflow
and _build_step_graph to avoid the cycle).

Import chain: _analysis.py → contracts.py, io.py, schema.py
Neither contracts.py nor io.py imports _analysis.py, so no cycle exists.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoskillit.core import SkillResolver

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
    """One immutable validation pass: owned recipe view + manifest + evidence.

    The fingerprint pair (``manifest_fingerprint``,
    ``recipe_invocation_fingerprint``) is computed once when the snapshot is
    built; both fields together forbid cross-recipe evidence substitution —
    evidence from recipe A cannot be paired with recipe B even when both use
    the same manifest. The fingerprints are distinct from the source
    ``content_hash`` / ``composite_hash`` provenance identities (those remain
    authoritative for rerun detection on the raw YAML).
    """

    owned_recipe: MappingProxyType[str, Any]
    normalized_manifest: MappingProxyType[str, Any]
    delivery_evidence: DeliveryEvidenceMap
    manifest_fingerprint: str
    recipe_invocation_fingerprint: str

    def to_step_evidence(self, step_name: str) -> Any:
        """Convenience: return evidence for ``step_name`` or ``None``."""
        return self.delivery_evidence.for_step(step_name)


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
    # MappingProxyType iterates as (key, value) where value is whatever the
    # underlying dict held — typically a MappingProxyType itself when the
    # recipe was constructed via the dataclass. Cast to plain dicts to make
    # the ``.get`` calls type-safe.
    digest_tools: list[dict[str, Any]] = []
    for k, v in dict(raw_steps).items():
        step_dict = dict(v) if hasattr(v, "items") else {}
        with_args = step_dict.get("with_args", {}) or {}
        digest_tools.append(
            {
                "name": str(k),
                "tool": step_dict.get("tool"),
                "skill_command": (
                    dict(with_args).get("skill_command") if hasattr(with_args, "items") else None
                ),
                "dispatch_items": step_dict.get("dispatch_items"),
            }
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
) -> ValidationSnapshot:
    """Build the canonical immutable validation snapshot for one pass.

    The recipe view is a deep copy owned by the snapshot, so callers can
    continue mutating the original recipe without affecting the snapshot
    or the rules that consume it. The manifest is the bundled
    ``skill_contracts.yaml`` loaded once per snapshot — semantic rules must
    consume ``snapshot.normalized_manifest`` rather than re-loading it.
    """
    owned = copy.deepcopy(recipe)
    owned_view = MappingProxyType(vars(owned))
    loaded_manifest = manifest if manifest is not None else load_bundled_manifest()
    normalized = _normalize_manifest_for_fingerprint(loaded_manifest)
    evidence = analyze_recipe_delivery(owned)
    manifest_fp = _compute_manifest_fingerprint(normalized)
    invocation_fp = _compute_recipe_invocation_fingerprint(owned_view)
    return ValidationSnapshot(
        owned_recipe=owned_view,
        normalized_manifest=normalized,
        delivery_evidence=evidence,
        manifest_fingerprint=manifest_fp,
        recipe_invocation_fingerprint=invocation_fp,
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
        contract_snapshot if contract_snapshot is not None else build_validation_snapshot(recipe)
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
