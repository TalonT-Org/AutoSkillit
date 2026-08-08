"""RecipeStep field-classification ledger — the forcing function for silent
declared-but-inert fields (#4402).

A frozen field-name -> classification mapping, diffed bidirectionally
against ``dataclasses.fields(RecipeStep)``. A new field without a conscious
classification fails; a removed field leaves a stale entry that fails.
Mirrors the config-key-ledger pattern (#4303, ``test_config_key_ledger.py``)
— an inline Python dict rather than an external ``.txt`` file, since these
values carry structure (``inert-tracked:#NNNN`` is itself validated), unlike
that ledger's flat name list.

Classifications:
  ``execution``          — read on a runtime code path (server fallback,
                            composition dispatch, executor argument).
  ``composition``         — consumed while composing/pruning the pipeline
                            graph before dispatch.
  ``validation-only``     — consumed exclusively by ``recipe/rules/`` lint
                            rules or schema validation, never by
                            execution/composition.
  ``inert-tracked:#NNNN`` — zero consumer outside schema/parse code; the
                            open tracking issue is part of the value, so an
                            inert entry without a live ticket is visible at
                            review time.

Populated by reading actual consumer sites (not copied blindly from any
plan) — where a description and the code disagree, the code wins and the
ledger records reality.
"""

from __future__ import annotations

import re
from dataclasses import fields as dataclass_fields

import pytest

from autoskillit.recipe.schema import RecipeStep

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_INERT_TRACKED_RE = re.compile(r"^inert-tracked:#\d+$")
_KNOWN_CLASSIFICATIONS = frozenset({"execution", "composition", "validation-only"})

# Keys MUST stay sorted — enforced by test_ledger_is_sorted() below.
RECIPE_STEP_FIELD_CLASSIFICATION: dict[str, str] = {
    # tool/action select and shape what a step dispatches to; action's
    # per-step stop semantics reach the orchestrating agent verbatim via
    # _api.py's orchestration_rules payload.
    "action": "execution",
    # Lint-rule-only YAML routing hint (rules_bypass.py et al.); no runtime
    # composition/dispatch consumer.
    "block": "validation-only",
    # capture/capture_list are read by validator.py and the dataflow
    # analysis detectors (lint rules) — no execution-time consumer found.
    "capture": "validation-only",
    "capture_list": "validation-only",
    # Discriminates a constant-value step at compile time; excluded from
    # bind_step_invocation's compiler (only tool-having steps compile), so
    # its only consumers are schema/lint validation.
    "constant": "validation-only",
    # The compile-time binding input record — bind_step_invocation reads it
    # directly; the real dispatch call site (server/_recipe_execution.py)
    # sets it explicitly on synthetic bound steps.
    "declared_with_args": "execution",
    # #4498 — zero consumer outside schema/parse code.
    "description": "inert-tracked:#4498",
    # Decides sub-recipe merge vs. drop in _recipe_composition.py.
    "gate": "composition",
    # Server-side RecipeStep fallback (tools_execution.py) — the #2969/#3377
    # pattern.
    "idle_output_timeout": "execution",
    # Embedded verbatim into the runtime orchestration_rules/
    # stop_step_semantics payload served to the orchestrating agent
    # (_api.py:_build_stop_step_semantics).
    "message": "execution",
    # Server-side RecipeStep fallback — the one EXECUTION_TUNING param this
    # plan newly wires (previously parsed and lint-validated but read by
    # zero runtime code).
    "model": "execution",
    # Read by rules_reachability.py/rules_blocks.py (lint rules) and the
    # analysis-block extraction chain, which is itself lint-only.
    "name": "validation-only",
    # Lint-rule-only (rules_bypass.py, rules_note_shape_contradiction.py, …).
    "note": "validation-only",
    # Routing field — the pipeline composer reads it to build the flow
    # graph and derive pipeline dependencies before dispatch.
    "on_context_limit": "composition",
    "on_exhausted": "composition",
    "on_failure": "composition",
    "on_rate_limit": "composition",
    "on_result": "composition",
    "on_success": "composition",
    # Schema/lint-only guard field.
    "optional": "validation-only",
    "optional_context_refs": "validation-only",
    "pass_through": "validation-only",
    "phoropter_family": "validation-only",
    # step_provider's server-side RecipeStep fallback (pre-gate, profile
    # resolution) — tools_execution.py.
    "provider": "execution",
    # Discriminates a python-callable step; same "excluded from the
    # compiler, lint/schema-only consumers" shape as constant.
    "python": "validation-only",
    # Validated by schema/validator only — the retry loop itself is
    # agent-prompt-driven (cli/_prompts_orchestrator.py generic text), not a
    # per-step Python read site.
    "retries": "validation-only",
    # Full execution chain: pipeline composition pruning
    # (_recipe_composition.py) and lock enforcement (tools_kitchen.py).
    "skip_when_false": "composition",
    # #4497 — zero runtime skip effect (unlike its sibling skip_when_false).
    "skip_when_true": "inert-tracked:#4497",
    # Server-side RecipeStep fallback — the #2969/#3377 pattern this plan
    # extends to model.
    "stale_threshold": "execution",
    # _recipe_composition.py's _build_active_recipe loads/merges the
    # sub-recipe or drops the placeholder before dispatch.
    "sub_recipe": "composition",
    # Selects the ToolDef that compiles the actual dispatch
    # (bind_step_invocation); read at the real dispatch call site too.
    "tool": "execution",
    # Server-side RecipeStep fallback reads with_args directly
    # (tools_execution.py's output_dir resolution) in addition to compiling
    # through bind_step_invocation.
    "with_args": "execution",
}


