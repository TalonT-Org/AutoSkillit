"""Delivery evidence analyzer — single source of truth for worker-payload delivery.

A ``DeliveryEvidence`` distinguishes four classes of context references:

1. **tool-bound**: a reference whose value is consumed by the MCP tool's
   runtime parameter (e.g., ``cwd`` for ``run_skill``). These refs may carry
   values the worker never sees; the tool receives them.
2. **worker-bound**: a reference whose template expression appears inside the
   ``skill_command`` string itself. This is the only authoritative proof that
   a value reaches the worker process. A correctly named ref in the wrong
   position is NOT worker-bound.
3. **orchestrator-control**: a top-level ``RecipeStep`` field declared for
   orchestrator routing (currently ``dispatch_items``). Never delivered to the
   worker; never consumed by the MCP tool's parameter set.
4. **availability-only**: a reference declared in ``optional_context_refs`` to
   permit a cyclic reference before first capture. It is NOT delivery evidence
   by itself; it merely declares the ref is allowed to be undefined.

Anything not in those four classes is **unsupported** — a ``with:`` key not in
the canonical tool parameter set, or a context ref that is neither worker-bound
nor tool-bound. Unsupported keys MUST NOT satisfy bilateral/inventory rules
and MUST NOT consume captured state.

IL-2 module: depends only on IL-0 ``autoskillit.core`` and IL-1
``autoskillit.recipe`` primitives. Does NOT import server handlers, rule
modules, or analysis consumers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from autoskillit.core import OPTIONAL_ARG_OMISSION_SENTINEL, resolve_skill_name
from autoskillit.recipe._contracts_manifest import (
    _CONTEXT_REF_RE,
    INPUT_REF_RE,
)
from autoskillit.recipe.tool_registry import for_tool

__all__ = [
    "DeliveryEvidence",
    "DeliveryEvidenceMap",
    "analyze_step_delivery",
    "analyze_recipe_delivery",
    "OPTIONAL_ARG_OMISSION_SENTINEL",
]


@dataclass(frozen=True, slots=True)
class DeliveryEvidence:
    """Per-step delivery evidence derived from a single ``RecipeStep``.

    ``worker_bound_refs`` is the canonical proof set consumed by all semantic
    rules, contract cards, and dataflow detectors. ``unsupported_keys`` is the
    set of ``with:`` keys NOT in the canonical tool parameter set; their
    presence in a recipe is a structural defect (WARNING for non-``run_skill``
    tools; ERROR for ``run_skill`` after this plan migrates its sibling debt).
    """

    step_name: str
    tool: str | None
    skill_name: str | None
    worker_bound_refs: frozenset[str] = field(default_factory=frozenset)
    tool_bound_refs: frozenset[str] = field(default_factory=frozenset)
    orchestrator_control_refs: frozenset[str] = field(default_factory=frozenset)
    availability_only_refs: frozenset[str] = field(default_factory=frozenset)
    unsupported_keys: frozenset[str] = field(default_factory=frozenset)

    @property
    def delivered_refs(self) -> frozenset[str]:
        """Union of worker-bound and tool-bound refs.

        Use this for "is the capture consumed?" checks — a ref is consumed
        only if it has been routed to either the worker command or the tool's
        parameter set.
        """
        return self.worker_bound_refs | self.tool_bound_refs

    @property
    def has_orphan_siblings(self) -> bool:
        return bool(self.unsupported_keys)


@dataclass(frozen=True, slots=True)
class DeliveryEvidenceMap:
    """Immutable per-step evidence map keyed by step name."""

    steps: Mapping[str, DeliveryEvidence]
    manifest_snapshot_id: str

    def for_step(self, step_name: str) -> DeliveryEvidence | None:
        return self.steps.get(step_name)

    def all_unsupported_keys(self) -> dict[str, frozenset[str]]:
        return {
            name: ev.unsupported_keys for name, ev in self.steps.items() if ev.unsupported_keys
        }


def _extract_refs_from_command(skill_command: str | None) -> frozenset[str]:
    """Extract ${{ context.X }} and ${{ inputs.X }} references from a skill_command string."""
    if not skill_command:
        return frozenset()
    ctx = set(_CONTEXT_REF_RE.findall(skill_command))
    inp = set(INPUT_REF_RE.findall(skill_command))
    return frozenset(ctx | inp)


def _detect_orchestrator_control_keys(step: Any) -> frozenset[str]:
    """Read orchestrator-only fields off a step and project them to ref names."""
    refs: set[str] = set()
    dispatch_items = getattr(step, "dispatch_items", None)
    if isinstance(dispatch_items, str) and dispatch_items:
        refs.update(_CONTEXT_REF_RE.findall(dispatch_items))
        refs.update(INPUT_REF_RE.findall(dispatch_items))
    return frozenset(refs)


def analyze_step_delivery(
    step: Any,
    *,
    optional_context_refs: Sequence[str] | None = None,
) -> DeliveryEvidence:
    """Build DeliveryEvidence for a single RecipeStep.

    Does NOT mutate the step. Accepts ``optional_context_refs`` to avoid
    re-reading the step attribute (callers may pass the parsed YAML value).
    """
    name = getattr(step, "name", "<unknown>")
    tool = getattr(step, "tool", None)
    with_args = getattr(step, "with_args", {}) or {}
    skill_command = str(with_args.get("skill_command", "")) if with_args else ""
    skill_name = resolve_skill_name(skill_command) if skill_command else None

    # Worker-bound refs: refs whose template appears inside the skill_command string.
    worker_bound = _extract_refs_from_command(skill_command)

    # Tool-bound refs: refs in with_args values where the key is a registered tool param
    # AND the value is not itself the skill_command token (which is the command, not
    # a context-carrying param).
    tool_def = for_tool(tool) if tool else None
    tool_param_set: frozenset[str] = tool_def.param_set if tool_def is not None else frozenset()

    tool_bound: set[str] = set()
    unsupported: set[str] = set()
    if isinstance(with_args, dict):
        for key, value in with_args.items():
            if key == "skill_command":
                continue
            if key in tool_param_set:
                ctx_refs = set(_CONTEXT_REF_RE.findall(str(value)))
                inp_refs = set(INPUT_REF_RE.findall(str(value)))
                tool_bound.update(ctx_refs | inp_refs)
            else:
                unsupported.add(key)

    # Orchestrator-control refs: top-level dispatch_items, etc.
    orch_control = _detect_orchestrator_control_keys(step)

    # Availability-only refs: declared in optional_context_refs but not bound anywhere.
    declared_optional = set(optional_context_refs or [])
    all_bound = worker_bound | tool_bound | orch_control
    availability = frozenset(declared_optional - all_bound)

    # Filter worker-bound and tool-bound to exclude orch_control (they're distinct views)
    worker_bound = frozenset(worker_bound - orch_control)

    return DeliveryEvidence(
        step_name=name,
        tool=tool,
        skill_name=skill_name,
        worker_bound_refs=worker_bound,
        tool_bound_refs=frozenset(tool_bound),
        orchestrator_control_refs=orch_control,
        availability_only_refs=availability,
        unsupported_keys=frozenset(unsupported),
    )


def analyze_recipe_delivery(recipe: Any) -> DeliveryEvidenceMap:
    """Build a DeliveryEvidenceMap for every step in a recipe."""
    steps_attr = getattr(recipe, "steps", {}) or {}
    out: dict[str, DeliveryEvidence] = {}
    for step_name, step in steps_attr.items():
        # Ensure the step's name is set so evidence carries it.
        if getattr(step, "name", "") != step_name:
            try:
                object.__setattr__(step, "name", step_name)
            except Exception:
                pass
        optional_refs = list(getattr(step, "optional_context_refs", []) or [])
        out[step_name] = analyze_step_delivery(step, optional_context_refs=optional_refs)
    manifest_id = type(recipe).__name__ + ":" + str(getattr(recipe, "name", ""))
    return DeliveryEvidenceMap(steps=out, manifest_snapshot_id=manifest_id)


def is_invalid_run_skill_sibling(step: Any) -> bool:
    """Return True if a step is a run_skill step that declares unsupported siblings.

    Convenience predicate for the ``unsupported-run-skill-param`` ERROR rule.
    """
    if getattr(step, "tool", None) != "run_skill":
        return False
    evidence = analyze_step_delivery(
        step, optional_context_refs=getattr(step, "optional_context_refs", [])
    )
    return bool(evidence.unsupported_keys)


__all__ += ["is_invalid_run_skill_sibling"]
