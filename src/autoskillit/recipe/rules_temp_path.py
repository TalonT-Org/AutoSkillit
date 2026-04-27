"""Lint rule: reject bare {{AUTOSKILLIT_TEMP}}/ paths without a context-variable scope prefix."""

from __future__ import annotations

import re
from collections.abc import Iterator

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import RecipeStep

_BARE_TEMP_RE = re.compile(r"\{\{AUTOSKILLIT_TEMP\}\}/")
_CONTEXT_SCOPED_RE = re.compile(r"\$\{\{\s*context\.\w+\s*\}\}")
_SKIP_KEYS = frozenset({"step_name", "callable", "pass_name"})


def _iter_path_values(step: RecipeStep) -> Iterator[tuple[str, str]]:
    for key, val in (step.with_args or {}).items():
        if key in _SKIP_KEYS:
            continue
        if key == "env" and isinstance(val, dict):
            for env_key, env_val in val.items():
                if isinstance(env_val, str):
                    yield f"env.{env_key}", env_val
        elif isinstance(val, str):
            yield key, val


@semantic_rule(
    name="non-unique-output-path",
    description=(
        "Recipe steps must scope output paths through a per-run context variable. "
        "Bare {{AUTOSKILLIT_TEMP}}/ paths are shared across runs and cause "
        "stale-artifact failures."
    ),
    severity=Severity.ERROR,
)
def _check_non_unique_output_path(ctx: ValidationContext) -> list[RuleFinding]:
    findings = []
    for step_name, step in ctx.recipe.steps.items():
        for key, val in _iter_path_values(step):
            if _BARE_TEMP_RE.search(val) and not _CONTEXT_SCOPED_RE.search(val):
                findings.append(
                    RuleFinding(
                        rule="non-unique-output-path",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' uses a bare '{{{{AUTOSKILLIT_TEMP}}}}/' "
                            f"path in '{key}' without a context-variable scope prefix. "
                            "Capture a unique per-run directory in the init step "
                            "and reference it via ${{{{ context.run_dir }}}} or similar."
                        ),
                    )
                )
    return findings
