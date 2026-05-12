"""Tests for campaign capture extraction and ingredient interpolation (Group J)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core.types import CaptureEntrySpec, resolve_payload_field

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _ce(from_: str, value_type: str = "string") -> CaptureEntrySpec:
    """Shorthand to build a CaptureEntrySpec in tests."""
    return CaptureEntrySpec(from_=from_, value_type=value_type)


# ---------------------------------------------------------------------------
# Capture extraction tests
# ---------------------------------------------------------------------------


def test_extract_captures_from_payload():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"sources_manifest": _ce("${{ result.sources_manifest }}")},
        {"sources_manifest": "/tmp/sources.json", "extra": "ignored"},
    )
    assert result == {"sources_manifest": "/tmp/sources.json"}


def test_extract_captures_all_fields_missing_raises():
    from autoskillit.fleet._api import CaptureCompletenessError, _extract_captures

    with pytest.raises(CaptureCompletenessError):
        _extract_captures(
            {"missing_key": _ce("${{ result.missing_key }}")},
            {"other": "value"},
        )


def test_extract_captures_non_result_template_skipped():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"a": _ce("plain_string"), "b": _ce("${{ inputs.x }}")},
        {"plain_string": "val", "x": "val2"},
    )
    assert result == {}


def test_extract_captures_converts_value_to_str():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"count": _ce("${{ result.count }}")},
        {"count": 42},
    )
    assert result == {"count": "42"}


def test_extract_captures_list_value_uses_json_dumps():
    """list payload value must be JSON-serialized, not Python repr."""
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"issue_urls": _ce("${{ result.issue_urls }}")},
        {
            "issue_urls": [
                "https://github.com/org/repo/issues/1",
                "https://github.com/org/repo/issues/2",
            ]
        },
    )
    assert result == {
        "issue_urls": '["https://github.com/org/repo/issues/1", "https://github.com/org/repo/issues/2"]'
    }
    assert "'" not in result["issue_urls"]


def test_extract_captures_dict_value_uses_json_dumps():
    """dict payload value must be JSON-serialized, not Python repr."""
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"meta": _ce("${{ result.meta }}")},
        {"meta": {"count": 3, "status": "ok"}},
    )
    assert json.loads(result["meta"]) == {"count": 3, "status": "ok"}


# ---------------------------------------------------------------------------
# Ingredient interpolation tests
# ---------------------------------------------------------------------------


def test_interpolate_campaign_refs_basic():
    from autoskillit.fleet._api import _interpolate_campaign_refs

    result = _interpolate_campaign_refs({"k": "${{ campaign.v }}"}, {"v": "resolved"})
    assert result == {"k": "resolved"}


def test_interpolate_unresolved_ref_raises_value_error():
    from autoskillit.fleet._api import _interpolate_campaign_refs

    with pytest.raises(ValueError, match="missing"):
        _interpolate_campaign_refs({"k": "${{ campaign.missing }}"}, {})


def test_interpolate_passthrough_non_campaign_values():
    from autoskillit.fleet._api import _interpolate_campaign_refs

    result = _interpolate_campaign_refs(
        {"a": "${{ inputs.x }}", "b": "plain"},
        {},
    )
    assert result == {"a": "${{ inputs.x }}", "b": "plain"}


def test_interpolate_multiple_refs_in_one_value():
    from autoskillit.fleet._api import _interpolate_campaign_refs

    result = _interpolate_campaign_refs(
        {"path": "${{ campaign.a }}/${{ campaign.b }}"},
        {"a": "foo", "b": "bar"},
    )
    assert result == {"path": "foo/bar"}


def test_extract_captures_bool_value_uses_json_dumps():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"flag": _ce("${{ result.flag }}")},
        {"flag": True},
    )
    assert result == {"flag": "true"}
    assert result["flag"] != "True"


def test_extract_captures_none_value_uses_json_dumps():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"val": _ce("${{ result.val }}")},
        {"val": None},
    )
    assert result == {"val": "null"}
    assert result["val"] != "None"


def test_extract_captures_float_value_uses_json_dumps():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"pi": _ce("${{ result.pi }}")},
        {"pi": 3.14},
    )
    assert result == {"pi": "3.14"}


def test_extract_captures_empty_spec_returns_empty():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures({}, {"anything": "value"})
    assert result == {}


def test_interpolate_campaign_refs_empty_ingredients():
    from autoskillit.fleet._api import _interpolate_campaign_refs

    result = _interpolate_campaign_refs({}, {"k": "v"})
    assert result == {}


# ---------------------------------------------------------------------------
# Research-campaign capture field tests
# ---------------------------------------------------------------------------


def test_extract_research_design_captures():
    from autoskillit.fleet._api import _extract_captures

    spec = {
        "worktree_path": _ce("${{ result.worktree_path }}"),
        "research_dir": _ce("${{ result.research_dir }}"),
        "experiment_plan": _ce("${{ result.experiment_plan }}"),
        "visualization_plan_path": _ce("${{ result.visualization_plan_path }}"),
    }
    payload = {
        "worktree_path": "/tmp/wt-123",
        "research_dir": "/tmp/wt-123/research",
        "experiment_plan": "plan.md",
        "visualization_plan_path": "/tmp/wt-123/viz.yaml",
    }
    result = _extract_captures(spec, payload)
    assert result == {
        "worktree_path": "/tmp/wt-123",
        "research_dir": "/tmp/wt-123/research",
        "experiment_plan": "plan.md",
        "visualization_plan_path": "/tmp/wt-123/viz.yaml",
    }


def test_extract_research_implement_capture():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"report_path": _ce("${{ result.report_path }}")},
        {"report_path": "/tmp/wt-123/report.md"},
    )
    assert result == {"report_path": "/tmp/wt-123/report.md"}


def test_extract_research_review_captures():
    from autoskillit.fleet._api import _extract_captures

    spec = {
        "pr_url": _ce("${{ result.pr_url }}"),
        "all_diagram_paths": _ce("${{ result.all_diagram_paths }}"),
        "report_path_after_finalize": _ce("${{ result.report_path_after_finalize }}"),
    }
    payload = {
        "pr_url": "https://github.com/org/repo/pull/42",
        "all_diagram_paths": "/tmp/diagrams/arch.png",
        "report_path_after_finalize": "/tmp/wt-123/report-final.md",
    }
    result = _extract_captures(spec, payload)
    assert result == {
        "pr_url": "https://github.com/org/repo/pull/42",
        "all_diagram_paths": "/tmp/diagrams/arch.png",
        "report_path_after_finalize": "/tmp/wt-123/report-final.md",
    }


def test_extract_research_review_all_diagram_paths_list():
    from autoskillit.fleet._api import _extract_captures

    result = _extract_captures(
        {"all_diagram_paths": _ce("${{ result.all_diagram_paths }}")},
        {"all_diagram_paths": ["arch.png", "flow.png"]},
    )
    assert result == {"all_diagram_paths": '["arch.png", "flow.png"]'}
    assert "'" not in result["all_diagram_paths"]


def test_extract_research_partial_payload_skips_missing():
    from autoskillit.fleet._api import _extract_captures

    spec = {
        "worktree_path": _ce("${{ result.worktree_path }}"),
        "research_dir": _ce("${{ result.research_dir }}"),
        "experiment_plan": _ce("${{ result.experiment_plan }}"),
        "visualization_plan_path": _ce("${{ result.visualization_plan_path }}"),
    }
    payload = {
        "worktree_path": "/tmp/wt-123",
        "experiment_plan": "plan.md",
    }
    result = _extract_captures(spec, payload)
    assert result == {
        "worktree_path": "/tmp/wt-123",
        "experiment_plan": "plan.md",
    }
    assert "research_dir" not in result
    assert "visualization_plan_path" not in result


def test_extract_research_capture_all_fields_combined():
    from autoskillit.fleet._api import _extract_captures

    spec = {
        "worktree_path": _ce("${{ result.worktree_path }}"),
        "research_dir": _ce("${{ result.research_dir }}"),
        "experiment_plan": _ce("${{ result.experiment_plan }}"),
        "visualization_plan_path": _ce("${{ result.visualization_plan_path }}"),
        "report_path": _ce("${{ result.report_path }}"),
        "pr_url": _ce("${{ result.pr_url }}"),
        "all_diagram_paths": _ce("${{ result.all_diagram_paths }}"),
    }
    payload = {
        "worktree_path": "/tmp/wt-123",
        "research_dir": "/tmp/wt-123/research",
        "experiment_plan": "plan.md",
        "visualization_plan_path": "/tmp/wt-123/viz.yaml",
        "report_path": "/tmp/wt-123/report.md",
        "pr_url": "https://github.com/org/repo/pull/42",
        "all_diagram_paths": ["a.png", "b.png"],
    }
    result = _extract_captures(spec, payload)
    assert len(result) == 7
    assert result["worktree_path"] == "/tmp/wt-123"
    assert result["all_diagram_paths"] == '["a.png", "b.png"]'


def test_relative_path_capture_and_reconstruction():
    """Repo-relative path values survive capture → interpolation
    and produce correct absolute paths when joined with worktree_path."""
    from autoskillit.fleet._api import _extract_captures, _interpolate_campaign_refs

    spec = {
        "worktree_path": _ce("${{ result.worktree_path }}"),
        "research_dir_rel": _ce("${{ result.research_dir_rel }}"),
    }
    payload = {
        "worktree_path": "/tmp/wt-A",
        "research_dir_rel": "research/2026-05-10-test",
        "research_dir": "/tmp/wt-A/research/2026-05-10-test",
    }
    captured = _extract_captures(spec, payload)
    assert captured == {
        "worktree_path": "/tmp/wt-A",
        "research_dir_rel": "research/2026-05-10-test",
    }

    # Simulate implement phase updating the anchor to a new worktree
    new_anchor = {**captured, "worktree_path": "/tmp/wt-B"}
    interpolated = _interpolate_campaign_refs(
        {"research_dir": "${{ campaign.worktree_path }}/${{ campaign.research_dir_rel }}"},
        new_anchor,
    )
    assert interpolated["research_dir"] == "/tmp/wt-B/research/2026-05-10-test"


def test_interpolate_campaign_worktree_and_report_path():
    from autoskillit.fleet._api import _interpolate_campaign_refs

    result = _interpolate_campaign_refs(
        {
            "wt": "${{ campaign.worktree_path }}",
            "rpt": "${{ campaign.report_path }}",
        },
        {"worktree_path": "/tmp/wt-123", "report_path": "/tmp/wt-123/report.md"},
    )
    assert result == {"wt": "/tmp/wt-123", "rpt": "/tmp/wt-123/report.md"}


def test_interpolate_campaign_unresolved_research_ref_raises():
    import pytest

    from autoskillit.fleet._api import _interpolate_campaign_refs

    with pytest.raises(ValueError, match="has not been captured"):
        _interpolate_campaign_refs(
            {"plan": "${{ campaign.experiment_plan }}"},
            {},
        )


# ---------------------------------------------------------------------------
# Integration path via execute_dispatch
# ---------------------------------------------------------------------------


def _make_recipe_info(name: str = "test-recipe"):
    from autoskillit.recipe.schema import RecipeInfo, RecipeSource

    return RecipeInfo(
        name=name,
        description="test",
        source=RecipeSource.PROJECT,
        path=Path(f"/fake/{name}.yaml"),
    )


def _simple_prompt_builder(**kwargs) -> str:
    return f"prompt-for-{kwargs.get('recipe', 'unknown')}"


async def _no_sleep_quota_checker(config, **kwargs) -> dict:
    return {
        "should_sleep": False,
        "sleep_seconds": 0,
        "utilization": None,
        "resets_at": None,
        "window_name": None,
    }


async def _noop_quota_refresher(config, **kwargs) -> None:
    pass


def _setup_dispatch(tool_ctx, recipe_name: str = "test-recipe", ingredients: dict | None = None):
    from autoskillit.fleet import FleetSemaphore
    from autoskillit.recipe.schema import Recipe, RecipeKind
    from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

    tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
    repo = InMemoryRecipeRepository()
    recipe_info = _make_recipe_info(recipe_name)
    repo.add_recipe(recipe_name, recipe_info)
    repo.add_full_recipe(
        recipe_info.path,
        Recipe(
            name=recipe_name,
            description="test",
            kind=RecipeKind.STANDARD,
            ingredients=ingredients or {},
        ),
    )
    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()


def _make_success_result(payload: dict):

    from autoskillit.core.types import SkillResult

    body = json.dumps(payload)
    sentinel_id_placeholder = "PLACEHOLDER"
    stdout = (
        f"%%L3_DONE::{sentinel_id_placeholder}%%\n---l2-result---\n{body}\n---end-l2-result---"
    )
    return SkillResult(
        success=True,
        result=stdout,
        session_id="sess-123",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason="none",
        stderr="",
        token_usage=None,
    )


def _read_state_file(tool_ctx) -> dict:
    state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    return json.loads(state_files[0].read_text())


@pytest.mark.anyio
async def test_dispatch_captures_extracted_and_written_to_state(tool_ctx, monkeypatch):
    """After a successful dispatch with capture spec, state file has captured_values."""

    from autoskillit.fleet._api import execute_dispatch

    _setup_dispatch(tool_ctx)

    payload = {"success": True, "reason": "", "out": "hello"}

    # The actual dispatch ID isn't known ahead of time; we patch parse_l3_result_block
    # to return a clean result with the payload.
    from autoskillit.fleet.result_parser import L3ParseResult

    monkeypatch.setattr(
        "autoskillit.fleet._api.parse_l3_result_block",
        lambda **kwargs: L3ParseResult(
            outcome="completed_clean",
            payload=payload,
            raw_body=None,
            parse_error=None,
            source="stdout",
        ),
    )

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="test-recipe",
        task="t",
        ingredients=None,
        dispatch_name=None,
        timeout_sec=None,
        capture={"out": "${{ result.out }}"},
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    result = json.loads(raw.to_envelope())
    assert result["success"] is True

    dispatch_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    assert len(dispatch_files) == 1, f"Expected 1 state file, found {len(dispatch_files)}"
    state_data = _read_state_file(tool_ctx)
    assert state_data.get("captured_values") == {"out": "hello"}


@pytest.mark.anyio
async def test_dispatch_ingredients_interpolated_from_captured_values(tool_ctx, monkeypatch):
    """Prior captured_values in state file are resolved into ingredients before dispatch."""

    from autoskillit.fleet._api import execute_dispatch
    from autoskillit.fleet.result_parser import L3ParseResult
    from autoskillit.fleet.state import DispatchRecord, write_captured_values, write_initial_state

    # Pre-create a state file for the same campaign_id with captured_values
    campaign_id = tool_ctx.kitchen_id
    dispatches_dir = tool_ctx.temp_dir / "dispatches"
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    prior_state_path = dispatches_dir / "prior.json"
    write_initial_state(
        prior_state_path,
        campaign_id=campaign_id,
        campaign_name="prior-dispatch",
        manifest_path="",
        dispatches=[DispatchRecord(name="prior-dispatch")],
    )
    from autoskillit.fleet.state import DispatchStatus, append_dispatch_record

    append_dispatch_record(
        prior_state_path,
        DispatchRecord(name="prior-dispatch", status=DispatchStatus.SUCCESS),
    )
    write_captured_values(prior_state_path, {"v": "injected"})

    _setup_dispatch(tool_ctx, ingredients={"x": ""})

    received_ingredients: list[dict] = []

    def _capturing_prompt_builder(**kwargs):
        received_ingredients.append(kwargs.get("ingredients", {}))
        return "prompt"

    monkeypatch.setattr(
        "autoskillit.fleet._api.parse_l3_result_block",
        lambda **kwargs: L3ParseResult(
            outcome="completed_clean",
            payload={"success": True, "reason": ""},
            raw_body=None,
            parse_error=None,
            source="stdout",
        ),
    )

    await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="test-recipe",
        task="t",
        ingredients={"x": "${{ campaign.v }}"},
        dispatch_name=None,
        timeout_sec=None,
        capture=None,
        prompt_builder=_capturing_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    assert received_ingredients, "prompt_builder was not called"
    assert len(received_ingredients) == 1
    assert received_ingredients[0].get("x") == "injected"


@pytest.mark.anyio
async def test_unresolved_campaign_ref_in_ingredients_returns_fleet_error(tool_ctx, monkeypatch):
    """Dispatch with ${{ campaign.missing }} and no prior captures returns fleet_error."""
    from autoskillit.fleet._api import execute_dispatch

    _setup_dispatch(tool_ctx, ingredients={"x": ""})

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="test-recipe",
        task="t",
        ingredients={"x": "${{ campaign.missing }}"},
        dispatch_name=None,
        timeout_sec=None,
        capture=None,
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )

    result = json.loads(raw.to_envelope())
    assert result["success"] is False
    assert "error" in result


def test_extract_captures_logs_warning_for_missing_spec_keys():
    """_extract_captures must log a WARNING for every capture spec key
    whose result field is absent from the payload, rather than silently skipping.
    """
    import structlog

    from autoskillit.fleet._api import _extract_captures

    capture_spec = {
        "pr_url": _ce("${{ result.pr_url }}"),
        "report_path": _ce("${{ result.report_path }}"),
    }
    payload: dict[str, object] = {"pr_url": "https://github.com/..."}  # report_path absent

    with structlog.testing.capture_logs() as logs:
        result = _extract_captures(capture_spec, payload)

    assert "pr_url" in result
    assert "report_path" not in result
    assert any(
        e.get("log_level") == "warning" and e.get("expected_field") == "report_path" for e in logs
    )


def test_extract_captures_raises_on_complete_capture_miss():
    """When ALL capture spec fields are absent from the payload,
    _extract_captures must raise CaptureCompletenessError rather than
    returning an empty dict that silently poisons the campaign state.
    """
    from autoskillit.fleet._api import CaptureCompletenessError, _extract_captures

    capture_spec = {
        "pr_url": _ce("${{ result.pr_url }}"),
        "report_path": _ce("${{ result.report_path }}"),
    }
    payload: dict[str, object] = {"success": True}  # No captured fields present at all

    with pytest.raises(CaptureCompletenessError):
        _extract_captures(capture_spec, payload)


# ---------------------------------------------------------------------------
# Round-trip contract tests
# ---------------------------------------------------------------------------


class TestCaptureFieldNameRoundTrip:
    """Verify the prompt builder and extractor agree on payload field names."""

    def test_prompt_example_fields_match_extractor_keys(self) -> None:
        """Prompt builder produces bare field names; extractor looks for bare names."""
        from autoskillit.core.types import CaptureEntrySpec
        from autoskillit.fleet._prompts import _build_food_truck_prompt

        capture_spec = {
            "worktree_path": CaptureEntrySpec(from_="${{ result.worktree_path }}"),
            "pr_url": CaptureEntrySpec(from_="${{ result.pr_url }}"),
        }

        prompt = _build_food_truck_prompt(
            recipe="test-recipe",
            task="implement feature X",
            ingredients={"branch": "main"},
            mcp_prefix="mcp__autoskillit__",
            dispatch_id="abc12345deadbeef",
            campaign_id="camp-001",
            l3_timeout_sec=3600,
            capture=capture_spec,
        )

        section8 = prompt[prompt.index("--- SECTION 8") :]

        for key, entry in capture_spec.items():
            field = resolve_payload_field(entry)
            assert field is not None
            assert f'"{field}"' in section8, f"Bare field name {field!r} not in Section 8"
            assert f"capture_{field}" not in section8, (
                f"Prefix form capture_{field!r} found in Section 8 — extractor uses bare names"
            )

    def test_synthetic_payload_round_trip(self) -> None:
        """Build synthetic L3 JSON using prompt field names; verify extractor finds all."""
        from autoskillit.core.types import CaptureEntrySpec
        from autoskillit.fleet._api import _extract_captures

        capture_spec = {
            "worktree_path": CaptureEntrySpec(from_="${{ result.worktree_path }}"),
            "pr_url": CaptureEntrySpec(from_="${{ result.pr_url }}"),
        }

        synthetic_payload = {
            "success": True,
            "reason": "completed",
            "summary": "done",
            "worktree_path": "/home/user/worktrees/impl-feature-20260512",
            "pr_url": "https://github.com/org/repo/pull/42",
        }

        result = _extract_captures(capture_spec, synthetic_payload)
        assert result["worktree_path"] == "/home/user/worktrees/impl-feature-20260512"
        assert result["pr_url"] == "https://github.com/org/repo/pull/42"

    def test_hyphenated_field_name_round_trip(self) -> None:
        """Field names with hyphens are handled correctly end-to-end."""
        from autoskillit.core.types import CaptureEntrySpec
        from autoskillit.fleet._api import _extract_captures
        from autoskillit.fleet._prompts import _build_food_truck_prompt

        capture_spec = {
            "worktree-path": CaptureEntrySpec(from_="${{ result.worktree-path }}"),
        }

        prompt = _build_food_truck_prompt(
            recipe="test-recipe",
            task="implement feature X",
            ingredients={"branch": "main"},
            mcp_prefix="mcp__autoskillit__",
            dispatch_id="abc12345deadbeef",
            campaign_id="camp-001",
            l3_timeout_sec=3600,
            capture=capture_spec,
        )

        section8 = prompt[prompt.index("--- SECTION 8") :]
        assert '"worktree-path"' in section8

        synthetic_payload = {
            "success": True,
            "reason": "completed",
            "summary": "done",
            "worktree-path": "/home/user/worktree-path",
        }

        result = _extract_captures(capture_spec, synthetic_payload)
        assert result["worktree-path"] == "/home/user/worktree-path"


# ---------------------------------------------------------------------------
# Typed capture contract tests (Step 1a–1d, 1g)
# ---------------------------------------------------------------------------


def test_extract_path_type_rejects_empty_string():
    """A path-type capture with an empty-string value must raise CaptureValueTypeError."""
    from autoskillit.fleet._api import CaptureValueTypeError, _extract_captures

    capture_spec = {
        "p": CaptureEntrySpec(from_="${{ result.p }}", value_type="path"),
    }
    payload = {"p": ""}

    with pytest.raises(CaptureValueTypeError) as exc_info:
        _extract_captures(capture_spec, payload)
    assert "path" in str(exc_info.value).lower()
    assert "p" in str(exc_info.value)


def test_extract_url_type_rejects_empty_string():
    """A url-type capture with an empty-string value must raise CaptureValueTypeError."""
    from autoskillit.fleet._api import CaptureValueTypeError, _extract_captures

    capture_spec = {
        "u": CaptureEntrySpec(from_="${{ result.u }}", value_type="url"),
    }
    payload = {"u": ""}

    with pytest.raises(CaptureValueTypeError) as exc_info:
        _extract_captures(capture_spec, payload)
    assert exc_info.value.declared_type == "url"
    assert "non-empty" in str(exc_info.value)


def test_extract_optional_string_type_accepts_empty():
    """An optional_string-type capture with an empty-string value must be accepted."""
    from autoskillit.fleet._api import _extract_captures

    capture_spec = {
        "pr_url": CaptureEntrySpec(from_="${{ result.pr_url }}", value_type="optional_string"),
    }
    payload = {"pr_url": ""}

    result = _extract_captures(capture_spec, payload)
    assert result == {"pr_url": ""}


def test_extract_path_type_rejects_nonexistent_path(tmp_path: Path):
    """A path-type capture with a non-existent path must raise CaptureValueTypeError."""
    from autoskillit.fleet._api import CaptureValueTypeError, _extract_captures

    capture_spec = {
        "p": CaptureEntrySpec(from_="${{ result.p }}", value_type="path"),
    }
    payload = {"p": str(tmp_path / "nonexistent")}

    with pytest.raises(CaptureValueTypeError) as exc_info:
        _extract_captures(capture_spec, payload)
    msg = str(exc_info.value).lower()
    assert "path" in msg or "exist" in msg


def test_interpolate_rejects_empty_string_campaign_ref():
    """_interpolate_campaign_refs must reject an empty-string captured value."""
    from autoskillit.fleet._api import _interpolate_campaign_refs

    ingredients = {"report_path": "${{ campaign.report_path }}"}
    captured = {"report_path": ""}

    with pytest.raises(ValueError) as exc_info:
        _interpolate_campaign_refs(ingredients, captured)
    msg = str(exc_info.value).lower()
    assert "empty" in msg
    assert "report_path" in msg
