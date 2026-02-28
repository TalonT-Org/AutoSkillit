"""Rule registry for semantic validation rules.

Holds the rule dataclasses, registry list, and utility functions shared by
validator.py and any future rule modules. Keeping these out of validator.py
prevents that file from exceeding the 1000-line architecture limit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from autoskillit.core import Severity
from autoskillit.recipe.schema import DataFlowReport, Recipe


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
    check: Callable[[Recipe], list[RuleFinding]]


_RULE_REGISTRY: list[RuleSpec] = []


def semantic_rule(
    name: str,
    description: str,
    severity: Severity = Severity.WARNING,
) -> Callable:
    """Decorator that registers a semantic validation rule."""

    def decorator(
        fn: Callable[[Recipe], list[RuleFinding]],
    ) -> Callable[[Recipe], list[RuleFinding]]:
        _RULE_REGISTRY.append(
            RuleSpec(name=name, description=description, severity=severity, check=fn)
        )
        return fn

    return decorator


def run_semantic_rules(wf: Recipe) -> list[RuleFinding]:
    """Execute all registered semantic rules against a workflow."""
    findings: list[RuleFinding] = []
    for spec in _RULE_REGISTRY:
        findings.extend(spec.check(wf))
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
