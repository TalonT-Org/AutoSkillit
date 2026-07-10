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

from autoskillit.core import (
    DISPATCH_ITEM_PLACEHOLDER,
    OPTIONAL_ARG_OMISSION_SENTINEL,
    BindingForm,
    BindingState,
    InputBinding,
    ResolutionStatus,
    extract_positional_args,
    resolve_skill_name,
)
from autoskillit.recipe._contracts_manifest import (
    _CONTEXT_REF_RE,
    INPUT_REF_RE,
    resolve_input_contract,
)
from autoskillit.recipe.tool_registry import for_tool, unsupported_params

__all__ = [
    "DeliveryEvidence",
    "DeliveryEvidenceMap",
    "analyze_step_delivery",
    "analyze_recipe_delivery",
    "OPTIONAL_ARG_OMISSION_SENTINEL",
    "effective_consumption",
    "input_receives_ref",
    "binding_for_input",
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
    worker_bound_qualified_refs: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    tool_bound_qualified_refs: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    orchestrator_control_qualified_refs: frozenset[tuple[str, str]] = field(
        default_factory=frozenset
    )
    input_bindings: tuple[InputBinding, ...] = ()

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
    """Immutable per-step evidence map keyed by step name.

    ``manifest_snapshot_id`` is the legacy cheap string derived from recipe
    type/name. ``manifest_fingerprint`` and ``recipe_invocation_fingerprint``
    are the canonical content-derived identifiers paired by the validation
    snapshot — a per-pass pair that forbids cross-recipe evidence
    substitution. They are distinct from the source ``content_hash`` /
    ``composite_hash`` provenance identities (those remain authoritative for
    rerun detection).
    """

    steps: Mapping[str, DeliveryEvidence]
    manifest_snapshot_id: str
    manifest_fingerprint: str = ""
    recipe_invocation_fingerprint: str = ""

    def for_step(self, step_name: str) -> DeliveryEvidence | None:
        return self.steps.get(step_name)

    def all_unsupported_keys(self) -> dict[str, frozenset[str]]:
        return {
            name: ev.unsupported_keys for name, ev in self.steps.items() if ev.unsupported_keys
        }


def _extract_qualified_refs(value: str | None) -> frozenset[tuple[str, str]]:
    """Extract namespace-preserving template references from ``value``."""
    if not value:
        return frozenset()
    refs = {("context", name) for name in _CONTEXT_REF_RE.findall(value)}
    refs.update(("inputs", name) for name in INPUT_REF_RE.findall(value))
    return frozenset(refs)


def _flat_ref_names(refs: frozenset[tuple[str, str]]) -> frozenset[str]:
    """Project qualified refs to the legacy name-only compatibility view."""
    return frozenset(name for _, name in refs)


def _resolve_input_bindings(skill_command: str) -> tuple[InputBinding, ...]:
    """Resolve manifest inputs to their absolute positional command tokens."""
    resolution = resolve_input_contract(skill_command)
    if resolution.status not in {
        ResolutionStatus.VALID,
        ResolutionStatus.KNOWN_ZERO_INPUT,
    }:
        return ()
    args = extract_positional_args(skill_command)
    bindings: list[InputBinding] = []
    for spec in resolution.inputs:
        if spec.position >= len(args):
            bindings.append(
                InputBinding(
                    position=spec.position,
                    name=spec.name,
                    type=spec.type,
                    required=spec.required,
                    form=BindingForm.EMPTY,
                    state=BindingState.UNBOUND,
                )
            )
            continue
        token = args[spec.position]
        if token == OPTIONAL_ARG_OMISSION_SENTINEL:
            state = BindingState.OMITTED
            form = BindingForm.POSITIONAL
        elif token == DISPATCH_ITEM_PLACEHOLDER:
            state = BindingState.DISPATCH_OCCUPIED
            form = BindingForm.DISPATCH_SPLICE
        else:
            state = BindingState.BOUND
            form = BindingForm.POSITIONAL
        refs = _extract_qualified_refs(token)
        ref_namespace: str | None = None
        ref_name: str | None = None
        diagnostics: tuple[str, ...] = ()
        if len(refs) == 1:
            ref_namespace, ref_name = next(iter(refs))
        elif len(refs) > 1:
            diagnostics = ("input slot contains multiple template references",)
        bindings.append(
            InputBinding(
                position=spec.position,
                name=spec.name,
                type=spec.type,
                required=spec.required,
                form=form,
                state=state,
                source_token=token,
                ref_namespace=ref_namespace,
                ref_name=ref_name,
                diagnostics=diagnostics,
            )
        )
    return tuple(bindings)


def _detect_orchestrator_control_keys(step: Any) -> frozenset[tuple[str, str]]:
    """Read orchestrator-only fields off a step as qualified refs."""
    refs: set[tuple[str, str]] = set()
    dispatch_items = getattr(step, "dispatch_items", None)
    if isinstance(dispatch_items, str) and dispatch_items:
        refs.update(_extract_qualified_refs(dispatch_items))
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
    worker_qualified = _extract_qualified_refs(skill_command)
    worker_bound = _flat_ref_names(worker_qualified)
    input_bindings = _resolve_input_bindings(skill_command) if skill_command else ()

    # Tool-bound refs: refs in with_args values where the key is a registered tool param
    # AND the value is not itself the skill_command token (which is the command, not
    # a context-carrying param). The unsupported set is computed via the canonical
    # ``unsupported_params`` helper so the registry parity tests and the
    # delivery-evidence parser cannot drift apart.
    tool_param_set: frozenset[str] = frozenset()
    if tool:
        td = for_tool(tool)
        if td is not None:
            tool_param_set = td.param_set

    tool_qualified: set[tuple[str, str]] = set()
    if isinstance(with_args, dict):
        for key, value in with_args.items():
            if key == "skill_command":
                continue
            if key in tool_param_set:
                tool_qualified.update(_extract_qualified_refs(str(value)))
    tool_bound = _flat_ref_names(frozenset(tool_qualified))

    unsupported: frozenset[str]
    if isinstance(with_args, dict):
        unsupported = unsupported_params(tool or "", frozenset(with_args.keys()))
    else:
        unsupported = frozenset()

    # Orchestrator-control refs: top-level dispatch_items, etc.
    orch_qualified = _detect_orchestrator_control_keys(step)
    orch_control = _flat_ref_names(orch_qualified)

    # Availability-only refs: declared in optional_context_refs but not bound anywhere.
    # Caller may pass a list explicitly; otherwise read from the step attribute.
    declared_optional: set[str]
    if optional_context_refs is None:
        declared_optional = set(getattr(step, "optional_context_refs", []) or [])
    else:
        declared_optional = set(optional_context_refs)
    all_bound = set(worker_bound) | set(tool_bound) | set(orch_control)
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
        worker_bound_qualified_refs=worker_qualified,
        tool_bound_qualified_refs=frozenset(tool_qualified),
        orchestrator_control_qualified_refs=orch_qualified,
        input_bindings=input_bindings,
    )


