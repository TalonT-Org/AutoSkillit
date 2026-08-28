"""Keep recipe-field runtime prose aligned with the consumption ledger.

The scanner intentionally recognizes only recipe dataclass field names beside a
small runtime-effect verb vocabulary. It is a narrow consistency guard, not a
general natural-language verifier; broad prose matching would create more noise
than signal and obscure a real deferred runtime claim.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from autoskillit.recipe.schema import RecipeStep
from tests.arch._deferred_debt import TrackedDeferral
from tests.arch.test_recipe_dataclass_consumption import DEFERRED_RECIPE_FIELDS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]

_EFFECT_VERBS = r"skip|route|enforce|gate|dispatch|execute|apply"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _production_texts() -> dict[Path, str]:
    root = _project_root()
    paths = list((root / "docs").rglob("*.md")) + [
        root / "src/autoskillit/cli/prompts/_prompts_orchestrator.py",
        root / "src/autoskillit/cli/prompts/_prompts_kitchen.py",
        root / "src/autoskillit/recipe/_api_orchestration.py",
        root / "src/autoskillit/server/tools/tools_recipe.py",
        root / "src/autoskillit/skills/sous-chef/SKILL.md",
    ]
    return {path: path.read_text(encoding="utf-8") for path in paths if path.is_file()}


def _deferred_runtime_claims(
    texts: Mapping[object, str],
    deferrals: Mapping[tuple[type[object], str], TrackedDeferral],
) -> dict[object, set[str]]:
    deferred_names = {field for _, field in deferrals}
    claims: dict[object, set[str]] = {}
    for source, text in texts.items():
        for line in text.splitlines():
            for field in deferred_names:
                field_pattern = rf"(?:`{re.escape(field)}`|Recipe\w*\.{re.escape(field)})"
                if re.search(
                    rf"(?:{field_pattern}.{{0,100}}\b(?:{_EFFECT_VERBS})\b|"
                    rf"\b(?:{_EFFECT_VERBS})\b.{{0,100}}{field_pattern})",
                    line,
                    re.IGNORECASE,
                ):
                    claims.setdefault(source, set()).add(field)
    return claims


def test_documented_runtime_claims_cannot_target_deferred_fields() -> None:
    claims = _deferred_runtime_claims(_production_texts(), DEFERRED_RECIPE_FIELDS)
    assert not claims, (
        "production prose claims runtime semantics for deferred recipe fields: "
        f"{ {str(source): sorted(fields) for source, fields in claims.items()} }"
    )


def test_deferred_skip_when_true_claim_is_rejected_without_global_mutation() -> None:
    synthetic_deferrals = {
        (RecipeStep, "skip_when_true"): TrackedDeferral(
            issue=4891,
            rationale="Synthetic guard input proves a deferred runtime claim is rejected.",
            added_date=DEFERRED_RECIPE_FIELDS[next(iter(DEFERRED_RECIPE_FIELDS))].added_date,
        )
    }
    claims = _deferred_runtime_claims(
        {"synthetic": "`skip_when_true` will skip the guarded recipe step"},
        synthetic_deferrals,
    )
    assert claims == {"synthetic": {"skip_when_true"}}
