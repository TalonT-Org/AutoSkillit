"""Semantic rule: criterion-schema-drift

Fires when a bundled canary manifest consumed by parse_agent_eval_manifests uses
plain-string detection_criteria entries instead of structured {text, type} objects.
Plain strings are rejected by the runtime validator, so manifests using them will
fail at execution time. Catching the drift at recipe-validation time prevents
runtime errors.
"""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_TARGET_CALLABLE = "parse_agent_eval_manifests"
_MANIFEST_ARG = "canary_manifest"
_REQUIRED_CRITERION_KEYS = frozenset({"text", "type"})


def _is_criterion_drift(criteria: object) -> bool:
    if not isinstance(criteria, list):
        return False
    for entry in criteria:
        if not isinstance(entry, dict):
            return True
        if not _REQUIRED_CRITERION_KEYS <= entry.keys():
            return True
    return False


@semantic_rule(
    name="criterion-schema-drift",
    description=(
        "Bundled canary manifests consumed by parse_agent_eval_manifests must use "
        "structured {text, type} detection_criteria — plain strings are rejected."
    ),
    severity=Severity.ERROR,
)
def _check_criterion_schema_drift(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_python":
            continue
        callable_val = str(step.with_args.get("callable", ""))
        callable_leaf = callable_val.rsplit(".", 1)[-1]
        if callable_leaf != _TARGET_CALLABLE:
            continue
        manifest_arg = step.with_args.get(_MANIFEST_ARG, "")
        if not manifest_arg:
            continue
        manifest_path = Path(str(manifest_arg))
        if not manifest_path.exists():
            continue
        try:
            canaries = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(canaries, list):
            continue
        for canary in canaries:
            if not isinstance(canary, dict):
                continue
            canary_id = canary.get("id", "?")
            criteria = canary.get("detection_criteria", [])
            if _is_criterion_drift(criteria):
                findings.append(
                    RuleFinding(
                        rule="criterion-schema-drift",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"Canary {canary_id} in {manifest_path} uses plain-string "
                            f"detection_criteria — must be structured {{text, type}} objects."
                        ),
                    )
                )
    return findings