def _live_recipe_step_field_names() -> set[str]:
    return {field.name for field in dataclass_fields(RecipeStep)}


def test_ledger_is_sorted() -> None:
    keys = list(RECIPE_STEP_FIELD_CLASSIFICATION)
    assert keys == sorted(keys), (
        "RECIPE_STEP_FIELD_CLASSIFICATION keys must stay sorted so a diff always "
        "isolates exactly the entry that changed."
    )


def test_no_silent_field_additions() -> None:
    """Every live RecipeStep field must have a conscious ledger classification."""
    missing = sorted(_live_recipe_step_field_names() - set(RECIPE_STEP_FIELD_CLASSIFICATION))
    assert not missing, (
        f"RecipeStep has field(s) not present in RECIPE_STEP_FIELD_CLASSIFICATION: "
        f"{missing}. Classify each as execution / composition / validation-only / "
        "inert-tracked:#NNNN — file a tracking issue first if it has no consumer yet."
    )


def test_no_silent_field_removals() -> None:
    """Every ledger entry must name a live RecipeStep field."""
    stale = sorted(set(RECIPE_STEP_FIELD_CLASSIFICATION) - _live_recipe_step_field_names())
    assert not stale, (
        f"RECIPE_STEP_FIELD_CLASSIFICATION has entries for field(s) RecipeStep no "
        f"longer declares: {stale}. Remove these entries."
    )


def test_inert_tracked_entries_cite_a_live_issue() -> None:
    """An inert-tracked entry without a live issue reference is invisible at review time."""
    malformed = sorted(
        f"{name}={value!r}"
        for name, value in RECIPE_STEP_FIELD_CLASSIFICATION.items()
        if value.startswith("inert-tracked") and not _INERT_TRACKED_RE.match(value)
    )
    assert not malformed, (
        f"inert-tracked entries must match 'inert-tracked:#NNNN' exactly: {malformed}"
    )


def test_classifications_are_known_values() -> None:
    unknown = sorted(
        f"{name}={value!r}"
        for name, value in RECIPE_STEP_FIELD_CLASSIFICATION.items()
        if value not in _KNOWN_CLASSIFICATIONS and not _INERT_TRACKED_RE.match(value)
    )
    assert not unknown, f"unrecognized classification value(s): {unknown}"
