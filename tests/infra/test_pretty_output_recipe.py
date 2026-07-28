"""Tests: pretty_output token/timing, load_recipe, list_recipes, open_kitchen, deduplication."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from autoskillit.recipe._recipe_ingredients import OpenKitchenResult

from autoskillit.core import (
    RECIPE_FLOW_SCHEMA_VERSION,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST,
    RecipeFlowGeneration,
)
from autoskillit.execution import CODEX_RECIPE_DELIVERY_BUDGET
from autoskillit.hooks.formatters.pretty_output_hook import _format_response
from autoskillit.server._recipe_delivery import _attested_render, persist_recipe_artifact
from tests.infra._pretty_output_helpers import (
    REALISTIC_RECIPE_YAML,
    _make_event,
    _run_hook,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def _simple_flow_generation(name: str) -> RecipeFlowGeneration:
    records = (
        json.dumps(
            {"kind": "entrypoint", "name": name},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        json.dumps(
            {"index": 0, "kind": "step", "name": name},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
    return RecipeFlowGeneration(
        schema_version=RECIPE_FLOW_SCHEMA_VERSION,
        records=records,
    )


def test_response_backstop_tool_metadata_comes_from_registry() -> None:
    from autoskillit.server.tools._serve_helpers import response_backstop_tool_meta

    for tool_name in ("load_recipe", "open_kitchen"):
        definition = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY[tool_name]
        metadata = response_backstop_tool_meta(tool_name, always_load=tool_name == "open_kitchen")
        assert metadata["anthropic/maxResultSizeChars"] == definition.max_chars
        assert metadata["autoskillit/responseBackstopMeasurement"] == definition.measurement_id
        assert metadata["autoskillit/responseBackstopMaxUtf8Bytes"] == definition.max_utf8_bytes
        assert (
            metadata["autoskillit/responseBackstopRegistryDigest"]
            == RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST
        )
        assert metadata.get("anthropic/alwaysLoad", False) is (tool_name == "open_kitchen")


# PHK-15
def test_format_get_token_summary_compact():
    """get_token_summary must show compact per-step lines and totals."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_token_summary",
        "tool_response": json.dumps(
            {
                "steps": [
                    {
                        "step_name": "investigate",
                        "invocation_count": 1,
                        "input_tokens": 45200,
                        "output_tokens": 12800,
                        "cache_read_tokens": 1200000,
                        "cache_write_tokens": 0,
                    },
                    {
                        "step_name": "make_plan",
                        "invocation_count": 2,
                        "input_tokens": 30000,
                        "output_tokens": 8000,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 500000,
                    },
                    {
                        "step_name": "implement",
                        "invocation_count": 1,
                        "input_tokens": 60000,
                        "output_tokens": 15000,
                        "cache_read_tokens": 2000000,
                        "cache_write_tokens": 0,
                    },
                ],
                "total": {
                    "input_tokens": 135200,
                    "output_tokens": 35800,
                    "cache_read_tokens": 3200000,
                    "cache_write_tokens": 500000,
                },
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "token_summary" in text
    assert "investigate x1 [uc:45.2k out:12.8k cr:1.2M pk:0 cw:0 turns:0 t:0.0s]" in text
    assert "make_plan x2 [uc:30.0k out:8.0k cr:0 pk:0 cw:500.0k turns:0 t:0.0s]" in text
    assert "implement x1 [uc:60.0k out:15.0k cr:2.0M pk:0 cw:0 turns:0 t:0.0s]" in text
    assert "total_uncached:" in text
    assert "total_out:" in text
    assert "total_cache_read:" in text
    assert "total_peak_context:" in text
    assert "total_cache_write:" in text


# T7
def test_fmt_get_token_summary_prefers_wall_clock_seconds():
    """_fmt_get_token_summary prefers wall_clock_seconds over elapsed_seconds."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_token_summary",
        "tool_response": json.dumps(
            {
                "steps": [
                    {
                        "step_name": "implement",
                        "input_tokens": 5000,
                        "output_tokens": 1200,
                        "cache_write_tokens": 200,
                        "cache_read_tokens": 3000,
                        "invocation_count": 2,
                        "wall_clock_seconds": 150.0,
                        "elapsed_seconds": 123.4,
                    }
                ],
                "total": {
                    "input_tokens": 5000,
                    "output_tokens": 1200,
                    "cache_write_tokens": 200,
                    "cache_read_tokens": 3000,
                    "total_elapsed_seconds": 123.4,
                },
                "mcp_responses": {"steps": [], "total": {}},
            }
        ),
    }
    out, _ = _run_hook(event=event)
    data = json.loads(out)
    rendered = data["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "implement" in rendered
    assert "t:150.0s" in rendered


# T7b
def test_fmt_get_token_summary_falls_back_to_elapsed():
    """_fmt_get_token_summary falls back to elapsed_seconds when no wall_clock."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_token_summary",
        "tool_response": json.dumps(
            {
                "steps": [
                    {
                        "step_name": "plan",
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_write_tokens": 0,
                        "cache_read_tokens": 0,
                        "invocation_count": 1,
                        "elapsed_seconds": 42.5,
                    }
                ],
                "total": {"input_tokens": 100, "output_tokens": 50},
                "mcp_responses": {"steps": [], "total": {}},
            }
        ),
    }
    out, _ = _run_hook(event=event)
    data = json.loads(out)
    rendered = data["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "t:42.5s" in rendered


def test_hook_token_summary_output_equivalent_to_canonical():
    """1g: Hook inline _fmt_get_token_summary produces identical output to
    TelemetryFormatter.format_compact_kv for the same input data."""
    from autoskillit.hooks.formatters.pretty_output_hook import _fmt_get_token_summary
    from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter

    data = {
        "steps": [
            {
                "step_name": "investigate",
                "input_tokens": 7000,
                "output_tokens": 5939,
                "cache_write_tokens": 8495,
                "cache_read_tokens": 252179,
                "invocation_count": 1,
                "wall_clock_seconds": 45.0,
                "elapsed_seconds": 40.0,
            },
            {
                "step_name": "implement",
                "input_tokens": 2031000,
                "output_tokens": 122306,
                "cache_write_tokens": 280601,
                "cache_read_tokens": 19071323,
                "invocation_count": 3,
                "wall_clock_seconds": 492.0,
                "elapsed_seconds": 480.0,
            },
        ],
        "total": {
            "input_tokens": 2038000,
            "output_tokens": 128245,
            "cache_write_tokens": 289096,
            "cache_read_tokens": 19323502,
            "total_elapsed_seconds": 537.0,
        },
        "mcp_responses": {
            "steps": [],
            "total": {"total_invocations": 42, "total_estimated_response_tokens": 5000},
        },
    }

    hook_output = _fmt_get_token_summary(data, _pipeline=False)
    canonical_output = TelemetryFormatter.format_compact_kv(
        data["steps"], data["total"], mcp_responses=data["mcp_responses"]
    )
    assert hook_output == canonical_output, (
        f"Hook and canonical formatter produce different output:\n"
        f"HOOK:\n{hook_output}\n\nCANONICAL:\n{canonical_output}"
    )


def test_hook_token_summary_non_anthropic_equivalent_to_canonical():
    """1g-ext: Hook and canonical produce identical * annotation and footnote."""
    from autoskillit.hooks.formatters.pretty_output_hook import _fmt_get_token_summary
    from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter

    data = {
        "steps": [
            {
                "step_name": "plan",
                "model": "claude-sonnet-4-6",
                "input_tokens": 7000,
                "output_tokens": 5939,
                "cache_write_tokens": 8495,
                "cache_read_tokens": 252179,
                "invocation_count": 1,
                "wall_clock_seconds": 45.0,
                "elapsed_seconds": 40.0,
            },
            {
                "step_name": "implement",
                "model": "MiniMax-M2.7-highspeed",
                "input_tokens": 2031000,
                "output_tokens": 122306,
                "cache_write_tokens": 280601,
                "cache_read_tokens": 19071323,
                "invocation_count": 3,
                "wall_clock_seconds": 492.0,
                "elapsed_seconds": 480.0,
            },
        ],
        "total": {
            "input_tokens": 2038000,
            "output_tokens": 128245,
            "cache_write_tokens": 289096,
            "cache_read_tokens": 19323502,
            "total_elapsed_seconds": 537.0,
        },
        "mcp_responses": {
            "total": {"total_invocations": 42, "total_estimated_response_tokens": 5000}
        },
    }
    hook_output = _fmt_get_token_summary(data, _pipeline=False)
    canonical_output = TelemetryFormatter.format_compact_kv(
        data["steps"], data["total"], mcp_responses=data["mcp_responses"]
    )
    assert hook_output == canonical_output


def test_fmt_run_skill_interactive_shows_four_token_fields():
    """_fmt_run_skill interactive mode shows all 4 token fields."""
    data = {
        "success": True,
        "subtype": "COMPLETED",
        "exit_code": 0,
        "needs_retry": False,
        "result": "done",
        "token_usage": {
            "input_tokens": 5000,
            "output_tokens": 3000,
            "cache_read_tokens": 200000,
            "cache_write_tokens": 8000,
        },
    }
    rendered = _format_response(
        "mcp__plugin_autoskillit_autoskillit__run_skill",
        json.dumps({"result": json.dumps(data)}),
        pipeline=False,
    )
    assert rendered is not None
    assert "tokens_uncached:" in rendered
    assert "tokens_out:" in rendered
    assert "tokens_cache_read:" in rendered
    assert "tokens_cache_write:" in rendered


def test_fmt_run_skill_suppresses_zero_cache_fields():
    """_fmt_run_skill suppresses tokens_cache_read and tokens_cache_write when both are 0."""
    data = {
        "success": True,
        "subtype": "COMPLETED",
        "exit_code": 0,
        "needs_retry": False,
        "result": "done",
        "token_usage": {
            "input_tokens": 5000,
            "output_tokens": 3000,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        },
    }
    rendered = _format_response(
        "mcp__plugin_autoskillit_autoskillit__run_skill",
        json.dumps({"result": json.dumps(data)}),
        pipeline=False,
    )
    assert rendered is not None
    assert "tokens_uncached:" in rendered
    assert "tokens_out:" in rendered
    assert "tokens_cache_read:" not in rendered
    assert "tokens_cache_write:" not in rendered


def test_fmt_get_timing_summary_renders_compact():
    """get_timing_summary dedicated formatter renders compact Markdown-KV."""
    event = {
        "tool_name": "mcp__autoskillit__get_timing_summary",
        "tool_response": json.dumps(
            {
                "steps": [
                    {"step_name": "clone", "total_seconds": 4.0, "invocation_count": 1},
                    {"step_name": "implement", "total_seconds": 492.0, "invocation_count": 3},
                ],
                "total": {"total_seconds": 496.0},
            }
        ),
    }
    out, _ = _run_hook(event=event)
    data = json.loads(out)
    rendered = data["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "## timing_summary" in rendered
    assert "clone x1" in rendered
    assert "implement x3" in rendered
    assert "dur:4s" in rendered
    assert "dur:8m 12s" in rendered
    assert "total:" in rendered


def test_get_token_summary_format_table_passes_through_unmodified(tmp_path):
    """get_token_summary(format='table') returns pre-formatted markdown; hook passes it through."""
    table = (
        "## Token Usage Summary\n\n| Step | input | output |\n|---|---|---|\n| impl | 45k | 12k |"
    )
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_token_summary",
        "tool_response": json.dumps({"result": table}),
    }
    out, code = _run_hook(event=event, cwd=tmp_path)
    assert code == 0
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "## Token Usage Summary" in text
    assert "| impl |" in text
    assert text.strip() != "## token_summary"


def test_get_timing_summary_format_table_passes_through_unmodified(tmp_path):
    """get_timing_summary(format='table') returns pre-formatted markdown; hook passes through."""
    table = (
        "## Step Timing Summary\n\n"
        "| Step | Duration | Invocations |\n|---|---|---|\n| clone | 4s | 1 |"
    )
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_timing_summary",
        "tool_response": json.dumps({"result": table}),
    }
    out, code = _run_hook(event=event, cwd=tmp_path)
    assert code == 0
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "## Step Timing Summary" in text
    assert "| clone |" in text
    assert text.strip() != "## timing_summary"


# PHK-50: open_kitchen combined formatter
def test_fmt_open_kitchen_combined_response():
    """open_kitchen with recipe returns combined kitchen+recipe format."""
    payload = {
        "content": "name: my-recipe\nsteps:\n  do:\n    tool: run_cmd\n",
        "valid": True,
        "suggestions": [],
        "kitchen": "open",
        "version": "1.2.3",
    }
    formatted = _format_response(
        "mcp__autoskillit__open_kitchen",
        json.dumps(payload),
        pipeline=False,
    )
    assert formatted is not None
    assert "open_kitchen" in formatted
    assert "v1.2.3" in formatted
    assert "--- RECIPE ---" in formatted
    assert "run_cmd" in formatted


def test_fmt_open_kitchen_combined_includes_ingredients_table():
    """Combined open_kitchen response includes pre-formatted ingredients table."""
    payload = {
        "content": "name: my-recipe\nsteps: ...",
        "valid": True,
        "suggestions": [],
        "kitchen": "open",
        "version": "1.0.0",
        "ingredients_table": "  Name  Description  Default\n  task  The task     (required)",
    }
    formatted = _format_response(
        "mcp__autoskillit__open_kitchen",
        json.dumps(payload),
        pipeline=False,
    )
    assert formatted is not None
    assert "--- INGREDIENTS TABLE" in formatted
    assert "The task" in formatted


def test_fmt_open_kitchen_combined_error():
    """Combined open_kitchen with recipe error shows error with kitchen status."""
    payload = {
        "error": "No recipe named 'bad' found",
        "kitchen": "open",
        "version": "1.0.0",
    }
    formatted = _format_response(
        "mcp__autoskillit__open_kitchen",
        json.dumps(payload),
        pipeline=False,
    )
    assert formatted is not None
    assert "open_kitchen" in formatted
    assert "No recipe named 'bad' found" in formatted
    assert "\u2717" in formatted


def test_fmt_open_kitchen_plain_text():
    """open_kitchen without recipe returns plain text format."""
    formatted = _format_response(
        "mcp__autoskillit__open_kitchen",
        json.dumps({"result": "Kitchen is open. AutoSkillit 1.2.3."}),
        pipeline=False,
    )
    assert formatted is not None
    assert "open_kitchen" in formatted
    assert "Kitchen is open. AutoSkillit 1.2.3." in formatted


@pytest.mark.parametrize("tool_name", ["open_kitchen", "load_recipe"])
def test_attested_recipe_delivery_region_is_preserved_byte_for_byte(
    tmp_path: Path, tool_name: str
) -> None:
    payload = {
        "success": True,
        "content": "steps:\n  impl:\n    action: stop\n",
        "post_prune_step_names": ["impl"],
        "ingredients_table": None,
        "orchestration_rules": "follow the graph",
        "stop_step_semantics": "stop means stop",
        "errors": [],
        "warnings": [],
    }
    flow_generation = _simple_flow_generation("impl")
    payload["flow_records"] = list(flow_generation.records)
    payload["recipe_flow"] = flow_generation.identity()
    generation = persist_recipe_artifact(
        tmp_path,
        kitchen_id="formatter-contract",
        producer_tool=tool_name,
        recipe_name="remediation",
        payload=payload,
        flow_generation=flow_generation,
    )
    protected = _attested_render(
        payload,
        generation,
        budget=CODEX_RECIPE_DELIVERY_BUDGET,
        evidence_identity="formatter-contract-v1",
    )
    wrapped = json.dumps({"result": protected})
    assert _format_response(f"mcp__autoskillit__{tool_name}", wrapped, False) == protected


# PHK-42/43/44: _fmt_load_recipe tests


def test_fmt_load_recipe_includes_diagram():
    """Diagram is rendered — the step-flow chain must survive preview truncation."""
    formatted = _format_response(
        "mcp__autoskillit__load_recipe",
        json.dumps(
            {
                "content": "name: my-recipe\nsteps: ...",
                "diagram": "## my-recipe\nsome diagram graph",
                "valid": True,
                "suggestions": [],
            }
        ),
        pipeline=False,
    )
    assert formatted is not None
    assert "some diagram graph" in formatted
    assert "--- RECIPE ---" in formatted


def test_fmt_load_recipe_includes_raw_yaml_content():
    """load_recipe response includes the raw YAML so the agent can execute steps."""
    yaml_content = "name: my-recipe\nsteps:\n  do_thing:\n    tool: run_cmd\n"
    formatted = _format_response(
        "mcp__autoskillit__load_recipe",
        json.dumps(
            {
                "content": yaml_content,
                "diagram": "## my-recipe\nsome diagram",
                "valid": True,
                "suggestions": [],
            }
        ),
        pipeline=False,
    )
    assert formatted is not None
    assert "do_thing" in formatted
    assert "--- RECIPE ---" in formatted


def test_fmt_load_recipe_shows_finding_count():
    """Findings are summarized as a count, not individual bullets."""
    formatted = _format_response(
        "mcp__autoskillit__load_recipe",
        json.dumps(
            {
                "content": "name: x",
                "diagram": "## x",
                "valid": False,
                "suggestions": [
                    {
                        "rule": "missing-step",
                        "message": "Step 'done' not found",
                        "severity": "error",
                    },
                    {"rule": "unknown-tool", "message": "Tool 'badtool'", "severity": "warning"},
                ],
            }
        ),
        pipeline=False,
    )
    assert formatted is not None
    assert "2 finding(s)" in formatted


# PHK-45/46: _fmt_list_recipes tests


def test_fmt_list_recipes_shows_all_names():
    """PHK-45: list_recipes response renders all recipe names."""
    recipes = [
        {"name": f"recipe-{i:02d}", "description": f"Description for recipe {i}", "summary": "..."}
        for i in range(10)
    ]
    event = _make_event("list_recipes", {"recipes": recipes, "count": 10})
    out, _ = _run_hook(event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    for i in range(10):
        assert f"recipe-{i:02d}" in text, f"recipe-{i:02d} missing from output"
    assert '{"name"' not in text
    assert "10" in text


def test_fmt_list_recipes_compact_representation():
    """PHK-46: list_recipes renders one line per recipe in 'name: description' format."""
    event = _make_event(
        "list_recipes",
        {
            "recipes": [
                {
                    "name": "implementation",
                    "description": "Implement a plan in a worktree",
                    "summary": "...",
                },
                {
                    "name": "smoke-test",
                    "description": "Run a smoke-test pipeline",
                    "summary": "...",
                },
            ],
            "count": 2,
        },
    )
    out, _ = _run_hook(event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "implementation" in text
    assert "Implement a plan in a worktree" in text
    assert "smoke-test" in text
    assert "Run a smoke-test pipeline" in text


def test_fmt_load_recipe_field_coverage():
    """Every LoadRecipeResult field must be in RENDERED or SUPPRESSED."""
    from autoskillit.hooks.formatters.pretty_output_hook import (
        _FMT_LOAD_RECIPE_RENDERED,
        _FMT_LOAD_RECIPE_SUPPRESSED,
    )
    from autoskillit.recipe._api import LoadRecipeResult

    all_fields = set(LoadRecipeResult.__annotations__)
    covered = _FMT_LOAD_RECIPE_RENDERED | _FMT_LOAD_RECIPE_SUPPRESSED
    uncovered = all_fields - covered
    assert uncovered == set(), (
        f"LoadRecipeResult fields have no coverage decision: {sorted(uncovered)}. "
        "Add each to _FMT_LOAD_RECIPE_RENDERED or _FMT_LOAD_RECIPE_SUPPRESSED."
    )
    extra = covered - all_fields
    assert extra == set(), (
        f"Coverage registry references non-existent fields: {sorted(extra)}. Remove stale entries."
    )


def test_fmt_load_recipe_derivation_map_coverage():
    """Every key/value in _LOAD_RECIPE_CONTENT_DERIVED_FROM must be in _FMT_LOAD_RECIPE_RENDERED."""  # noqa: E501
    from autoskillit.hooks.formatters.pretty_output_hook import (
        _FMT_LOAD_RECIPE_RENDERED,
        _LOAD_RECIPE_CONTENT_DERIVED_FROM,
    )

    for derived_field, source_field in _LOAD_RECIPE_CONTENT_DERIVED_FROM.items():
        assert derived_field in _FMT_LOAD_RECIPE_RENDERED, (
            f"Derived field '{derived_field}' must be in _FMT_LOAD_RECIPE_RENDERED."
        )
        assert source_field in _FMT_LOAD_RECIPE_RENDERED, (
            f"Source field '{source_field}' must be in _FMT_LOAD_RECIPE_RENDERED."
        )


def test_fmt_load_recipe_renders_error():
    """When error is present, it appears in output with cross mark."""
    formatted = _format_response(
        "mcp__autoskillit__load_recipe",
        json.dumps({"error": "Recipe 'x' not found", "valid": False}),
        pipeline=False,
    )
    assert formatted is not None
    assert "\u2717" in formatted
    assert "Recipe 'x' not found" in formatted


def test_fmt_load_recipe_suppresses_kitchen_rules():
    """Kitchen rules are suppressed — they're in the YAML content."""
    formatted = _format_response(
        "mcp__autoskillit__load_recipe",
        json.dumps(
            {
                "valid": True,
                "diagram": "## test diagram",
                "suggestions": [],
                "kitchen_rules": ["no raw SQL", "use run_cmd for shell"],
            }
        ),
        pipeline=False,
    )
    assert formatted is not None
    assert "no raw SQL" not in formatted


def test_fmt_load_recipe_suppresses_greeting():
    """Greeting is suppressed — delivered via positional CLI arg instead."""
    formatted = _format_response(
        "mcp__autoskillit__load_recipe",
        json.dumps(
            {
                "valid": True,
                "diagram": "## test diagram",
                "suggestions": [],
                "greeting": "Welcome to Good Burger!",
            }
        ),
        pipeline=False,
    )
    assert formatted is not None
    assert "Welcome to Good Burger!" not in formatted


def test_fmt_list_recipes_field_coverage():
    """Every ListRecipesResult field must be in RENDERED or SUPPRESSED."""
    from autoskillit.hooks.formatters.pretty_output_hook import (
        _FMT_LIST_RECIPES_RENDERED,
        _FMT_LIST_RECIPES_SUPPRESSED,
    )
    from autoskillit.recipe._api import ListRecipesResult

    all_fields = set(ListRecipesResult.__annotations__)
    covered = _FMT_LIST_RECIPES_RENDERED | _FMT_LIST_RECIPES_SUPPRESSED
    uncovered = all_fields - covered
    assert uncovered == set(), (
        f"ListRecipesResult fields have no coverage decision: {sorted(uncovered)}."
    )
    extra = covered - all_fields
    assert extra == set(), (
        f"Coverage registry references non-existent fields: {sorted(extra)}. Remove stale entries."
    )


def test_fmt_recipe_list_item_field_coverage():
    """Every RecipeListItem field must be in RENDERED or SUPPRESSED."""
    from autoskillit.hooks.formatters.pretty_output_hook import (
        _FMT_RECIPE_LIST_ITEM_RENDERED,
        _FMT_RECIPE_LIST_ITEM_SUPPRESSED,
    )
    from autoskillit.recipe._api import RecipeListItem

    all_fields = set(RecipeListItem.__annotations__)
    covered = _FMT_RECIPE_LIST_ITEM_RENDERED | _FMT_RECIPE_LIST_ITEM_SUPPRESSED
    uncovered = all_fields - covered
    assert uncovered == set(), (
        f"RecipeListItem fields have no coverage decision: {sorted(uncovered)}."
    )
    extra = covered - all_fields
    assert extra == set(), (
        f"Coverage registry references non-existent fields: {sorted(extra)}. Remove stale entries."
    )


def test_fmt_open_kitchen_field_coverage():
    """Every OpenKitchenResult field must be in RENDERED or SUPPRESSED."""
    import typing

    from autoskillit.hooks.formatters.pretty_output_hook import (
        _FMT_OPEN_KITCHEN_RENDERED,
        _FMT_OPEN_KITCHEN_SUPPRESSED,
    )
    from autoskillit.recipe._recipe_ingredients import OpenKitchenResult

    all_fields = set(typing.get_type_hints(OpenKitchenResult))
    covered = _FMT_OPEN_KITCHEN_RENDERED | _FMT_OPEN_KITCHEN_SUPPRESSED
    uncovered = all_fields - covered
    assert uncovered == set(), (
        f"OpenKitchenResult fields have no coverage decision: {sorted(uncovered)}. "
        "Add each to _FMT_OPEN_KITCHEN_RENDERED or _FMT_OPEN_KITCHEN_SUPPRESSED."
    )
    extra = covered - all_fields
    assert extra == set(), (
        f"Coverage registry references non-existent fields: {sorted(extra)}. Remove stale entries."
    )


def test_fmt_list_recipes_renders_summary():
    """Recipe summary appears on the line below each recipe name."""
    formatted = _format_response(
        "mcp__autoskillit__list_recipes",
        json.dumps(
            {
                "recipes": [
                    {"name": "test-recipe", "description": "A test", "summary": "step1 -> done"},
                ],
                "count": 1,
            }
        ),
        pipeline=False,
    )
    assert formatted is not None
    assert "test-recipe" in formatted
    assert "step1 -> done" in formatted


# Raw/derived field deduplication: ingredients_table vs content


def test_fmt_recipe_body_ingredients_not_duplicated_when_table_present():
    """When ingredients_table is present, ingredient names must not appear in RECIPE section."""
    from autoskillit.hooks.formatters.pretty_output_hook import _fmt_recipe_body

    data = {
        "content": REALISTIC_RECIPE_YAML,
        "ingredients_table": (
            "| Name | Description | Default |\n"
            "| task | What to implement | (required) |\n"
            "| review_approach | Run review-approach before planning | false |\n"
        ),
        "valid": True,
        "suggestions": [],
    }
    result = "\n".join(_fmt_recipe_body(data))
    assert "--- INGREDIENTS TABLE" in result, (
        "_fmt_recipe_body did not emit the INGREDIENTS TABLE header."
    )
    recipe_section = result.split("--- INGREDIENTS TABLE")[0]
    assert "review_approach:" not in recipe_section
    assert "  task:" not in recipe_section
    assert "implement" in recipe_section
    assert "kitchen_rules" in recipe_section
    table_section = result.split("--- INGREDIENTS TABLE")[1]
    assert "review_approach" in table_section
    assert "task" in table_section


def test_strip_yaml_ingredients_block_removes_ingredients_section():
    from autoskillit.hooks.formatters.pretty_output_hook import _strip_yaml_ingredients_block

    yaml = "name: test\ningredients:\n  task:\n    description: a task\nsteps:\n  do: {}\n"
    result = _strip_yaml_ingredients_block(yaml)
    assert "ingredients:" not in result
    assert "  task:" not in result
    assert "steps:" in result
    assert "name: test" in result


def test_strip_yaml_ingredients_block_noop_when_no_ingredients_key():
    from autoskillit.hooks.formatters.pretty_output_hook import _strip_yaml_ingredients_block

    yaml = "name: test\nsteps:\n  do: {}\n"
    result = _strip_yaml_ingredients_block(yaml)
    assert result == yaml


def test_strip_yaml_ingredients_block_at_end_of_file():
    from autoskillit.hooks.formatters.pretty_output_hook import _strip_yaml_ingredients_block

    yaml = "name: test\nsteps:\n  do: {}\ningredients:\n  foo:\n    description: bar\n"
    result = _strip_yaml_ingredients_block(yaml)
    assert "ingredients:" not in result
    assert "steps:" in result


def test_strip_yaml_ingredients_block_multiline_description():
    from autoskillit.hooks.formatters.pretty_output_hook import _strip_yaml_ingredients_block

    yaml = (
        "name: test\n"
        "ingredients:\n"
        "  task:\n"
        "    description: >\n"
        "      Long description\n"
        "      spanning multiple lines\n"
        "    required: true\n"
        "steps:\n"
        "  do: {}\n"
    )
    result = _strip_yaml_ingredients_block(yaml)
    assert "ingredients:" not in result
    assert "steps:" in result


def test_fmt_open_kitchen_ingredients_not_duplicated_when_table_present():
    """open_kitchen routes through _fmt_recipe_body — verify same deduplication applies."""
    from autoskillit.hooks.formatters.pretty_output_hook import _fmt_open_kitchen

    data = {
        "content": REALISTIC_RECIPE_YAML,
        "ingredients_table": "| task | What to implement | (required) |",
        "valid": True,
        "suggestions": [],
        "kitchen": "open",
        "version": "0.6.0",
    }
    result = _fmt_open_kitchen(data, pipeline=False)
    assert "--- INGREDIENTS TABLE" in result, (
        "_fmt_open_kitchen did not emit the INGREDIENTS TABLE header."
    )
    recipe_section = result.split("--- INGREDIENTS TABLE")[0]
    assert "  task:" not in recipe_section
    assert "review_approach:" not in recipe_section


def test_fmt_open_kitchen_ingredients_only_no_recipe_block():
    """Formatter must not render RECIPE block when content is absent (ingredients_only)."""
    from autoskillit.hooks.formatters.pretty_output_hook import _fmt_open_kitchen

    data = {
        "success": True,
        "kitchen": "open",
        "version": "0.9.372",
        "valid": True,
        "ingredients_table": "| Name | Description | Default |",
        "suggestions": [],
    }
    output = _fmt_open_kitchen(data, pipeline=False)
    assert "--- RECIPE ---" not in output


# Issue #4253 Part A: STEP FLOW head, diagram inclusion, compaction, and payload budget


def test_open_kitchen_output_leads_with_step_flow():
    """STEP FLOW must be the first section, before FLOW DIAGRAM and RECIPE, and must
    survive any truncation to the first 2KB of the response."""
    from autoskillit.hooks.formatters.pretty_output_hook import _fmt_open_kitchen

    data = {
        "summary": "first > second",
        "content": (
            "name: x\nsteps:\n  first:\n    tool: run_cmd\n    on_success: second\n"
            "  second:\n    action: stop\n"
        ),
        "diagram": "## x\nfirst --> second",
        "ingredients_table": "| Name | Description | Default |",
        "orchestration_rules": "STEP EXECUTION IS NOT DISCRETIONARY:\nsome rules",
        "valid": True,
        "suggestions": [],
        "version": "0.1.0",
    }
    formatted = _fmt_open_kitchen(data, pipeline=False)
    assert formatted.startswith("## open_kitchen")
    idx_flow = formatted.index("--- STEP FLOW ---")
    idx_diagram = formatted.index("--- FLOW DIAGRAM ---")
    idx_recipe = formatted.index("--- RECIPE ---")
    assert idx_flow < idx_diagram < idx_recipe

    head_bytes = formatted.encode("utf-8")[:2048]
    assert b"--- STEP FLOW ---" in head_bytes
    assert b"first > second" in head_bytes
    assert b"--- END STEP FLOW ---" in head_bytes


def test_open_kitchen_output_includes_diagram():
    from autoskillit.hooks.formatters.pretty_output_hook import _fmt_open_kitchen

    data = {
        "diagram": "## x\nfirst --> second diagram content",
        "valid": True,
        "suggestions": [],
        "version": "0.1.0",
    }
    formatted = _fmt_open_kitchen(data, pipeline=False)
    assert "first --> second diagram content" in formatted


def _strip_presentation_fields(parsed: dict) -> dict:
    """Expected projection: original parsed YAML minus presentation-only fields
    compact_recipe_display() is allowed to drop (top-level description/summary/name/
    recipe_version, direct step description)."""
    projected = dict(parsed)
    for key in ("description", "summary", "name", "recipe_version"):
        projected.pop(key, None)
    steps = projected.get("steps")
    if isinstance(steps, dict):
        new_steps = {}
        for name, step in steps.items():
            if isinstance(step, dict):
                step = {k: v for k, v in step.items() if k != "description"}
            new_steps[name] = step
        projected["steps"] = new_steps
    return projected


_COMPACT_TEST_OVERRIDES = {
    "default": {
        "task": "test task",
        "issue_url": "https://github.com/test/test/issues/1",
        "max_issues_per_l2": "6",
        "model_context_window": "200000",
    },
    "all_truthy": {
        "task": "test task",
        "issue_url": "https://github.com/test/test/issues/1",
        "is_fleet_dispatch": "true",
        "adversarial_review_level": "true",
        "local_review_rounds": "true",
        "max_issues_per_l2": "6",
        "model_context_window": "200000",
        "base_branch": "true",
        "pipeline_health": "true",
    },
}


def _served_content(
    recipe_name: str, project_root, overrides: dict, tmp_path, monkeypatch
) -> str | None:
    """Load a bundled recipe's served content in isolation from the shared cache."""
    from autoskillit.recipe import _api_cache, load_and_validate
    from autoskillit.recipe._api_cache import LoadCache

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
    resolved = dict(overrides, source_dir=str(project_root))
    result = load_and_validate(
        recipe_name,
        project_dir=project_root,
        ingredient_overrides=resolved,
        resolved_defaults=resolved,
        temp_dir=tmp_path,
    )
    return result.get("content")


def test_compact_recipe_display_preserves_execution_semantics(tmp_path, monkeypatch):
    """compact_recipe_display() must not alter any parsed value except the
    explicitly allowlisted presentation-only fields, for every runtime-discoverable
    recipe under both ingredient modes."""
    from autoskillit.core import load_yaml
    from autoskillit.hooks.formatters._fmt_recipe_compact import compact_recipe_display
    from autoskillit.recipe import all_validated_recipe_names

    project_root = Path(__file__).resolve().parent.parent.parent
    remediation_note_checked = False

    for recipe_name in all_validated_recipe_names(project_root):
        for mode_name, overrides in _COMPACT_TEST_OVERRIDES.items():
            content = _served_content(recipe_name, project_root, overrides, tmp_path, monkeypatch)
            if not content:
                continue
            original_parsed = load_yaml(content)
            if not isinstance(original_parsed, dict):
                continue
            compacted = compact_recipe_display(content)
            compacted_parsed = load_yaml(compacted)
            expected = _strip_presentation_fields(original_parsed)
            assert compacted_parsed == expected, (
                f"{recipe_name} ({mode_name}): compaction altered parsed recipe semantics"
            )
            if recipe_name == "remediation":
                remediation_note_checked = True
                rectify_note = original_parsed["steps"]["rectify"]["note"]
                assert "all_plan_paths accumulates" in rectify_note
                assert compacted_parsed["steps"]["rectify"]["note"] == rectify_note
                assert "all_plan_paths accumulates" in compacted

    assert remediation_note_checked, "remediation recipe was not exercised by this test"


def test_canonical_recipe_responses_fit_independent_registry_ceilings(tmp_path, monkeypatch):
    """Measure the same pre-backstop string in characters and UTF-8 bytes."""

    from autoskillit import __version__
    from autoskillit.recipe import _api_cache, all_validated_recipe_names, load_and_validate
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.recipe.io import _SCRIPTS_PLACEHOLDER, builtin_scripts_dir
    from autoskillit.recipe.repository import DefaultRecipeRepository
    from autoskillit.server._misc import strip_ingredients_only_keys
    from autoskillit.server.tools._serve_helpers import (
        build_open_kitchen_recipe_payload,
        render_served_response,
        serve_recipe,
    )

    project_root = Path(__file__).resolve().parent.parent.parent
    measured_modes: set[tuple[str, str, bool]] = set()
    maxima = {"load_recipe": (0, "", ""), "open_kitchen": (0, "", "")}
    monkeypatch.setattr(_api_cache, "_refresh_staleness_baseline", lambda: None)
    # Normalize away the environment-dependent scripts path so the pinned
    # maxima are identical regardless of checkout location.
    _scripts_dir = str(builtin_scripts_dir())

    for recipe_name in all_validated_recipe_names(project_root):
        for mode_name, overrides in _COMPACT_TEST_OVERRIDES.items():
            monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
            tool_ctx = SimpleNamespace(
                recipes=DefaultRecipeRepository(),
                project_dir=project_root,
                session_serve_overrides=None,
                session_serve_defer_unresolved=False,
            )
            resolved = dict(overrides, source_dir=str(project_root))
            preview = load_and_validate(
                recipe_name,
                project_dir=project_root,
                ingredient_overrides=resolved,
                resolved_defaults=resolved,
                temp_dir=tmp_path,
            )
            if not preview.get("post_prune_step_names"):
                continue
            monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
            result = serve_recipe(
                tool_ctx,
                recipe_name,
                caller_overrides=resolved,
                config_default=resolved,
                session_overrides=resolved,
                config_layer=resolved,
                resolved_defaults=resolved,
                temp_dir=tmp_path,
            )

            for ingredients_only in (False, True):
                for tool_name in ("load_recipe", "open_kitchen"):
                    payload = dict(result)
                    if tool_name == "open_kitchen":
                        payload = build_open_kitchen_recipe_payload(payload, version=__version__)
                    if ingredients_only:
                        payload = strip_ingredients_only_keys(payload)
                    raw = render_served_response(payload)
                    definition = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY[tool_name]
                    assert len(raw) <= definition.max_chars, (
                        tool_name,
                        recipe_name,
                        mode_name,
                        ingredients_only,
                        len(raw),
                    )
                    assert len(raw.encode("utf-8")) <= definition.max_utf8_bytes, (
                        tool_name,
                        recipe_name,
                        mode_name,
                        ingredients_only,
                        len(raw.encode("utf-8")),
                    )
                    normalized = raw.replace(_scripts_dir, _SCRIPTS_PLACEHOLDER)
                    maxima[tool_name] = max(
                        maxima[tool_name],
                        (len(normalized.encode("utf-8")), recipe_name, mode_name),
                    )
                    measured_modes.add((tool_name, mode_name, ingredients_only))

    assert measured_modes == {
        (tool_name, mode_name, ingredients_only)
        for tool_name in ("load_recipe", "open_kitchen")
        for mode_name in _COMPACT_TEST_OVERRIDES
        for ingredients_only in (False, True)
    }
    assert maxima == {
        "load_recipe": (168_523, "remediation", "all_truthy"),
        "open_kitchen": (168_576, "remediation", "all_truthy"),
    }


def test_rendered_open_kitchen_payload_under_budget(tmp_path, monkeypatch):
    """Fit-by-construction rendering gate (issue #4304 Part B REQ-B-T8): the rendered
    Markdown from ``open_kitchen`` for every runtime-discoverable recipe under every
    ingredient-resolution mode must stay at or under the measured exemption ceiling
    from ``RESPONSE_BACKSTOP_EXEMPTION_REGISTRY`` — because the input is bounded by
    construction via ``build_recipe_envelope`` whenever the raw payload would
    otherwise exceed the smallest registered backend's effective delivery bound.

    The previous iteration formatted the raw, un-enveloped payload and only passed
    today because no bundled recipe happens to exceed the ceiling yet; it provided
    no guarantee for a large recipe added later. This rewrite replaces oversized
    raw payloads with the bounded envelope (built via ``build_recipe_envelope``,
    same construction as the Part B envelope tests, including
    ``recipe_name=recipe_name``) before formatting through ``_fmt_open_kitchen``.
    Ceiling accommodates growth from issue #4274 Part B (the new
    ``inter_part_push_pre_remediation`` / ``verify_ref_push_exhaustion`` steps in
    ``remediation.yaml`` legitimately grew the rendered payload)."""
    from autoskillit.core import (
        RECIPE_FLOW_SCHEMA_VERSION,
        RecipeFlowGeneration,
        resolve_recipe_envelope_byte_limit,
    )
    from autoskillit.execution.backends import BACKEND_REGISTRY
    from autoskillit.hooks.formatters import _fmt_recipe_compact
    from autoskillit.hooks.formatters.pretty_output_hook import (
        _fmt_open_kitchen,
        _strip_yaml_ingredients_block,
    )
    from autoskillit.recipe import _api_cache, all_validated_recipe_names, load_and_validate
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server._recipe_delivery import build_recipe_envelope, persist_recipe_artifact

    project_root = Path(__file__).resolve().parent.parent.parent
    ceiling = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["open_kitchen"].max_utf8_bytes
    # Smallest backend's effective delivery bound across the registry — the
    # conservative ceiling a payload of arbitrary size must fit. Mirrors
    # The recipe envelope uses the strictest registered backend byte ceiling.
    # This mirrors the production resolver and
    # `test_capability_default_uses_conservative_bound` in this directory's
    # sibling test file. Computed inline because this test lives in tests/infra/
    # and does not have a full `ToolContext` fixture available; replicates the
    # fits/build-envelope decision directly rather than depending on
    # `maybe_envelope_recipe_response`, which receives a single active session's
    # bound instead of computing the registry minimum.
    backend_caps = {name: cls().capabilities for name, cls in BACKEND_REGISTRY.items()}
    smallest_bound_bytes = min(
        resolve_recipe_envelope_byte_limit(caps) for caps in backend_caps.values()
    )
    over_budget: list[str] = []
    maximum: tuple[int, str, str] = (0, "", "")

    for recipe_name in all_validated_recipe_names(project_root):
        for mode_name, overrides in _COMPACT_TEST_OVERRIDES.items():
            monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
            resolved = dict(overrides, source_dir=str(project_root))
            result = load_and_validate(
                recipe_name,
                project_dir=project_root,
                ingredient_overrides=resolved,
                resolved_defaults=resolved,
                temp_dir=tmp_path,
            )
            payload = dict(result)
            step_names = [
                name
                for name in payload.get("post_prune_step_names") or []
                if isinstance(name, str)
            ]
            if not step_names:
                continue
            flow_records = [
                json.dumps(
                    {"kind": "entrypoint", "name": step_names[0]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                *[
                    json.dumps(
                        {"index": index, "kind": "step", "name": name},
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    for index, name in enumerate(step_names)
                ],
            ]
            flow_generation = RecipeFlowGeneration(
                schema_version=RECIPE_FLOW_SCHEMA_VERSION,
                records=tuple(flow_records),
            )
            payload["flow_records"] = list(flow_generation.records)
            payload["recipe_flow"] = flow_generation.identity()
            generation = persist_recipe_artifact(
                tmp_path,
                kitchen_id="pretty-output",
                producer_tool="open_kitchen",
                recipe_name=recipe_name,
                payload=payload,
                flow_generation=flow_generation,
            )
            envelope = build_recipe_envelope(
                payload,
                recipe_name=recipe_name,
                generation=generation,
                flow_generation=flow_generation,
                entrypoint=step_names[0],
                bound_bytes=smallest_bound_bytes,
            )
            rendered = _fmt_open_kitchen(envelope, pipeline=False)
            byte_len = len(rendered.encode("utf-8"))
            maximum = max(maximum, (byte_len, recipe_name, mode_name))
            if byte_len > ceiling:
                over_budget.append(f"{recipe_name} ({mode_name}): {byte_len} bytes > {ceiling}")

            # Issue #4399 content gate: when the ordinary-rendered payload fits within
            # the exemption ceiling, the formatter must receive the raw ORDINARY_INLINE
            # payload (containing `content`, `summary`, `diagram`) — not the stripped
            # envelope — so the rendered output retains the recipe body. The envelope
            # path strips `content`/`summary`/`diagram`, producing a degenerate
            # `## open_kitchen ✓ v{version}` with no recipe body. This assertion
            # fails before the fix because the test currently feeds the envelope
            # to `_fmt_open_kitchen` regardless of size.
            ordinary_payload_bytes = len(
                json.dumps(dict(result), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            if ordinary_payload_bytes <= ceiling:
                ordinary_rendered = _fmt_open_kitchen(
                    cast("OpenKitchenResult", dict(result)), pipeline=False
                )
                # Recipe YAML content must survive rendering for the exempt ORDINARY path.
                # _fmt_recipe_body applies compact_recipe_display (strips top-level
                # name/description/summary/recipe_version, step descriptions, then halves
                # structural indentation) — and additionally strips the YAML
                # ``ingredients:`` block when ``ingredients_table`` is present so the
                # INGREDIENTS TABLE block doesn't duplicate it. Mirror that projection
                # before substring-checking rather than comparing against raw bytes.
                raw_recipe_content = result.get("content") or ""
                assert raw_recipe_content, (
                    f"{recipe_name} ({mode_name}): recipe payload missing 'content' field"
                )
                display_content = raw_recipe_content
                if result.get("ingredients_table"):
                    display_content = _strip_yaml_ingredients_block(display_content)
                compacted_content = _fmt_recipe_compact.compact_recipe_display(display_content)
                assert compacted_content in ordinary_rendered, (
                    f"{recipe_name} ({mode_name}): rendered ORDINARY_INLINE output "
                    f"missing compacted recipe content "
                    f"(only {len(ordinary_rendered)} bytes rendered)"
                )

    max_bytes, max_recipe, max_mode = maximum
    assert not over_budget, (
        f"maximum rendered payload: {max_recipe} ({max_mode}) = {max_bytes} bytes\n"
        + "\n".join(over_budget)
    )
