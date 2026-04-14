"""Rule registry for semantic validation rules.

Holds the rule dataclasses, registry list, and utility functions shared by
validator.py and any future rule modules. Keeping these out of validator.py
prevents that file from exceeding the 1000-line architecture limit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from autoskillit.core import Severity

# Imported at module level for type annotations (no circular import: _analysis.py does
# not import registry.py). The runtime isinstance() check in run_semantic_rules also
# requires this to be available as a concrete class, not just a TYPE_CHECKING stub.
from autoskillit.recipe._analysis import ValidationContext  # noqa: E402
from autoskillit.recipe.schema import DataFlowReport, Recipe, RecipeBlock


@dataclass
class RuleFinding:
    """A single finding produced by a semantic rule."""

    rule: str
    severity: Severity
    step_name: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "rule": self.rule,
            "severity": self.severity.value,
            "step": self.step_name,
            "message": self.message,
        }


@dataclass
class RuleSpec:
    """Internal: metadata for one registered rule."""

    name: str
    description: str
    severity: Severity
    check: Callable[[ValidationContext], list[RuleFinding]]


_RULE_REGISTRY: list[RuleSpec] = []


@dataclass(frozen=True)
class BlockContext:
    """Per-block dispatch context for block-level semantic rules.

    Block rules receive a ``BlockContext`` rather than a full ``ValidationContext``
    to enforce the constraint that rules operate on exactly one block at a time.
    ``parent`` is available read-only for cross-block lookups if ever needed.
    """

    block: RecipeBlock
    parent: ValidationContext


@dataclass
class BlockRuleSpec:
    """Internal: metadata for one registered block-level rule."""

    name: str
    description: str
    severity: Severity
    check: Callable[[BlockContext], list[RuleFinding]]


_BLOCK_RULE_REGISTRY: list[BlockRuleSpec] = []


def semantic_rule(
    name: str,
    description: str,
    severity: Severity = Severity.WARNING,
) -> Callable:
    """Decorator that registers a semantic validation rule."""

    def decorator(
        fn: Callable[[ValidationContext], list[RuleFinding]],
    ) -> Callable[[ValidationContext], list[RuleFinding]]:
        _RULE_REGISTRY.append(
            RuleSpec(name=name, description=description, severity=severity, check=fn)
        )
        return fn

    return decorator


def block_rule(
    name: str,
    description: str,
    severity: Severity = Severity.WARNING,
) -> Callable:
    """Decorator that registers a block-level semantic validation rule.

    The decorated function receives a ``BlockContext`` (one block at a time) and
    returns a list of ``RuleFinding`` objects.  Registered rules are dispatched
    by ``run_semantic_rules`` for every block in ``ctx.blocks``.
    """

    def decorator(
        fn: Callable[[BlockContext], list[RuleFinding]],
    ) -> Callable[[BlockContext], list[RuleFinding]]:
        _BLOCK_RULE_REGISTRY.append(
            BlockRuleSpec(name=name, description=description, severity=severity, check=fn)
        )
        return fn

    return decorator


def run_semantic_rules(wf: Recipe | ValidationContext) -> list[RuleFinding]:
    """Execute all registered semantic rules against a workflow.

    Accepts either a bare ``Recipe`` or a pre-built ``ValidationContext``.
    When a ``Recipe`` is passed, a ``ValidationContext`` is built once and
    shared across all rules, avoiding redundant graph and dataflow computation.
    """
    from autoskillit.recipe._analysis import make_validation_context

    ctx = wf if isinstance(wf, ValidationContext) else make_validation_context(wf)
    findings: list[RuleFinding] = []
    for spec in _RULE_REGISTRY:
        findings.extend(spec.check(ctx))
    # Dispatch block-level rules for every declared block in the recipe.
    for block in ctx.blocks:
        bctx = BlockContext(block=block, parent=ctx)
        for bspec in _BLOCK_RULE_REGISTRY:
            findings.extend(bspec.check(bctx))
    return findings


def findings_to_dicts(findings: list[RuleFinding]) -> list[dict[str, str]]:
    """Convert a list of RuleFindings to serializable dicts."""
    return [f.to_dict() for f in findings]


def filter_version_rule(suggestions: list[dict[str, str]]) -> list[dict[str, str]]:
    """Remove 'outdated-recipe-version' rule findings from suggestions."""
    return [s for s in suggestions if s.get("rule") != "outdated-recipe-version"]


def build_quality_dict(report: DataFlowReport) -> dict[str, object]:
    """Build the quality analysis dict from a DataFlowReport."""
    return {
        "warnings": [
            {
                "code": w.code,
                "step": w.step_name,
                "field": w.field,
                "message": w.message,
            }
            for w in report.warnings
        ],
        "summary": report.summary,
    }


def compute_recipe_validity(
    errors: list[str],
    semantic_findings: list[RuleFinding],
    contract_findings: list[dict],  # type: ignore[type-arg]
) -> bool:
    """Return True if no schema, semantic, or contract errors are present."""
    has_schema_errors = bool(errors)
    has_semantic_errors = any(f.severity == Severity.ERROR for f in semantic_findings)
    has_contract_errors = any(f.get("severity") == "error" for f in contract_findings)
    return not has_schema_errors and not has_semantic_errors and not has_contract_errors
