"""Contract-recovery salvage-route requirement rule.

Derives a hard requirement directly from skill contract data (not a hand-maintained
site list): a run_skill step invoking a skill whose contract can trigger
``retry_reason=contract_recovery`` at runtime (non-empty ``expected_output_patterns``
and not ``read_only``) must declare a distinct, non-decorative ``on_context_limit``
salvage route. Without one, ``on_failure`` is used as the fallback, discarding a
completed-but-unparsed artifact instead of attempting salvage (issue #4305).

Severity is WARNING, not ERROR, as a deliberate staged rollout: the contract-derived
eligibility predicate matches far more steps than the nine sites audited and fixed by
the prior part (any non-read-only skill with expected_output_patterns qualifies, which
is most write-capable skills, not just plan-producing ones). Promoting straight to ERROR
today would flip ``valid=False`` for roughly a dozen bundled recipes with pre-existing,
unremediated gaps — and this codebase's own governance
(``test_error_severity_rules_have_no_dispatch_ready_exemptions``) forbids giving ERROR
rules a dispatch-ready exemption to paper over that, by design: fix all recipes first,
then promote. See tests/recipe/test_bundled_recipes_behavioral_properties.py's
``_SALVAGE_ROUTE_SITES`` for the nine sites already fully remediated and covered as an
unconditional structural regression test. Once a follow-up part wires salvage routes for
the remaining flagged sites, promote this rule's severity to ERROR (one-line change).
"""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import get_skill_contract, load_bundled_manifest
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

logger = get_logger(__name__)


@semantic_rule(
    name="contract-recovery-requires-salvage-route",
    description=(
        "A run_skill step invoking a skill whose contract can trigger "
        "retry_reason=contract_recovery at runtime (non-empty expected_output_patterns "
        "and not read_only) must declare an on_context_limit salvage route distinct from "
        "on_failure. This targets the same provably at-risk subset as the generic "
        "WARNING-tier run-skill-missing-context-limit rule, narrowed to steps whose skill "
        "contract can actually produce contract_recovery. Severity is WARNING pending a "
        "follow-up remediation pass across the remaining flagged sites (see module "
        "docstring); promote to ERROR once all bundled recipes are clean."
    ),
    severity=Severity.WARNING,
)
def _check_contract_recovery_requires_salvage_route(ctx: ValidationContext) -> list[RuleFinding]:
    recipe = ctx.recipe
    findings: list[RuleFinding] = []

    try:
        manifest = load_bundled_manifest()
    except (FileNotFoundError, OSError, ValueError):
        logger.warning("failed to load bundled manifest", exc_info=True)
        return findings

    # Steps that are themselves on_context_limit targets are exempt — they ARE
    # the recovery path and do not need to declare a recovery of their own.
    context_limit_targets: set[str] = set()
    for step in recipe.steps.values():
        if step.on_context_limit and step.on_context_limit not in (
            "escalate",
            "release_issue_failure",
        ):
            context_limit_targets.add(step.on_context_limit)

    for step_name, step in recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if step.action == "stop":
            continue
        if step_name in context_limit_targets:
            continue
        skill_name = step.skill_name
        if not skill_name:
            continue
        contract = get_skill_contract(skill_name, manifest)
        if contract is None:
            continue
        if contract.read_only or not contract.expected_output_patterns:
            continue
        if step.on_context_limit is not None and step.on_context_limit != step.on_failure:
            continue
        findings.append(
            make_finding(
                rule_name="contract-recovery-requires-salvage-route",
                step_name=step_name,
                message=(
                    f"Step '{step_name}' invokes '{skill_name}', whose contract can trigger "
                    f"retry_reason=contract_recovery (non-empty expected_output_patterns, not "
                    f"read_only), but declares no on_context_limit distinct from "
                    f"on_failure={step.on_failure!r}. A completed-but-unparsed artifact would "
                    f"be discarded instead of salvaged. Add a non-destructive salvage route — "
                    f"see remediation.yaml's make_plan -> salvage_plan for the pattern."
                ),
            )
        )
    return findings