def analyze_recipe_delivery(recipe: Any) -> DeliveryEvidenceMap:
    """Build a DeliveryEvidenceMap for every step in a recipe."""
    from autoskillit.core import get_logger

    logger = get_logger(__name__)
    steps_attr = getattr(recipe, "steps", {}) or {}
    out: dict[str, DeliveryEvidence] = {}
    for step_name, step in steps_attr.items():
        # Ensure the step's name is set so evidence carries it.
        if getattr(step, "name", "") != step_name:
            try:
                object.__setattr__(step, "name", step_name)
            except Exception:
                logger.warning("Failed to set step name attribute", exc_info=True)
        optional_refs = list(getattr(step, "optional_context_refs", []) or [])
        out[step_name] = analyze_step_delivery(step, optional_context_refs=optional_refs)
    manifest_id = type(recipe).__name__ + ":" + str(getattr(recipe, "name", ""))
    return DeliveryEvidenceMap(steps=out, manifest_snapshot_id=manifest_id)


def effective_consumption(evidence: DeliveryEvidence) -> frozenset[str]:
    """Return the canonical effective-consumption set for a single step.

    Effective consumption is the union of:
      - worker-bound refs (refs inside the skill_command)
      - tool-bound refs (refs that feed a registered MCP parameter)
      - orchestrator-control refs (typed dispatch_items sources)

    Availability-only refs (declared in optional_context_refs but unbound)
    and unsupported sibling keys never enter this set. A correctly named ref
    that appears only as an inert with: sibling is therefore not consumed,
    proving it cannot satisfy a bilateral/inventory rule or invalidate a
    captured downstream state.
    """
    return (
        evidence.worker_bound_refs | evidence.tool_bound_refs | evidence.orchestrator_control_refs
    )


def input_receives_ref(
    evidence: DeliveryEvidence,
    *,
    namespace: str,
    name: str,
    input_name: str | None = None,
) -> bool:
    """Return True iff ``evidence`` shows the named ref reached an absolute input slot.

    Namespace is preserved: ``inputs.foo`` cannot satisfy a check for
    ``context.foo`` and vice versa. Both worker-bound (inside
    ``skill_command``) and tool-bound (registered MCP parameter) refs count
    as "received" — but only when the namespace matches exactly.
    """
    return any(
        binding.state == BindingState.BOUND
        and binding.ref_namespace == namespace
        and binding.ref_name == name
        and (input_name is None or binding.name == input_name)
        for binding in evidence.input_bindings
    )


def binding_for_input(
    evidence: DeliveryEvidence,
    *,
    name: str,
) -> bool:
    """Return True iff ``evidence`` shows the named input was bound to anything.

    Namespace-agnostic convenience predicate. Use ``input_receives_ref``
    when the namespace matters.
    when the namespace matters.
    """
    return any(
        binding.name == name
        and binding.state in {BindingState.BOUND, BindingState.DISPATCH_OCCUPIED}
        for binding in evidence.input_bindings
    )


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
