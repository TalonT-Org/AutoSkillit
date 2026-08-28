"""Shared owner-qualified recipe field ledger and consumers.

Lives in tests/arch/_recipe_field_ledger.py so that both arch and contracts tests
import from a single source of truth without depending on each other's test
modules. Mirrors the pattern of tests/arch/_deferred_debt.py.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from datetime import date
from typing import Literal

from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep
from tests.arch._deferred_debt import TrackedDeferral

RecipeDataclass = type[Recipe] | type[RecipeIngredient] | type[RecipeStep]
FieldKey = tuple[RecipeDataclass, str]
_Classification = Literal["execution", "composition", "validation-only"]


@dataclasses.dataclass(frozen=True, slots=True)
class DeclaredFieldDef:
    classification: _Classification
    consumer_sites: tuple[str, ...]
    behavioral_test: str | None = None


_BEHAVIORAL_ANCHOR = (
    "tests/server/test_step_guard_admission.py::"
    "test_truthy_guard_bypasses_dispatch_marks_tracker_and_unblocks_dependents"
)
_COMPOSITION_ANCHOR = (
    "tests/recipe/test_skip_guard_deferral.py::"
    "test_resolve_skip_guards_strips_optional_true_on_truthy"
)
_VALIDATION_SITE = "autoskillit.recipe.validator:validate_recipe_structure"
_COMPOSITION_SITE = "autoskillit.recipe._recipe_composition:_build_active_recipe"
_EXECUTION_SITE = "autoskillit.server._recipe_execution:build_recipe_execution_snapshot"
_RECIPE_SITE = "autoskillit.recipe.io:load_recipe"


def _field_defs(
    owner: RecipeDataclass,
    fields: Iterable[str],
    classification: _Classification,
    consumer_site: str,
    behavioral_test: str | None = None,
) -> dict[FieldKey, DeclaredFieldDef]:
    return {
        (owner, field): DeclaredFieldDef(classification, (consumer_site,), behavioral_test)
        for field in fields
    }


DECLARED_RECIPE_FIELDS: dict[FieldKey, DeclaredFieldDef] = (
    _field_defs(
        RecipeIngredient,
        ("default", "description", "hidden", "required"),
        "execution",
        _EXECUTION_SITE,
        _BEHAVIORAL_ANCHOR,
    )
    | _field_defs(
        RecipeStep,
        (
            "action",
            "idle_output_timeout",
            "message",
            "model",
            "provider",
            "stale_threshold",
            "tool",
            "with_args",
            "skip_when_true",
        ),
        "execution",
        _EXECUTION_SITE,
        _BEHAVIORAL_ANCHOR,
    )
    | _field_defs(
        RecipeStep,
        (
            "gate",
            "on_context_limit",
            "on_exhausted",
            "on_failure",
            "on_rate_limit",
            "on_result",
            "on_skip",
            "on_success",
            "skip_when_false",
            "sub_recipe",
        ),
        "composition",
        _COMPOSITION_SITE,
        _COMPOSITION_ANCHOR,
    )
    | _field_defs(
        RecipeStep,
        (
            "block",
            "capture",
            "capture_list",
            "constant",
            "name",
            "note",
            "optional",
            "optional_context_refs",
            "pass_through",
            "phoropter_family",
            "python",
            "retries",
        ),
        "validation-only",
        _VALIDATION_SITE,
    )
    | _field_defs(
        Recipe,
        (
            "categories",
            "composite_hash",
            "content_hash",
            "description",
            "dispatch_only",
            "experimental",
            "ingredients",
            "kitchen_rules",
            "name",
            "requires_features",
            "requires_packs",
            "summary",
        ),
        "execution",
        _RECIPE_SITE,
        _BEHAVIORAL_ANCHOR,
    )
    | _field_defs(
        Recipe,
        (
            "delivery_segments",
            "dispatches",
            "kind",
            "steps",
        ),
        "composition",
        _COMPOSITION_SITE,
        _COMPOSITION_ANCHOR,
    )
    | _field_defs(
        Recipe,
        ("recipe_version", "version"),
        "validation-only",
        _VALIDATION_SITE,
    )
)

# These fields are deliberately declared but have no effective consumer.  The
# issue bodies describe the missing authority/type/dispatch semantics; do not
# turn a deferral into a classification merely because a parser happens to see it.
DEFERRED_RECIPE_FIELDS: dict[FieldKey, TrackedDeferral] = {
    (RecipeIngredient, "authority"): TrackedDeferral(
        issue=4891,
        rationale="Kitchen opening accepts config authority but leaves its no-op path unenforced.",
        added_date=date(2026, 8, 27),
    ),
    (RecipeIngredient, "type"): TrackedDeferral(
        issue=4892,
        rationale="Declared ingredient types have no value-level validation at input resolution.",
        added_date=date(2026, 8, 27),
    ),
    (Recipe, "allowed_recipes"): TrackedDeferral(
        issue=4893,
        rationale="Recipe allowlists are parsed but not enforced by recipe dispatch.",
        added_date=date(2026, 8, 27),
    ),
    (Recipe, "blocks"): TrackedDeferral(
        issue=4893,
        rationale=(
            "Parsed recipe blocks are assigned but have no external validation or runtime reader."
        ),
        added_date=date(2026, 8, 27),
    ),
    (Recipe, "continue_on_failure"): TrackedDeferral(
        issue=4893,
        rationale=(
            "The recipe failure-continuation declaration has no composition or execution consumer."
        ),
        added_date=date(2026, 8, 27),
    ),
    (Recipe, "requires_recipe_packs"): TrackedDeferral(
        issue=4893,
        rationale="Required recipe packs are parsed but not enforced by recipe dispatch.",
        added_date=date(2026, 8, 27),
    ),
    (RecipeStep, "declared_with_args"): TrackedDeferral(
        issue=4893,
        rationale=(
            "The declared argument snapshot is self-validated but has no external "
            "behavior consumer."
        ),
        added_date=date(2026, 8, 27),
    ),
}

DECLARED_RECIPE_FIELDS = dict(
    sorted(
        DECLARED_RECIPE_FIELDS.items(),
        key=lambda item: (item[0][0].__name__, item[0][1]),
    )
)
DEFERRED_RECIPE_FIELDS = dict(
    sorted(
        DEFERRED_RECIPE_FIELDS.items(),
        key=lambda item: (item[0][0].__name__, item[0][1]),
    )
)
