"""Lint rule: reject bare {{AUTOSKILLIT_TEMP}}/ paths in non-output_dir fields.

output_dir values are allowed to use relative {{AUTOSKILLIT_TEMP}}/suffix paths
(server-resolvable via cwd). Other fields (command, env, list args) still require
a ${{ context.* }} scope prefix to avoid cross-run collisions.
"""

from __future__ import annotations

from collections.abc import Iterator

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import RecipeStep

_BARE_TEMP_RE = re.compile(r"\{\{AUTOSKILLIT_TEMP\}\}/")
_BARE_TEMP_EXACT_RE = re.compile(r"^\{\{AUTOSKILLIT_TEMP\}\}$")
_CONTEXT_SCOPED_RE = re.compile(r"\$\{\{\s*(?:context|inputs)\.\w+\s*\}\}")
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
        elif isinstance(val, list):
            for i, item in enumerate(val):
                if isinstance(item, str):
                    yield f"{key}[{i}]", item


def _is_dry_walkthrough_step(step: RecipeStep) -> bool:
    skill_cmd = (step.with_args or {}).get("skill_command", "")
    return "dry-walkthrough" in skill_cmd


@semantic_rule(
    name="non-unique-output-path",
    description=(
        "Non-output_dir fields must scope paths through a per-run context variable. "
        "output_dir may use relative {{AUTOSKILLIT_TEMP}}/suffix (server-resolvable) "
        "but must include a unique suffix unless it is a dry-walkthrough step."
    ),
    severity=Severity.ERROR,
)
def _check_non_unique_output_path(ctx: ValidationContext) -> list[RuleFinding]:
    findings = []
    for step_name, step in ctx.recipe.steps.items():
        for key, val in _iter_path_values(step):
            if key == "output_dir":
                if _CONTEXT_SCOPED_RE.search(val):
                    continue
                if _BARE_TEMP_RE.search(val):
                    continue
                if _BARE_TEMP_EXACT_RE.search(val):
                    if _is_dry_walkthrough_step(step):
                        continue
                    findings.append(
                        RuleFinding(
                            rule="non-unique-output-path",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Step '{step_name}' uses bare '{{{{AUTOSKILLIT_TEMP}}}}' "
                                f"in output_dir without a unique suffix. "
                                "Add a skill-specific subdirectory "
                                "(e.g., '{{{{AUTOSKILLIT_TEMP}}}}/skill-name')."
                            ),
                        )
                    )
            else:
                if _BARE_TEMP_RE.search(val) and not _CONTEXT_SCOPED_RE.search(val):
                    findings.append(
                        RuleFinding(
                            rule="non-unique-output-path",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Step '{step_name}' uses a bare "
                                f"'{{{{AUTOSKILLIT_TEMP}}}}/' path in '{key}' "
                                "without a context-variable scope prefix. "
                                "Capture a unique per-run directory in the init step "
                                "and reference it via "
                                "${{{{ context.run_dir }}}} or similar."
                            ),
                        )
                    )
    return findings
